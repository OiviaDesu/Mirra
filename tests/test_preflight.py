from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from preflight import check


class ConnectionCheckTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()

    def test_ready_bridge_opens_without_target_or_radio_queries(self):
        report = {'state': 'ready', 'detail': 'Bridge answered.', 'actions': ['open_mirroring']}
        observer = Mock(return_value=report)
        # A ready bridge must be sufficient even if Bluetooth/discovery access
        # is unavailable. No extra subprocess can veto Apple's own discovery.
        with patch('subprocess.run', side_effect=AssertionError('Unexpected radio or target query')):
            result = check(self.service, observer=observer)
        observer.assert_called_once_with(self.service)
        self.assertEqual(result['state'], 'ready')
        self.assertIs(result['report'], report)
        self.assertIsNone(result['mirroring_connection_verified'])
        self.service.start.assert_not_called()
        self.service.stop.assert_not_called()

    def test_unavailable_or_unhealthy_bridge_blocks_without_restart(self):
        for state in ('waiting_for_bridge', 'unknown', 'recovering', 'stopped',
                      'starting', 'setup_required', 'launch_support_unavailable'):
            with self.subTest(state=state):
                report = {'state': state, 'detail': 'Check the configured bridge.'}
                observer = Mock(return_value=report)
                result = check(self.service, observer=observer)
                self.assertEqual(result['state'], 'blocked')
                self.assertEqual(result['message'], report['detail'])
                self.assertIs(result['report'], report)
                observer.assert_called_once_with(self.service)
        self.service.start.assert_not_called()
        self.service.stop.assert_not_called()
