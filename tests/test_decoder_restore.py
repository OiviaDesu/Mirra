"""Failure and lifetime tests using simulated jobs, never Apple's service."""
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import decoder_route as route
from processes import process_birth


def state_text(value):
    lines = ['gui/test = {']
    for name in ('path', 'program', 'pid', 'state', 'active count'):
        if name in value:
            lines.append('\t' + name + ' = ' + str(value[name]))
    if value.get('injected'):
        lines += ['\t environment = {', '\t\tDYLD_INSERT_LIBRARIES => test', '\t }']
    lines += ['\tendpoints = {', '\t\t"com.apple.videoconference.avconference" = {',
              '\t\t\tactive = ' + str(value.get('endpoint', 0)), '\t\t}', '\t}', '}']
    return '\n'.join(lines)


class FakeCtl:
    def __init__(self, stage):
        self.stage = Path(stage)
        self.current = self.original()
        self.calls = []
        self.fail = None
        self.removal_polls = 0
        self.bootstrap_original_running = True

    @staticmethod
    def original():
        return {'path': str(route.ORIGINAL), 'program': route.PROGRAM,
                'pid': 123, 'state': 'running', 'endpoint': 0}

    def owned(self):
        return {'path': str(self.stage / 'job.plist'), 'program': str(self.stage / 'wrapper'),
                'pid': 456, 'state': 'running', 'injected': True, 'endpoint': 0}

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == 'print':
            if self.fail == 'query':
                return subprocess.CompletedProcess(args, 5, '', 'Observation denied')
            if self.removal_polls:
                self.removal_polls -= 1
                if not self.removal_polls:
                    self.current = None
            if self.current is None:
                return subprocess.CompletedProcess(args, 113, '', 'Could not find service')
            return subprocess.CompletedProcess(args, 0, state_text(self.current), '')
        if args[0] == 'bootout':
            if not (self.stage / 'armed.json').exists() and self.current and self.current.get('path') == str(route.ORIGINAL):
                raise AssertionError('Job changed before restoration was armed')
            if self.fail in ('bootout', 'bootout_after_removal', 'bootout_foreign'):
                if self.fail == 'bootout_after_removal':
                    self.current = None
                elif self.fail == 'bootout_foreign':
                    self.current = {'path': '/foreign/job.plist', 'program': '/foreign/program'}
                return subprocess.CompletedProcess(args, 5, '', 'Boot-out failed: fixture rejection')
            if self.fail == 'delayed':
                self.removal_polls = 3
            else:
                self.current = None
            if self.fail == 'timeout':
                self.fail = None
                raise subprocess.TimeoutExpired(args, 12)
        if args[0] == 'bootstrap':
            if args[-1] == str(route.ORIGINAL):
                self.current = self.original()
                if not self.bootstrap_original_running:
                    self.current.pop('pid', None)
                    self.current['state'] = 'not running'
            elif self.fail == 'bootstrap':
                return subprocess.CompletedProcess(args, 1, '', 'Failed')
            else:
                self.current = self.owned()
        if args[0] == 'kickstart':
            if self.fail == 'kickstart':
                return subprocess.CompletedProcess(args, 1, '', 'Failed')
            self.current = self.original()
        return subprocess.CompletedProcess(args, 0, '', '')


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.stage = Path(self.temporary.name)
        self.fake = FakeCtl(self.stage)
        self.job = route.DecoderJob(self.stage, self.fake)

    def tearDown(self):
        self.temporary.cleanup()

    def test_normal_round_trip(self):
        self.job.start()
        self.assertTrue(self.job.owned(self.job.state()))
        self.assertTrue(self.job.original(self.job.restore()))

    def test_previously_running_original_is_resumed_without_killing_it(self):
        self.fake.bootstrap_original_running = False
        self.job.start()
        restored = self.job.restore()
        self.assertEqual(restored['state'], 'running')
        self.assertEqual(sum(call[0] == 'kickstart' for call in self.fake.calls), 1)
        self.assertTrue(all('-k' not in call for call in self.fake.calls))

    def test_previously_idle_original_stays_idle(self):
        self.fake.current.pop('pid')
        self.fake.current['state'] = 'not running'
        self.fake.bootstrap_original_running = False
        self.job.start()
        restored = self.job.restore()
        self.assertNotIn('pid', restored)
        self.assertFalse(any(call[0] == 'kickstart' for call in self.fake.calls))

    def test_failed_original_resume_is_reported(self):
        self.fake.bootstrap_original_running = False
        self.job.start()
        self.fake.fail = 'kickstart'
        with self.assertRaisesRegex(RuntimeError, 'previous running state'):
            self.job.restore()

    def test_bootout_failure_preserves_original(self):
        self.fake.fail = 'bootout'
        with self.assertRaises(route.DecoderUnloadError) as caught:
            self.job.start()
        self.assertEqual(caught.exception.returncode, 5)
        self.assertEqual(caught.exception.stderr, 'Boot-out failed: fixture rejection')
        self.assertTrue(self.job.original(self.job.restore()))
        self.assertFalse(any(call[0] == 'bootstrap' for call in self.fake.calls))

    def test_bootout_timeout_after_mutation_restores(self):
        self.fake.fail = 'timeout'
        with self.assertRaises(subprocess.TimeoutExpired):
            self.job.start()
        self.assertTrue(self.job.original(self.job.restore()))
        self.assertEqual(sum(call[0] == 'bootout' for call in self.fake.calls), 1)

    def test_bootstrap_failure_restores(self):
        self.fake.fail = 'bootstrap'
        with self.assertRaises(RuntimeError):
            self.job.start()
        self.assertTrue(self.job.original(self.job.restore()))

    def test_waits_for_asynchronous_bootout(self):
        self.fake.fail = 'delayed'
        self.job.start()
        self.assertTrue(self.job.original(self.job.restore()))

    def test_foreign_configuration_is_preserved(self):
        self.fake.current = {'path': '/another/job.plist', 'program': '/another/program'}
        with self.assertRaises(RuntimeError):
            self.job.restore()
        self.assertTrue(all(call[0] == 'print' for call in self.fake.calls))

    def test_observation_failure_does_not_mutate(self):
        self.fake.fail = 'query'
        with self.assertRaises(RuntimeError):
            self.job.restore()
        self.assertTrue(all(call[0] == 'print' for call in self.fake.calls))

    def test_active_conference_does_not_mutate(self):
        self.fake.current['endpoint'] = 1
        with self.assertRaises(RuntimeError):
            self.job.start()
        self.assertFalse((self.stage / 'armed.json').exists())
        self.assertTrue(all(call[0] == 'print' for call in self.fake.calls))

    def test_existing_hook_does_not_mutate(self):
        self.fake.current['injected'] = True
        with self.assertRaises(RuntimeError):
            self.job.start()
        self.assertTrue(all(call[0] == 'print' for call in self.fake.calls))

    def test_process_observation_error_is_not_death(self):
        with patch.object(route, 'process_birth', side_effect=OSError('unavailable')):
            self.assertTrue(route.owner_alive((123, os.getuid(), 1, 1)))

    def test_reused_pid_is_not_owner(self):
        with patch.object(route, 'process_birth', return_value=(123, os.getuid(), 2, 1)):
            self.assertFalse(route.owner_alive((123, os.getuid(), 1, 1)))

    def test_staging_keeps_apple_options(self):
        original = self.stage / 'original.plist'
        import plistlib
        original.write_bytes(plistlib.dumps({'Label': route.LABEL, 'ProgramArguments':[route.PROGRAM, '--option'],
            'MachServices': {'private.test.endpoint': True}, 'EnvironmentVariables': {'EXISTING':'kept'}}))
        library = self.stage / 'input.dylib'
        library.write_bytes(b'local fixture')
        with patch.object(route, 'ORIGINAL', original), patch.object(route.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0, b'', b'')):
            self.job.prepare(library)
        definition = plistlib.loads((self.stage / 'job.plist').read_bytes())
        self.assertEqual(definition['MachServices'], {'private.test.endpoint': True})
        self.assertEqual(definition['EnvironmentVariables'],
                         {'EXISTING':'kept', 'MIRRA_NO_FLS':'1', 'MIRRA_DECODER_POLICY':'all_h264'})
        self.assertIn('--option', (self.stage / 'wrapper').read_text())


