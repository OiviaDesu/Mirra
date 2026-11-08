import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownership import LaunchOwnerJournal


class LaunchOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        patcher=patch.object(Path,'home',return_value=self.root);patcher.start();self.addCleanup(patcher.stop)
        self.directory=self.root/'runtime/native-test';self.directory.mkdir(parents=True)
        self.journal=LaunchOwnerJournal(self.root/'runtime')
        self.birth=(os.getpid(),os.getuid(),100,1)
        with patch('ownership.process_birth',return_value=self.birth):
            self.journal.publish(self.directory,{})

    def test_live_controller_cannot_be_recovered(self):
        with patch('ownership.process_birth',return_value=self.birth),self.assertRaisesRegex(RuntimeError,'still running'):
            self.journal.abandoned()
        self.assertTrue(self.journal.path.exists())

    def test_reused_pid_is_distinguished_from_the_original_owner(self):
        with patch('ownership.process_birth',return_value=(self.birth[0],self.birth[1],200,2)):
            self.assertEqual(self.journal.abandoned()['directory'],str(self.directory.resolve()))

    def test_observation_error_does_not_authorize_recovery(self):
        with patch('ownership.process_birth',side_effect=OSError('temporary observation failure')),self.assertRaises(OSError):
            self.journal.abandoned()
        self.assertTrue(self.journal.path.exists())

    def test_wrong_runtime_directory_is_rejected(self):
        data=json.loads(self.journal.path.read_text());data['directory']=str(self.root/'unrelated')
        self.journal.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(RuntimeError,'outside'):
            self.journal.abandoned()
