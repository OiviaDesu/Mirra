import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from workspace import BrokerWorkspace, cleanup
from retention import MARKER, track, retire, prune
from state import write_state


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def directory(self, name, kind='backend', retired=True):
        path = self.root / name
        path.mkdir(mode=0o700)
        track(path, kind)
        write_state(path / 'fixture.json', {'fixture': True})
        if retired:
            retire(path)
        return path

    def test_repeated_attempts_keep_three_completed_and_the_active_one(self):
        for number in range(1, 11):
            self.directory(f'backend-{number:06d}')
        active = self.directory('backend-000011', retired=False)
        evidence = self.root / 'backend-000000'
        evidence.mkdir()
        (evidence / 'validation.json').write_text('old research')
        report = prune(self.root, 'backend', protected=(active,))
        self.assertEqual(len(report['removed']), 7)
        self.assertEqual({p.name for p in self.root.iterdir()},
            {'backend-000000', 'backend-000008', 'backend-000009', 'backend-000010', 'backend-000011'})

    def test_live_native_owner_and_uncertain_observation_preserve_directories(self):
        for number in range(4):
            self.directory(f'native-20260101-00000{number}', 'native')
        self.assertEqual(prune(self.root, 'native')['removed'], [])
        with patch('retention.process_birth', side_effect=OSError('observation failed')):
            report = prune(self.root, 'native')
        self.assertEqual(report['removed'], [])
        self.assertEqual(report['deferred'], 4)
        with patch('retention.process_birth', return_value=None):
            self.assertEqual(len(prune(self.root, 'native')['removed']), 1)

    def test_links_and_replaced_identity_are_preserved(self):
        linked = self.directory('backend-000001')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'precious').write_text('preserve')
        (linked / 'link').symlink_to(outside, target_is_directory=True)
        replaced = self.directory('backend-000002')
        value = json.loads((replaced / MARKER).read_text())
        value['inode'] += 1
        write_state(replaced / MARKER, value)
        for number in range(3, 7):
            self.directory(f'backend-{number:06d}')
        report = prune(self.root, 'backend')
        self.assertEqual(report['deferred'], 2)
        self.assertTrue(linked.exists())
        self.assertTrue(replaced.exists())
        self.assertEqual((outside / 'precious').read_text(), 'preserve')

    def test_launchd_log_permissions_inside_private_tree_do_not_prevent_retention(self):
        first = self.directory('backend-000001')
        log = first / 'launchd.log'
        log.write_text('diagnostics')
        log.chmod(0o644)
        unsafe = self.directory('backend-000002')
        (unsafe / 'fixture.json').chmod(0o666)
        for number in range(3, 6):
            self.directory(f'backend-{number:06d}')
        report = prune(self.root, 'backend')
        self.assertEqual(report['removed'], [first.name])
        self.assertEqual(report['deferred'], 1)
        self.assertTrue(unsafe.exists())

    def test_cannot_retire_another_controller_or_adopt_unmarked_evidence(self):
        path = self.directory('backend-000001', retired=False)
        with patch('retention.process_birth', return_value=None):
            with self.assertRaises(RuntimeError):
                retire(path)
        unmarked = self.root / 'backend-000002'
        unmarked.mkdir()
        self.assertFalse(retire(unmarked))


class BrokerWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = BrokerWorkspace(self.root)
        self.addCleanup(self.workspace.close)

    def test_inherited_child_lease_survives_parent_close(self):
        write_state(self.workspace.path / 'certificate.der', {'fixture': True})
        child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
            stdin=subprocess.PIPE, pass_fds=(self.workspace.lease,))
        try:
            self.workspace.close()
            self.assertEqual(cleanup(self.root)['reason'], 'lease_in_use')
            self.assertTrue((self.workspace.path / 'certificate.der').exists())
        finally:
            child.stdin.close()
            child.wait(timeout=3)
        self.assertTrue(cleanup(self.root)['removed'])
        self.assertFalse(self.workspace.path.exists())
        self.assertEqual(cleanup(self.root)['reason'], 'already_absent')

    def test_unexpected_file_prevents_any_removal(self):
        self.workspace.close()
        unexpected = self.workspace.path / 'user-file'
        unexpected.write_text('preserve')
        unexpected.chmod(0o600)
        with self.assertRaises(RuntimeError):
            cleanup(self.root)
        self.assertTrue(unexpected.exists())
        unexpected.unlink()
        self.assertTrue(cleanup(self.root)['removed'])


if __name__ == '__main__':
    unittest.main()