class GuardianTests(unittest.TestCase):
    def test_guardian_restores_after_owner_killed_and_keeps_lock(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            base = Path(temporary)
            cache = base / 'cache'; cache.mkdir(mode=0o700)
            stages = base / 'stages'; stages.mkdir(mode=0o700)
            stage = stages / 'mirra-decoder-20261108-151300-guardtest'; stage.mkdir(mode=0o700)
            owner = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])
            descriptor = os.open(cache / 'decoder-route.lock', os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fake = FakeCtl(stage); fake.current = fake.owned()
            route.write_json(stage / 'armed.json', {'armed':True})
            route.write_json(stage / 'owner.json', {'owner_birth':process_birth(owner.pid),
                'deadline_monotonic':None})
            original_class = route.DecoderJob
            def fake_job(path):
                return original_class(path, fake)
            def guarded_child():
                raise SystemExit(route.guardian(stage, descriptor))
            context = multiprocessing.get_context('fork')
            child = context.Process(target=guarded_child)
            with patch.object(route, 'STAGES', stages), patch.object(route, 'CACHE', cache), \
                 patch.object(route, 'JOURNAL', cache/'decoder-route.json'), patch.object(route, 'DecoderJob', fake_job):
                child.start()
            os.close(descriptor)
            try:
                deadline = time.monotonic()+3
                while not (stage/'guardian-ready.json').exists() and time.monotonic()<deadline:
                    time.sleep(.05)
                self.assertTrue((stage/'guardian-ready.json').exists())
                attempted = os.open(cache/'decoder-route.lock', os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(attempted, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(attempted)
                owner.kill(); owner.wait(timeout=2)
                child.join(timeout=5)
                self.assertEqual(child.exitcode, 0)
                result = json.loads((stage/'restored.json').read_text())
                self.assertTrue(result['original_avc_restored'])
                self.assertEqual(result['state']['program'], route.PROGRAM)
                attempted = os.open(cache/'decoder-route.lock', os.O_RDWR)
                try:
                    fcntl.flock(attempted, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(attempted)
            finally:
                if owner.poll() is None: owner.kill(); owner.wait()
                if child.is_alive(): child.terminate(); child.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
