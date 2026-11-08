import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import launcher as module


class NativeLaunchAgentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'build').mkdir()
        (self.root / 'build/launch.dylib').write_bytes(b'test artifact')
        self.original = self.root / 'original.plist'
        self.original.write_bytes(plistlib.dumps({'Program': module.AGENT, 'Label': 'com.apple.coreservices.uiagent'}))
        self.run = self.root / 'runtime/native-test'
        self.run.mkdir(parents=True)
        for patcher in (patch.object(module, 'ROOT', self.root), patch.object(module, 'ORIGINAL', self.original),
                        patch.object(Path, 'home', return_value=self.root)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.job = self.original
        self.calls = []
        self.fail_probe_bootstrap = False
        self.agent = module.NativeLaunchAgent(self.run, {'MIRRA_LAUNCH_PROBE': 'test'})
        self.agent.command = self.launchctl

    def launchctl(self, *args):
        self.calls.append(args)
        if args[0] == 'print':
            if self.job is None:
                return subprocess.CompletedProcess(args, 113, '', 'Could not find service test')
            injected = self.job != self.original
            output = f'\tpath = {self.job}\n\tprogram = {module.AGENT}\n\tstate = running\n\tpid = 123\n'
            if injected:
                output += '\tDYLD_INSERT_LIBRARIES => test\n'
            return subprocess.CompletedProcess(args, 0, output, '')
        if args[0] == 'bootout':
            self.job = None
        if args[0] == 'bootstrap':
            path = Path(args[2])
            if self.fail_probe_bootstrap and path != self.original:
                return subprocess.CompletedProcess(args, 5, '', 'injected startup failure')
            self.job = path
        return subprocess.CompletedProcess(args, 0, '', '')

    def test_failed_bootstrap_restores_original(self):
        self.fail_probe_bootstrap = True
        with self.assertRaisesRegex(RuntimeError, 'bootstrap failed'):
            self.agent.start()
        self.assertEqual(self.job, self.original)
        self.assertFalse(self.agent.settings.exists())
        self.assertIsNone(self.agent.lock)

    def test_interrupted_start_restores_original(self):
        self.agent.stop.set()
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            self.agent.start()
        self.assertEqual(self.job, self.original)
        self.assertFalse(self.agent.settings.exists())

    def test_foreign_injected_job_is_not_replaced(self):
        foreign = self.root / 'someone-else.plist'
        self.job = foreign
        with self.assertRaisesRegex(RuntimeError, 'unexpected configuration'):
            self.agent.start()
        self.assertEqual(self.job, foreign)
        self.assertFalse(any(c[0] in ('bootout', 'bootstrap') for c in self.calls))

    def test_failed_observation_is_not_treated_as_missing_job(self):
        self.agent.command = lambda *args: subprocess.CompletedProcess(args, 1, '', 'temporary service communication error')
        with self.assertRaisesRegex(RuntimeError, 'Cannot inspect'):
            self.agent.state()
        self.assertEqual(self.job, self.original)

    def test_settings_cleanup_failure_does_not_skip_agent_restoration(self):
        self.agent.settings.write_text('{}')
        self.agent.changed = True
        self.job = self.agent.plist
        with patch.object(Path, 'rename', side_effect=OSError('simulated file cleanup failure')):
            result = self.agent.close()
        self.assertEqual(self.job, self.original)
        self.assertIn('settings_disable_error', result)

    def test_close_waits_for_exiting_owned_job_before_restoring_original(self):
        self.agent.changed = True
        owned = {'path': str(self.agent.plist), 'program': module.AGENT, 'injected': True}
        restored = {'path': str(self.original), 'program': module.AGENT, 'injected': False}
        with patch.object(self.agent, 'state', side_effect=[owned, owned, owned, {}, restored]), \
                patch.object(module.time, 'sleep'):
            self.assertEqual(self.agent.close(), restored)
        self.assertEqual([c[0] for c in self.calls], ['bootout', 'bootstrap'])
        self.assertFalse(self.agent.changed)

    def test_close_waits_for_original_registration_to_be_observable(self):
        self.agent.changed = True
        restored = {'path': str(self.original), 'program': module.AGENT, 'injected': False}
        with patch.object(self.agent, 'state', side_effect=[{}, {}, {}, restored]), \
                patch.object(module.time, 'sleep'):
            self.assertEqual(self.agent.close(), restored)
        self.assertEqual([c[0] for c in self.calls], ['bootstrap'])

    def test_unload_timeout_preserves_pending_cleanup_without_bootstrap(self):
        self.agent.changed = True
        owned = {'path': str(self.agent.plist), 'program': module.AGENT, 'injected': True}
        with patch.object(self.agent, 'state', return_value=owned), \
                patch.object(module.time, 'monotonic', side_effect=[0, 9]):
            with self.assertRaisesRegex(RuntimeError, 'still unloading'):
                self.agent.close()
        self.assertTrue(self.agent.changed)
        self.assertEqual([c[0] for c in self.calls], ['bootout'])

    def test_foreign_job_after_unload_is_preserved(self):
        self.agent.changed = True
        owned = {'path': str(self.agent.plist), 'program': module.AGENT, 'injected': True}
        foreign = {'path': '/other/agent.plist', 'program': module.AGENT, 'injected': False}
        with patch.object(self.agent, 'state', side_effect=[owned, foreign]):
            with self.assertRaisesRegex(RuntimeError, 'ownership changed'):
                self.agent.close()
        self.assertEqual([c[0] for c in self.calls], ['bootout'])

    def test_failed_unload_observation_never_bootstraps_over_unknown_job(self):
        self.agent.changed = True
        owned = {'path': str(self.agent.plist), 'program': module.AGENT, 'injected': True}
        with patch.object(self.agent, 'state', side_effect=[owned, RuntimeError('query timed out')]):
            with self.assertRaisesRegex(RuntimeError, 'query timed out'):
                self.agent.close()
        self.assertTrue(self.agent.changed)
        self.assertEqual([c[0] for c in self.calls], ['bootout'])


if __name__ == '__main__':
    unittest.main()
