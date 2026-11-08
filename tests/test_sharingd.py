import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sharingd import SharingdAgent, ORIGINAL_PLIST, PROGRAM, service
from state import write_state


class SharingdRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.directory = self.root / 'backend-000001'
        self.directory.mkdir(mode=0o700)
        self.plist = self.directory / 'sharing.plist'
        self.plist.write_bytes(b'owned test configuration')
        self.patch_home = patch('sharingd.Path.home', return_value=self.root)
        self.patch_home.start()
        self.addCleanup(self.patch_home.stop)
        self.agent = SharingdAgent(self.directory)
        self.agent.cache.mkdir(parents=True, mode=0o700)
        self.birth = [12345, os.getuid(), 50, 6]
        self.record = {'schema_version': 1, 'controller_birth': self.birth,
            'directory': str(self.directory), 'plist_sha256': hashlib.sha256(self.plist.read_bytes()).hexdigest()}
        write_state(self.agent.journal, self.record)
        self.original = {'program': PROGRAM, 'path': str(ORIGINAL_PLIST), 'injected': False}
        self.owned = {'program': PROGRAM, 'path': str(self.plist), 'injected': True}
        self.success = subprocess.CompletedProcess([], 0, '', '')

    def test_dead_controller_restores_owned_job_and_clears_journal(self):
        with patch('sharingd.process_birth', return_value=None), \
                patch('sharingd.service', side_effect=[self.owned, {}, self.original]), \
                patch('sharingd.launchctl', return_value=self.success) as command:
            result = SharingdAgent.recover_abandoned()
        self.assertTrue(result['original_restored'])
        self.assertEqual([c.args[0] for c in command.call_args_list], ['bootout', 'bootstrap'])
        self.assertFalse(self.agent.journal.exists())
        self.assertEqual(json.loads((self.directory / 'sharingd-last-restore.json').read_text()), self.original)

    def test_live_controller_is_not_recovered_even_if_lock_is_free(self):
        with patch('sharingd.process_birth', return_value=tuple(self.birth)), \
                patch('sharingd.launchctl') as command:
            with self.assertRaisesRegex(RuntimeError, 'still running'):
                SharingdAgent.recover_abandoned()
            command.assert_not_called()
        self.assertTrue(self.agent.journal.exists())

    def test_identity_observation_failure_is_not_death(self):
        with patch('sharingd.process_birth', side_effect=OSError('not observable')), \
                patch('sharingd.launchctl') as command:
            with self.assertRaises(OSError):
                SharingdAgent.recover_abandoned()
            command.assert_not_called()

    def test_changed_job_or_plist_is_not_unloaded(self):
        foreign = dict(self.owned, path='/another/agent.plist')
        for state, change in [(foreign, False), (self.owned, True)]:
            with self.subTest(change=change):
                if change:
                    self.plist.write_bytes(b'changed')
                with patch('sharingd.process_birth', return_value=None), \
                        patch('sharingd.service', return_value=state), \
                        patch('sharingd.launchctl') as command:
                    with self.assertRaises(RuntimeError):
                        SharingdAgent.recover_abandoned()
                    command.assert_not_called()
                self.assertTrue(self.agent.journal.exists())

    def test_partial_restore_retries_from_missing_job_without_second_bootout(self):
        with patch('sharingd.process_birth', return_value=None), \
                patch('sharingd.service', side_effect=[{}, self.original]), \
                patch('sharingd.launchctl', return_value=self.success) as command:
            self.assertTrue(SharingdAgent.recover_abandoned()['original_restored'])
            self.assertEqual([c.args[0] for c in command.call_args_list], ['bootstrap'])

    def test_observation_error_does_not_look_like_missing_service(self):
        failed = subprocess.CompletedProcess([], 1, '', 'Operation not permitted')
        with patch('sharingd.launchctl', return_value=failed):
            with self.assertRaises(RuntimeError):
                service()


if __name__ == '__main__':
    unittest.main()
