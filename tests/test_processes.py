from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from processes import NativeAppObserver


class NativeAppObservationTests(unittest.TestCase):
    def setUp(self):
        self.observer = NativeAppObserver('/system/original/app', '/session/adapter.dylib')
        self.observer.identity = Mock(return_value=(42, 100, 1))

    @staticmethod
    def pids():
        return subprocess.CompletedProcess(['pgrep'], 0, '42\n', '')

    @staticmethod
    def images():
        return subprocess.CompletedProcess(['lsof'], 0, 'p42\nn/session/adapter.dylib\n', '')

    def test_image_timeout_is_retried_without_marking_process_owned_or_dead(self):
        with patch('processes.subprocess.run', side_effect=[
                self.pids(), subprocess.TimeoutExpired('lsof', 3), self.pids(), self.images()]):
            self.assertEqual(self.observer.poll(), [])
            self.assertEqual(self.observer.last_errors, ['TimeoutExpired'])
            self.assertEqual(self.observer.poll(), [42])
            self.assertEqual(self.observer.last_errors, [])

    def test_known_process_does_not_repeat_expensive_image_inspection(self):
        with patch('processes.subprocess.run', side_effect=[self.pids(), self.images(), self.pids()]) as run:
            self.assertEqual(self.observer.poll(), [42])
            self.assertEqual(self.observer.poll(), [42])
            self.assertEqual(sum(c.args[0][0] == '/usr/sbin/lsof' for c in run.call_args_list), 1)
            self.assertIn('-n', run.call_args_list[1].args[0])
            self.assertIn('txt', run.call_args_list[1].args[0])

    def test_reused_pid_does_not_inherit_previous_adapter_ownership(self):
        with patch('processes.subprocess.run', side_effect=[self.pids(), self.images()]):
            self.assertEqual(self.observer.poll(), [42])
        self.observer.identity.return_value = (42, 200, 3)
        missing = subprocess.CompletedProcess(['lsof'], 0, 'p42\nn/system/original/app\n', '')
        with patch('processes.subprocess.run', side_effect=[self.pids(), missing]):
            self.assertEqual(self.observer.poll(), [])
            self.assertNotIn(42, self.observer.verified)


if __name__ == '__main__':
    unittest.main()
