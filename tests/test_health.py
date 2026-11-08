import json
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from keystore import AksBroker, SESSION_BASE
from transport import Endpoint
from health import health, wait_for_backend_loss


class BackendHealthTests(unittest.TestCase):
    def watch(self, observations, polls=None):
        stop = Mock()
        stop.wait.side_effect = [False] * len(observations) + [True]
        stop.is_set.return_value = False
        process = Mock()
        process.poll.side_effect = polls
        process.poll.return_value = None
        with patch('health.health', side_effect=observations) as query:
            result = wait_for_backend_loss(process, '/unused', stop, 1)
        return result, process, query

    def test_missing_or_malformed_observations_do_not_authorize_restart(self):
        observations = [TimeoutError(), ConnectionError(), FileNotFoundError(), ValueError(),
                        {}, {'ready': None}, {'ready': 0}, {'error': 'unknown'}, {'ready': True}]
        result, process, query = self.watch(observations)
        self.assertIsNone(result)
        self.assertEqual(process.poll.call_count, len(observations))
        self.assertEqual(query.call_count, len(observations))

    def test_explicit_failure_requires_confirmation_and_healthy_reply_clears_it(self):
        result, _, _ = self.watch([{'ready': False}, {'ready': True}, {'ready': False}])
        self.assertIsNone(result)
        result, _, query = self.watch([{'ready': False}, TimeoutError(), {'ready': False}])
        self.assertEqual(result, 'broker_reported_unhealthy')
        self.assertEqual(query.call_count, 3)

    def test_process_exit_after_missed_observation_is_authoritative(self):
        # The same Popen handle is checked again after the observation expires.
        result, process, query = self.watch([TimeoutError(), {'ready': True}], polls=[None, 7])
        self.assertEqual(result, 'broker_exited')
        self.assertEqual(process.poll.call_count, 2)
        self.assertEqual(query.call_count, 1)

    def test_actual_terminal_process_needs_no_health_query(self):
        with subprocess.Popen([sys.executable, '-c', 'raise SystemExit(7)']) as process:
            self.assertEqual(process.wait(timeout=5), 7)
            with patch('health.health') as query:
                self.assertEqual(wait_for_backend_loss(process, '/unused', threading.Event(), 1, interval=0),
                                 'broker_exited')
                query.assert_not_called()

    def serve(self, callback):
        temporary = tempfile.TemporaryDirectory(prefix='hm-health-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'broker.sock'

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                def readn(count):
                    data = b''
                    while len(data) < count:
                        chunk = self.request.recv(count - len(data))
                        if not chunk:
                            raise ConnectionError('closed')
                        data += chunk
                    return data
                try:
                    size = struct.unpack('>I', readn(4))[0]
                    if json.loads(readn(size)) != {'operation': 'health'}:
                        raise AssertionError('Unexpected request')
                    callback(self.request)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # A timed-out observer may close before the reply.

        server = socketserver.ThreadingUnixStreamServer(str(path), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(close)
        return path

    @staticmethod
    def reply(connection, response):
        data = json.dumps(response).encode()
        connection.sendall(struct.pack('>I', len(data)) + data)

    def test_partial_reply_obeys_one_overall_observation_deadline(self):
        def fragmented(connection):
            data = b'{"ready":true}'
            connection.sendall(struct.pack('>I', len(data)))
            for byte in data:
                time.sleep(.03)
                connection.sendall(bytes([byte]))
        path = self.serve(fragmented)
        start = time.monotonic()
        with self.assertRaises((TimeoutError, socket.timeout)):
            health(path, timeout=.1)
        self.assertLess(time.monotonic() - start, .3)

    def test_invalid_wire_reply_is_an_observation_error(self):
        path = self.serve(lambda connection: self.reply(connection, []))
        with self.assertRaises(ValueError):
            health(path)

    def test_failed_native_cleanup_reaches_watcher_with_endpoint_still_running(self):
        # A real endpoint process still validates its key after one reset fails.
        # The broker must retain that handle and explicitly request recovery;
        # neither process death nor transport loss can account for this result.
        program = '''import json,sys
print('{"ready":true}',flush=True)
for line in sys.stdin:
 r=json.loads(line)
 status=-7 if r['operation']=='reset' and r['session']==97 else 0
 print(json.dumps({'status':status}),flush=True)
'''
        with Endpoint(command=[sys.executable, '-u', '-c', program], timeout=1) as endpoint:
            broker = AksBroker(endpoint)
            broker.classes[9] = {'identity': 'fixture-only'}
            broker.sessions = {SESSION_BASE + 1: 97, SESSION_BASE + 2: 204}
            self.assertEqual(broker.reset_owned_sessions()['status'], -7)
            self.assertEqual(endpoint.call('validate')['status'], 0)
            path = self.serve(lambda connection: self.reply(connection, broker.execute({'operation': 'health'})))
            stop = threading.Event()
            timer = threading.Timer(3, stop.set)
            timer.start()
            try:
                result = wait_for_backend_loss(endpoint.process, path, stop, 1, interval=.01)
            finally:
                timer.cancel()
            self.assertEqual(result, 'broker_reported_unhealthy')
            self.assertIsNone(endpoint.process.poll())
            self.assertEqual(broker.sessions, {SESSION_BASE + 1: 97})
