from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from keystore import AksBroker, MAX_SESSIONS, NO_MEMORY, SESSION_BASE, SESSION_END, UNAVAIWORKSPACELE
from transport import Endpoint, EndpointError


class BrokerSessionTests(unittest.TestCase):
    def broker(self):
        endpoint = Mock(timeout=1)
        endpoint.process.poll.return_value = None
        broker = AksBroker(endpoint)
        broker.identities['owned-local'] = 'private-identity'
        return broker, endpoint

    def session(self, broker):
        return broker.execute({'operation': 'session_signed', 'local_ltk': 'owned-local',
                               'remote_ltk': 'owned-remote', 'flags': 2})

    def test_handles_remain_distinct_after_thousands_of_reconnects(self):
        broker, endpoint = self.broker()
        endpoint.call.side_effect = lambda op, **kw: {'status': 0, 'session': 7} if op == 'session_signed' else {'status': 0}
        old = None
        for _ in range(4100):
            current = self.session(broker)['session']
            if old is not None:
                self.assertEqual(broker.execute({'operation': 'token', 'session': old}), {'error': 'session_not_owned'})
            self.assertEqual(broker.execute({'operation': 'reset', 'session': current})['status'], 0)
            old = current
        last = self.session(broker)['session']
        endpoint.call.reset_mock()
        self.assertGreater(last, SESSION_BASE + 4096)
        self.assertEqual(broker.reset_owned_sessions()['reset_count'], 1)
        self.assertEqual(endpoint.call.call_count, 1)
        self.assertEqual(endpoint.call.call_args.kwargs['session'], 7)
        self.assertFalse(broker.sessions)

    def test_reset_failure_keeps_ownership_until_successful_cleanup(self):
        broker, endpoint = self.broker()
        broker.sessions = {SESSION_BASE + 3: 97, SESSION_BASE + 9000: 204}
        endpoint.call.side_effect = [{'status': -1}, {'status': 0}]
        result = broker.reset_owned_sessions()
        self.assertEqual((result['status'], result['reset_count'], result['remaining_sessions']), (-1, 1, 1))
        self.assertEqual(broker.sessions, {SESSION_BASE + 3: 97})
        self.assertEqual(broker.execute({'operation': 'health'})['error'], 'owned_session_reset_failed')
        self.assertFalse(broker.execute({'operation': 'health'})['ready'])
        endpoint.call.side_effect = None
        endpoint.call.reset_mock()
        self.assertEqual(self.session(broker)['error'], 'broker_requires_restart')
        endpoint.call.assert_not_called()
        endpoint.call.return_value = {'status': 0}
        self.assertEqual(broker.reset_owned_sessions()['remaining_sessions'], 0)
        # Teardown may retry known handles, but only a fresh broker may serve
        # authentication again after failure; old cached identities stay retired.
        self.assertFalse(broker.execute({'operation': 'health'})['ready'])

    def test_single_reset_can_retry_without_losing_ownership(self):
        broker, endpoint = self.broker()
        broker.sessions = {SESSION_BASE + 3: 97}
        endpoint.call.return_value = {'status': -2}
        self.assertEqual(broker.execute({'operation': 'reset', 'session': SESSION_BASE + 3})['status'], -2)
        self.assertIn(SESSION_BASE + 3, broker.sessions)
        endpoint.call.return_value = {'status': 0}
        self.assertEqual(broker.reset_owned_sessions()['remaining_sessions'], 0)
        self.assertTrue(broker.execute({'operation': 'health'})['ready'])

    def test_reset_transport_failure_stops_without_guessing_or_forgetting_handles(self):
        broker, endpoint = self.broker()
        broker.sessions = {SESSION_BASE + i: i + 80 for i in range(1, 17)}
        endpoint.call.side_effect = EndpointError('offline')
        result = broker.reset_owned_sessions()
        self.assertEqual(result['status'], UNAVAIWORKSPACELE)
        self.assertEqual(endpoint.call.call_count, 1)
        self.assertEqual(len(broker.sessions), 16)
        self.assertFalse(broker.execute({'operation': 'health'})['ready'])

    def test_validation_failure_returns_explicit_unhealthy_reply(self):
        for response in ({'status': -7}, {'status': False}, {}, EndpointError('lost command stream')):
            with self.subTest(response=response):
                broker, endpoint = self.broker()
                broker.classes[9] = {'identity': 'private-identity'}
                if isinstance(response, Exception):
                    endpoint.call.side_effect = response
                else:
                    endpoint.call.return_value = response
                self.assertFalse(broker.execute({'operation': 'health'})['ready'])
                self.assertFalse(broker.execute({'operation': 'health'})['ready'])
                endpoint.call.assert_called_once_with('validate', identity='private-identity')

    def test_malformed_reset_status_requires_recovery_and_preserves_handle(self):
        broker, endpoint = self.broker()
        broker.sessions = {SESSION_BASE + 1: 97}
        endpoint.call.return_value = {'status': False}
        self.assertEqual(broker.reset_owned_sessions()['status'], UNAVAIWORKSPACELE)
        self.assertEqual(broker.sessions, {SESSION_BASE + 1: 97})
        self.assertFalse(broker.execute({'operation': 'health'})['ready'])

    def test_invalid_resets_and_empty_cleanup_never_touch_endpoint(self):
        broker, endpoint = self.broker()
        self.assertEqual(broker.reset_owned_sessions()['status'], 0)
        for value in (-1, 7, SESSION_BASE + 2, True, '7', None):
            self.assertEqual(broker.execute({'operation': 'reset', 'session': value}), {'error': 'session_not_owned'})
        endpoint.call.assert_not_called()

    def test_capacity_and_namespace_are_checked_before_allocating_remote_sessions(self):
        broker, endpoint = self.broker()
        broker.sessions = {SESSION_BASE + i: i for i in range(1, MAX_SESSIONS + 1)}
        self.assertEqual(self.session(broker)['status'], NO_MEMORY)
        broker.sessions.clear()
        broker.counter = SESSION_END - SESSION_BASE - 1
        self.assertEqual(self.session(broker)['error'], 'session_namespace_exhausted')
        endpoint.call.assert_not_called()

    def test_endpoint_response_is_not_mutated_and_invalid_handle_is_rejected(self):
        broker, endpoint = self.broker()
        reply = {'status': 0, 'session': 12}
        endpoint.call.return_value = reply
        self.assertEqual(self.session(broker)['session'], SESSION_BASE + 1)
        self.assertEqual(reply['session'], 12)
        with self.assertRaises(EndpointError):
            self.session(broker)
        self.assertFalse(broker.execute({'operation': 'health'})['ready'])
        self.assertEqual(self.session(broker)['error'], 'broker_requires_restart')

    def test_many_slow_resets_share_one_real_transport_deadline(self):
        program = '''import json,sys,time
print('{"ready":true}',flush=True)
for line in sys.stdin:
 r=json.loads(line)
 assert '_deadline' not in r
 time.sleep(.08)
 print('{"status":0}',flush=True)
'''
        with Endpoint(command=[sys.executable, '-u', '-c', program], timeout=.14) as endpoint:
            broker = AksBroker(endpoint)
            broker.sessions = {SESSION_BASE + i: i for i in range(1, 17)}
            start = time.monotonic()
            result = broker.reset_owned_sessions()
            self.assertLess(time.monotonic() - start, .6)
            self.assertEqual(result['status'], UNAVAIWORKSPACELE)
            self.assertEqual(result['reset_count'], 1)
            self.assertEqual(result['remaining_sessions'], 15)
            with self.assertRaises(EndpointError):
                endpoint.call('validate')
