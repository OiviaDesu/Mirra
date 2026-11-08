import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from configuration import load_config
from supervisor import NativeBackend, recovery_gate


class RecoveryGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.original = subprocess.CompletedProcess([], 0,
            'path = /System/Library/LaunchAgents/com.apple.sharingd.plist\n'
            'program = /usr/libexec/sharingd\n', '')

    def test_live_previous_child_prevents_retry(self):
        birth = [123, os.getuid(), 50, 6]
        (self.root / 'backend-children.json').write_text(json.dumps({'broker': birth}))
        with patch('supervisor.process_birth', return_value=tuple(birth)), \
                patch('supervisor.subprocess.run') as command:
            self.assertFalse(recovery_gate(self.root)[0])
            command.assert_not_called()

    def test_pid_reuse_does_not_block_restored_session(self):
        (self.root / 'backend-children.json').write_text(json.dumps({'broker': [123, os.getuid(), 50, 6]}))
        with patch('supervisor.process_birth', return_value=(123, os.getuid(), 70, 8)), \
                patch('supervisor.Path.home', return_value=self.root), \
                patch('supervisor.subprocess.run', return_value=self.original):
            self.assertEqual(recovery_gate(self.root), (True, None))

    def test_uncertain_child_observation_does_not_authorize_restart(self):
        (self.root / 'backend-children.json').write_text(json.dumps({'broker': [123, os.getuid(), 50, 6]}))
        with patch('supervisor.process_birth', side_effect=OSError('observation failed')):
            with self.assertRaises(OSError):
                recovery_gate(self.root)

    def test_custom_or_unobservable_sharingd_defers_retry(self):
        states = [subprocess.CompletedProcess([], 1, '', 'Could not find service'),
                  subprocess.CompletedProcess([], 0, self.original.stdout + 'DYLD_INSERT_LIBRARIES = other', ''),
                  subprocess.CompletedProcess([], 0, self.original.stdout.replace('/System/Library/LaunchAgents/', '/tmp/'), '')]
        for state in states:
            with self.subTest(state=state), patch('supervisor.Path.home', return_value=self.root), \
                    patch('supervisor.subprocess.run', return_value=state):
                self.assertFalse(recovery_gate()[0])


class BackendWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'scripts').mkdir()
        self.run = self.root / 'native-test'
        self.run.mkdir()
        (self.run / 'keystore.dylib').write_bytes(b'test fixture only')
        (self.root / 'config.json').write_text('{}')
        self.config = load_config(self.root / 'config.json', root=self.root)

    def wait_for(self, backend, predicate, seconds=7):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            state = backend.snapshot()
            if predicate(state):
                return state
            time.sleep(.02)
        self.fail('Backend did not reach expected state: ' + repr(backend.snapshot()))

    def test_terminal_worker_retries_and_new_worker_can_stop(self):
        # A real pipe/process exercises readiness delivery and EOF, rather than
        # mocking Popen.poll to mirror the supervisor state machine.
        (self.root / 'scripts/backend.py').write_text('''
import json, os, pathlib, signal, sys, time
config = pathlib.Path(sys.argv[sys.argv.index('--config') + 1])
attempt = int(config.parent.name.split('-')[-1])
print(json.dumps({'NATIVE_WORKSPACE_READY': True, 'controller_pid': os.getpid()}), flush=True)
if attempt == 1:
    time.sleep(.1)
    sys.exit(7)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    time.sleep(.1)
''')
        with patch('supervisor.ROOT', self.root), \
                patch('supervisor.recovery_gate', return_value=(True, None)), \
                patch('supervisor.SharingdAgent.recover_abandoned', return_value=None), \
                patch('supervisor.emit_event'):
            backend = NativeBackend(self.config, self.run, recover=True).start()
            try:
                ready = self.wait_for(backend, lambda s: s['attempt'] == 2 and s['ready'])
                self.assertIsNone(backend.process.poll())
                persisted = json.loads((self.run / 'backend-status.json').read_text())
                self.assertEqual(persisted['worker_pid'], ready['worker_pid'])
                self.assertTrue((self.run / 'backend-000001/backend.log').read_text().strip())
            finally:
                report = backend.close()
            self.assertTrue(report['cleanup_verified'])
            self.assertIsNone(report['cleanup_error'])
            self.assertEqual(report['exit_code'], 0)

    def test_failed_observation_waits_without_starting_a_worker(self):
        with patch('supervisor.recovery_gate', side_effect=subprocess.TimeoutExpired('launchctl', 5)), \
                patch('supervisor.SharingdAgent.recover_abandoned', return_value=None), \
                patch('supervisor.subprocess.Popen') as spawn, patch('supervisor.emit_event'):
            backend = NativeBackend(self.config, self.run, recover=True).start()
            try:
                self.wait_for(backend, lambda s: s['phase'] == 'waiting_for_cleanup')
                spawn.assert_not_called()
            finally:
                backend.close()


if __name__ == '__main__':
    unittest.main()
