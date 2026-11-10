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
    def test_supported_laptop_checks_actual_stream(self):
        value = decoder.select_route('auto', LAPTOP)
        self.assertTrue(value['enabled'])
        self.assertEqual(value['policy'], 'fls_p3')

    def test_verified_partner_host_uses_no_fls_route(self):
        self.assertTrue(decoder.select_route('auto', DESKTOP)['enabled'])

    def test_uninspected_abi_does_not_enable_automatically(self):
        for host in (dict(DESKTOP, build='different'), dict(DESKTOP, architecture='arm64')):
            self.assertFalse(decoder.select_route('auto', host)['enabled'])

    def test_model_name_does_not_decide_stream_compatibility(self):
        value = decoder.select_route('auto', dict(LAPTOP, model='iMac20,1'))
        self.assertTrue(value['enabled'])
        self.assertEqual(value['policy'], 'fls_p3')

    def test_explicit_route_preserves_unconditional_h264_policy(self):
        self.assertEqual(decoder.select_route('no_fls', LAPTOP)['policy'], 'all_h264')

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
        instance = decoder.DecoderRoute(self.root, self.directory, host=LAPTOP, mode='normal')
        with patch.object(route, 'acquire') as acquire, patch.object(route, 'ctl') as command, \
             patch.object(route, 'DecoderJob') as job:
            state = instance.start()
            instance.poll()
            result = instance.close()
        acquire.assert_not_called(); command.assert_not_called(); job.assert_not_called()
        self.assertTrue(state['ready'])
        self.assertTrue(result['cleanup_verified'])
        self.assertIsNone(result['original_avc_restored'])

    def simulated_controller(self, *, mode='auto', failure=None):
        instance = decoder.DecoderRoute(self.root, self.directory, host=DESKTOP,
                                       mode=mode, stop=threading.Event())
        fake_holder = []
        class Job(route.DecoderJob):
            def __init__(self, stage):
                fake = FakeCtl(stage)
                fake.fail = failure
                old_owned = fake.owned
                fake.owned = lambda: dict(old_owned(), pid=os.getpid())
                super().__init__(stage, fake)
                fake_holder.append(fake)
            def prepare(self, library, *, policy='all_h264'):
                if policy != ('fls_p3' if mode == 'auto' else 'all_h264'):
                    raise AssertionError('Automatic policy was not passed to AVConference')
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

    def test_auto_bootout_failure_continues_normal_after_verified_restore(self):
        instance, calls = self.simulated_controller(failure='bootout')
        state = instance.start()
        self.assertEqual(state['requested'], 'auto')
        self.assertEqual(state['mode'], 'normal')
        self.assertTrue(state['ready'])
        self.assertFalse(state['enabled'])
        self.assertTrue(state['decoder_fallback']['original_avc_restored'])
        self.assertEqual(state['decoder_fallback']['launchctl_exit'], 5)
        saved = decoder.read_json(self.directory / 'decoder-route.json')
        self.assertEqual(saved['decoder_fallback'], state['decoder_fallback'])
        self.assertIsNone(instance.lock)
        self.assertFalse(route.JOURNAL.exists())
        self.assertEqual(sum(call[0] == 'bootout' for call in calls[0].calls), 1)
        self.assertFalse(any(call[0] == 'bootstrap' for call in calls[0].calls))
        before = len(calls[0].calls)
        instance.poll()
        self.assertTrue(instance.close()['cleanup_verified'])
        self.assertEqual(len(calls[0].calls), before)

    def test_auto_nonzero_bootout_that_removed_job_restores_before_fallback(self):
        instance, calls = self.simulated_controller(failure='bootout_after_removal')
        state = instance.start()
        self.assertEqual(state['mode'], 'normal')
        self.assertTrue(state['decoder_fallback']['original_avc_restored'])
        bootstraps = [call for call in calls[0].calls if call[0] == 'bootstrap']
        self.assertEqual(len(bootstraps), 1)
        self.assertEqual(bootstraps[0][-1], str(route.ORIGINAL))
        instance.close()

    def test_auto_fallback_refuses_foreign_service_after_rejected_bootout(self):
        instance, calls = self.simulated_controller(failure='bootout_foreign')
        with self.assertRaisesRegex(RuntimeError, 'requires verified'):
            instance.start()
        self.assertFalse(instance.ready)
        self.assertTrue(route.JOURNAL.exists())
        self.assertEqual(calls[0].current['path'], '/foreign/job.plist')
        self.assertFalse(any(call[0] == 'bootstrap' for call in calls[0].calls))

    def test_explicit_hook_request_never_falls_back(self):
        instance, _ = self.simulated_controller(mode='no_fls', failure='bootout')
        try:
            with self.assertRaises(route.DecoderUnloadError):
                instance.start()
            self.assertTrue(instance.decision['enabled'])
            self.assertFalse(instance.ready)
            self.assertNotIn('decoder_fallback', instance.metadata)
        finally:
            instance.close()

    def test_other_startup_errors_are_not_hidden_by_fallback(self):
        instance, _ = self.simulated_controller(failure='bootstrap')
        try:
            with self.assertRaisesRegex(RuntimeError, 'registration failed'):
                instance.start()
            self.assertNotIn('decoder_fallback', instance.metadata)
            self.assertFalse(instance.ready)
        finally:
            instance.close()

    def test_stop_during_restoration_prevents_fallback_readiness(self):
        instance, _ = self.simulated_controller(failure='bootout')
        close = instance.close
        def stop_and_restore():
            result = close()
            instance.stop.set()
            return result
        with patch.object(instance, 'close', side_effect=stop_and_restore):
            with self.assertRaisesRegex(RuntimeError, 'requires verified'):
                instance.start()
        self.assertFalse(instance.ready)
        self.assertFalse(route.JOURNAL.exists())

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

    def test_idle_and_reloaded_decoder_keep_registration_and_bridge_session(self):
        instance, calls = self.simulated_controller()
        try:
            instance.start()
            before = len(calls[0].calls)
            calls[0].current.pop('pid')
            calls[0].current['state'] = 'not running'
            instance.poll()
            self.assertTrue(instance.ready)
            self.assertIsNone(instance.pid)
            self.assertEqual(instance.observation, 'idle')
            pid = os.getpid() + 100000
            birth = (pid, os.getuid(), 2000, 1)
            calls[0].current.update(pid=pid, state='running')
            with patch('decoder.process_birth', return_value=birth):
                instance.poll()
                self.assertFalse(instance.ready)
                self.assertEqual(instance.observation, 'starting')
                with (instance.stage / 'stderr.log').open('a') as log:
                    log.write(f'MIRRA_DECODER_HOOK_READY pid={pid} version=1\n')
                instance.poll()
                self.assertTrue(instance.ready)
                self.assertEqual(instance.pid, pid)
            self.assertTrue(all(call[0] == 'print' for call in calls[0].calls[before:]))
        finally:
            instance.close()

    def test_observation_failure_defers_without_unloading_support(self):
        instance, calls = self.simulated_controller()
        try:
            instance.start()
            before = len(calls[0].calls)
            calls[0].fail = 'query'
            instance.poll()
            self.assertFalse(instance.ready)
            self.assertEqual(instance.observation, 'unknown')
            calls[0].fail = None
            instance.poll()
            self.assertTrue(instance.ready)
            self.assertTrue(all(call[0] == 'print' for call in calls[0].calls[before:]))
        finally:
            calls[0].fail = None
            instance.close()

    def test_reused_decoder_pid_waits_for_fresh_hook_marker(self):
        instance, _ = self.simulated_controller()
        try:
            instance.start()
            old = instance.ready_pids[os.getpid()]
            with patch('decoder.process_birth', return_value=(*old[:3], old[3] + 1)):
                instance.poll()
                self.assertFalse(instance.ready)
                self.assertEqual(instance.observation, 'starting')
            instance.poll()
            self.assertTrue(instance.ready)
        finally:
            instance.close()

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
