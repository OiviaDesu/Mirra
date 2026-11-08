"""Host selection, actual log parsing, and production route lifecycle checks."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import decoder
import decoder_route as route
from configuration import ConfigError, load_config
from processes import process_birth
from test_decoder_restore import FakeCtl

LAPTOP = {'architecture': 'x86_64', 'build': '25G332', 'model': 'MacBookPro16,2'}
DESKTOP = dict(LAPTOP, model='MacPro7,1')


class ProfileTests(unittest.TestCase):
    def test_verified_laptop_keeps_normal_decoder(self):
        value = decoder.select_route('auto', LAPTOP)
        self.assertFalse(value['enabled'])
        self.assertEqual(value['mode'], 'normal')

    def test_verified_partner_host_uses_no_fls_route(self):
        self.assertTrue(decoder.select_route('auto', DESKTOP)['enabled'])

    def test_unverified_model_or_os_does_not_enable_automatically(self):
        for host in (dict(DESKTOP, build='different'), dict(DESKTOP, architecture='arm64'),
                     dict(DESKTOP, model='iMac20,1')):
            self.assertFalse(decoder.select_route('auto', host)['enabled'])

    def test_explicit_normal_route_overrides_automatic_selection(self):
        self.assertFalse(decoder.select_route('normal', DESKTOP)['enabled'])

    def test_explicit_route_rejects_uninspected_abi(self):
        with self.assertRaises(RuntimeError):
            decoder.select_route('no_fls', dict(LAPTOP, build='uninspected'))

    def test_config_preserves_explicit_route_and_roothide(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'config.json'
            path.write_text('{}')
            value = replace(load_config(path), decoder_route='no_fls', path_mode='roothide')
            path.write_text(json.dumps(value.as_dict()))
            self.assertEqual(load_config(path), value)
            path.write_text('{"host":{"decoder_route":"FLS"}}')
            with self.assertRaises(ConfigError):
                load_config(path)


class RouteLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.directory = self.root / 'run'; self.directory.mkdir(mode=0o700)
        self.stages = self.root / 'stages'; self.stages.mkdir(mode=0o700)
        self.cache = self.root / 'cache'; self.cache.mkdir(mode=0o700)
        event = patch('decoder.emit_event'); self.events = event.start(); self.addCleanup(event.stop)
        for target, value in (('decoder.Path.home', self.root), ('decoder_route.STAGES', self.stages),
                              ('decoder_route.CACHE', self.cache), ('decoder_route.JOURNAL', self.cache / 'decoder-route.json')):
            p = patch(target, return_value=value) if target.endswith('.home') else patch(target, value)
            p.start(); self.addCleanup(p.stop)

    def test_normal_start_and_close_do_not_inspect_or_mutate_avconference(self):
        instance = decoder.DecoderRoute(self.root, self.directory, host=LAPTOP)
        with patch.object(route, 'acquire') as acquire, patch.object(route, 'ctl') as command, \
             patch.object(route, 'DecoderJob') as job:
            state = instance.start()
            instance.poll()
            result = instance.close()
        acquire.assert_not_called(); command.assert_not_called(); job.assert_not_called()
        self.assertTrue(state['ready'])
        self.assertTrue(result['cleanup_verified'])
        self.assertIsNone(result['original_avc_restored'])

    def simulated_controller(self):
        instance = decoder.DecoderRoute(self.root, self.directory, host=DESKTOP, stop=threading.Event())
        fake_holder = []
        class Job(route.DecoderJob):
            def __init__(self, stage):
                fake = FakeCtl(stage)
                old_owned = fake.owned
                fake.owned = lambda: dict(old_owned(), pid=os.getpid())
                super().__init__(stage, fake)
                fake_holder.append(fake)
            def prepare(self, library):
                for name in ('stdout.log', 'stderr.log'):
                    (self.stage / name).touch(mode=0o600)
                return {'decoder_hook_sha256': 'simulated fixture'}
            def start(self):
                super().start()
                with (self.stage / 'stderr.log').open('a') as log:
                    log.write(f'MIRRA_DECODER_HOOK_READY pid={os.getpid()} version=1\n')
        class Guardian:
            exit_code = None
            def poll(self):
                return self.exit_code
            def wait(self, timeout):
                state = instance.job.restore()
                route.write_json(instance.stage / 'restored.json', {'original_avc_restored': True, 'state': state})
                route.clear_journal(instance.stage)
                self.exit_code = 0
                return 0
        def spawn():
            instance.guardian = Guardian()
            route.write_json(instance.stage / 'guardian-ready.json', {'guardian_birth': process_birth(os.getpid())})
        instance.spawn_guardian = spawn
        p = patch.object(route, 'DecoderJob', Job); p.start(); self.addCleanup(p.stop)
        return instance, fake_holder

    def test_production_session_has_no_test_deadline_and_restores_on_stop(self):
        instance, calls = self.simulated_controller()
        try:
            state = instance.start()
            owner = decoder.read_json(instance.stage / 'owner.json')
            self.assertTrue(state['ready'])
            self.assertIsNone(owner['deadline_monotonic'])
            self.assertNotIn('gui_birth', owner)
            instance.poll()
            result = instance.close()
            self.assertTrue(result['original_avc_restored'])
            self.assertTrue(result['cleanup_verified'])
            self.assertFalse(route.JOURNAL.exists())
            self.assertTrue(any(call[0] == 'bootout' for call in calls[0].calls))
        finally:
            if instance.lock is not None: instance.close()

    def test_real_markers_are_parsed_and_non_decoder_messages_are_excluded(self):
        instance, _ = self.simulated_controller()
        try:
            instance.start()
            with (instance.stage / 'stderr.log').open('a') as log:
                log.write('ordinary unrelated message\n')
                log.write('MIRRA_DECODER_ROUTE create=1 removed=1 had_fls=1 colour_primaries=12 status=0 using_hardware=1\n')
            instance.poll()
            value = decoder.read_json(self.directory / 'decoder-route.json')
            self.assertEqual(value['native_event_count'], 2)
            self.assertEqual(value['native_decoder_events'][-1]['colour_primaries'], 12)
            self.assertEqual(value['native_decoder_events'][-1]['status'], 0)
            self.assertNotIn('ordinary unrelated', json.dumps(value))
        finally:
            instance.close()

    def test_guardian_death_is_a_failure_and_supervisor_restores(self):
        instance, _ = self.simulated_controller()
        try:
            instance.start()
            instance.guardian.exit_code = 1
            with self.assertRaisesRegex(RuntimeError, 'guardian ended'):
                instance.poll()
            result = instance.close()
            self.assertTrue(result['original_avc_restored'])
            self.assertTrue(result['recovered_by_supervisor'])
        finally:
            if instance.lock is not None: instance.close()

    def test_foreign_avconference_is_preserved_and_cleanup_failure_is_reported(self):
        instance, calls = self.simulated_controller()
        try:
            instance.start()
            calls[0].current = {'path': '/foreign/job.plist', 'program': '/foreign/program'}
            before = len(calls[0].calls)
            with self.assertRaises(RuntimeError):
                instance.poll()
            instance.guardian.exit_code = 1
            result = instance.close()
            self.assertFalse(result['cleanup_verified'])
            self.assertFalse(result['original_avc_restored'])
            self.assertTrue(all(call[0] == 'print' for call in calls[0].calls[before:]))
        finally:
            if instance.lock is not None: os.close(instance.lock); instance.lock = None


if __name__ == '__main__':
    unittest.main()
