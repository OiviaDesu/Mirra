import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from installation import NativeService
from launcher import AGENT
from state import write_state
from readiness import observe


class SupportStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = NativeService(self.root / 'installed', self.root)
        self.service.package.mkdir(parents=True, mode=0o700)
        write_state(self.service.package / 'config.json', {'backend': {'transport': 'ssh'}})
        self.service.validate_package = Mock(return_value={'schema_version': 5, 'support_revision': 16})
        self.snapshot = {'installed': True, 'registered': True, 'login_enabled': True,
                         'supervisor_alive': True, 'native_launch_support_ready': True,
                         'backend_ready': True, 'run': 'native-test', 'backend_attempt': 1}
        self.service.status = Mock(side_effect=lambda: dict(self.snapshot))
        self.run = self.service.package / 'runtime/native-test'
        self.directory = self.run / 'backend-000001'
        self.directory.mkdir(parents=True)
        self.backend = {'phase': 'ready', 'attempt': 1, 'directory': self.directory.name,
                        'automatic_recovery': True, 'ready': True}
        self.write_backend()
        self.broker_birth = [42, os.getuid(), 100, 1]
        write_state(self.directory / 'backend-children.json', {'broker': self.broker_birth})
        self.workspace = tempfile.TemporaryDirectory(prefix='mirra-', dir='/tmp')
        self.addCleanup(self.workspace.cleanup)
        path = Path(self.workspace.name); path.chmod(0o700); info = path.stat()
        self.workspace_record = {'schema_version': 1, 'path': str(path), 'device': info.st_dev, 'inode': info.st_ino}
        write_state(self.directory / 'broker-workspace.json', self.workspace_record)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path / 'broker.sock')); (path / 'broker.sock').chmod(0o600)
        self.server.listen(); self.server.settimeout(.5)
        self.addCleanup(self.server.close)
        self.launch = {'path': str(self.run / 'native-launch-agent.plist'), 'program': AGENT,
                       'injected': True, 'pid': '43'}
        self.patch_launch = patch('readiness.NativeLaunchAgent.state', side_effect=lambda: dict(self.launch))
        self.patch_launch.start(); self.addCleanup(self.patch_launch.stop)
        self.patch_birth = patch('readiness.process_birth', side_effect=lambda pid:
                                 tuple(self.broker_birth) if pid == 42 else (43, os.getuid(), 100, 2))
        self.patch_birth.start(); self.addCleanup(self.patch_birth.stop)

    def write_backend(self):
        write_state(self.run / 'backend-status.json', self.backend)

    def reply(self, payload, *, after_request=None):
        errors = []
        def serve():
            try:
                with self.server.accept()[0] as connection:
                    expected = struct.pack('>I', 22) + b'{"operation":"health"}'
                    received = bytearray()
                    while len(received) < len(expected):
                        chunk = connection.recv(len(expected) - len(received))
                        if not chunk:
                            raise ConnectionError('Health request ended early')
                        received.extend(chunk)
                    if received != expected:
                        raise ValueError('Unexpected health request')
                    if after_request:
                        after_request()
                    data = json.dumps(payload).encode()
                    connection.sendall(struct.pack('>I', len(data)) + data)
            except Exception as error:
                errors.append(str(error))
        thread = threading.Thread(target=serve, daemon=True); thread.start()
        self.addCleanup(self.assertEqual, errors, [])
        self.addCleanup(thread.join, 1)

    def test_live_health_ready_does_not_claim_mirroring_connection(self):
        self.reply({'ready': True, 'identities': 2, 'sessions': 2})
        result = observe(self.service)
        self.assertEqual(result['state'], 'ready')
        self.assertIn('open_mirroring', result['actions'])
        self.assertIsNone(result['mirroring_connection_verified'])

    def test_legacy_package_offers_update_without_reprovisioning_or_mutation(self):
        self.service.validate_package.return_value = {'schema_version': 4}
        with patch.object(self.service, 'stop') as stop, patch.object(self.service, 'prepare') as prepare:
            result = observe(self.service)
        self.assertEqual(result['state'], 'update_available')
        self.assertIn('install', result['actions'])
        self.assertNotIn('setup', result['actions'])
        stop.assert_not_called(); prepare.assert_not_called()
        self.service.status.assert_not_called()

    def test_failed_health_observation_never_offers_restart(self):
        result = observe(self.service, timeout=.02)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['actions'], ['refresh', 'guide'])

    def test_explicit_failure_has_recovery_guidance_without_private_error(self):
        self.reply({'ready': False, 'error': 'PRIVATE host=192.0.2.9 token=secret'})
        result = observe(self.service)
        self.assertEqual(result['state'], 'recovering')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('192.0.2.9', json.dumps(result))
        self.assertNotIn('open_mirroring', result['actions'])

    def test_backend_generation_change_invalidates_a_positive_reply(self):
        def advance():
            self.backend['attempt'] = 2; self.write_backend()
        self.reply({'ready': True}, after_request=advance)
        self.assertEqual(observe(self.service)['state'], 'unknown')

    def test_foreign_workspace_is_never_queried(self):
        self.workspace_record['inode'] += 1
        write_state(self.directory / 'broker-workspace.json', self.workspace_record)
        with patch('readiness.health') as query:
            self.assertEqual(observe(self.service)['state'], 'unknown')
            query.assert_not_called()

    def test_missing_launch_agent_cannot_report_ready(self):
        self.launch['pid'] = None
        self.reply({'ready': True})
        self.assertEqual(observe(self.service)['state'], 'launch_support_unavailable')

    def test_dead_or_reused_broker_cannot_report_ready(self):
        with patch('readiness.process_birth', return_value=(42, os.getuid(), 200, 1)), \
                patch('readiness.health') as query:
            self.assertEqual(observe(self.service)['state'], 'unknown')
            query.assert_not_called()

    def test_unregistered_supervisor_must_finish_before_start_is_offered(self):
        self.snapshot['registered'] = False
        result = observe(self.service)
        self.assertEqual(result['state'], 'stopping')
        self.assertEqual(result['actions'], ['refresh'])

    def test_removed_login_registration_requires_install_not_start(self):
        self.snapshot.update(registered=False, supervisor_alive=False, login_enabled=False)
        result = observe(self.service)
        self.assertEqual(result['state'], 'stopped')
        self.assertIn('install', result['actions'])
        self.assertNotIn('start', result['actions'])

    def test_terminal_failure_does_not_promise_automatic_retry(self):
        self.snapshot['backend_ready'] = False
        self.backend['phase'] = 'failed'; self.write_backend()
        result = observe(self.service)
        self.assertEqual(result['state'], 'needs_attention')
        self.assertNotIn('retry automatically', result['detail'])

    def test_changed_installation_does_not_read_or_start_backend(self):
        self.service.validate_package.side_effect = RuntimeError('PRIVATE changed file')
        result = observe(self.service)
        self.assertEqual(result['state'], 'installation_changed')
        self.service.status.assert_not_called()
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_unknown_exception_does_not_leak_paths_or_credentials(self):
        self.service.status.side_effect = RuntimeError('/private/key material')
        result = observe(self.service)
        self.assertEqual(result['state'], 'unknown')
        self.assertNotIn('/private/key', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
