from dataclasses import replace
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from configuration import load_config
from forwarder import USBForwarder


class USBForwarderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        (root / 'config.json').write_text('{}')
        self.config = replace(load_config(root / 'config.json', root=root), transport='usb', product_type='iPhone11,2', listen_port=0)

    def test_absent_device_then_replug_uses_fresh_device_identifier(self):
        connected = []
        def endpoint(device, port):
            connected.append(device)
            local, remote = socket.socketpair()
            def echo():
                with remote:
                    request = remote.recv(100)
                    remote.sendall(str(device).encode() + b':' + request)
            threading.Thread(target=echo, daemon=True).start()
            return local
        with patch.object(USBForwarder, 'device_id', side_effect=[ConnectionError('absent'), 11, 17]), \
                patch('forwarder.connect', side_effect=endpoint):
            proxy = USBForwarder(self.config).start()
            try:
                address = proxy.server.server_address
                with socket.create_connection(address, timeout=2) as client:
                    self.assertEqual(client.recv(100), b'')
                for device in [11, 17]:
                    with socket.create_connection(address, timeout=2) as client:
                        client.sendall(b'hello')
                        self.assertEqual(client.recv(100), str(device).encode() + b':hello')
                self.assertEqual(connected, [11, 17])
            finally:
                self.assertTrue(proxy.close()['stopped'])

    def test_close_disconnects_active_stream_and_releases_listening_port(self):
        local, remote = socket.socketpair()
        self.addCleanup(remote.close)
        with patch.object(USBForwarder, 'device_id', return_value=11), \
                patch('forwarder.connect', return_value=local):
            proxy = USBForwarder(self.config).start()
            address = proxy.server.server_address
            with socket.create_connection(address, timeout=2) as client:
                client.sendall(b'ping')
                remote.settimeout(2)
                self.assertEqual(remote.recv(4), b'ping')
                proxy.close()
                self.assertEqual(client.recv(4), b'')
            with self.assertRaises(OSError):
                socket.create_connection(address, timeout=.2)

    def test_busy_port_is_not_reused_or_disrupted(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            proxy = USBForwarder(replace(self.config, listen_port=listener.getsockname()[1]))
            with self.assertRaises(OSError):
                proxy.start()
            with socket.create_connection(listener.getsockname(), timeout=1):
                accepted, _ = listener.accept()
                accepted.close()


if __name__ == '__main__':
    unittest.main()
