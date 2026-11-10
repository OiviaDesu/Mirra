import io
import json
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import control
from activity import capture
from installation import NativeService


class ControlReplyTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock(spec=NativeService)
        self.service.package = Mock()
        self.service.package.exists.return_value = True
        self.report = {'state': 'update_available', 'actions': ['install', 'guide']}

    def invoke(self, *, logging=True):
        output, events = io.StringIO(), []
        with patch('control.NativeService', return_value=self.service), \
                patch('control.observe', return_value=self.report), \
                patch.object(sys, 'argv', ['control.py', 'install']), \
                redirect_stdout(output), capture(events.append if logging else None):
            status = control.control()
        return status, json.loads(output.getvalue()), events

    def test_failed_update_returns_one_failure_reply_without_logging_exception(self):
        self.service.update_saved.side_effect = RuntimeError(
            'Finish native session cleanup before replacing the package')
        status, reply, events = self.invoke()
        self.assertEqual(status, 1)
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['report'], self.report)
        self.assertIn('Refresh status', reply['message'])
        replies = [event for event in events if event['message'] == 'reply']
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]['fields']['result'], reply)
        self.service.update_saved.assert_called_once_with()
        self.service.install.assert_not_called()

    def test_failed_update_does_not_require_a_log_sink_or_expose_exception_details(self):
        self.service.update_saved.side_effect = RuntimeError('PRIVATE_ACCOUNT_DETAIL')
        status, reply, events = self.invoke(logging=False)
        self.assertEqual(status, 1)
        self.assertFalse(reply['ok'])
        self.assertNotIn('PRIVATE_ACCOUNT_DETAIL', json.dumps(reply))
        self.assertEqual(events, [])

    def test_successful_update_preserves_the_protocol_and_action(self):
        status, reply, events = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(reply, {'ok': True, 'report': self.report})
        self.assertEqual(events[-1]['fields']['result'], reply)
        self.service.update_saved.assert_called_once_with()
        self.service.install.assert_not_called()


if __name__ == '__main__':
    unittest.main()
