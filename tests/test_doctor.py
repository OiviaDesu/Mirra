import json
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from configuration import load_config
from doctor import inspect_backend
from test_provisioning import fixture_ltk


class BridgeDiagnosticTests(unittest.TestCase):
    def test_report_checks_wire_layout_and_excludes_device_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            certificate = root / 'certificate.der'; certificate.write_bytes(b'fixture')
            profile = root / 'config.json'; profile.write_text('{}')
            config = replace(load_config(profile), certificate_file=certificate)
            with patch('transport.Endpoint') as endpoint:
                bridge = endpoint.return_value.__enter__.return_value
                bridge.ready = {'dcrt_bytes': 7, 'dcrt_sha256': 'private-digest',
                                'process_architecture': 'arm64', 'product_type': 'iPhone10,5'}
                bridge.certificate_binding_verified = True
                bridge.call.return_value = {'status': 0, 'identity': 'private-key-handle', 'ltk': fixture_ltk()}
                report = inspect_backend(config)
                self.assertEqual(report['status'], 'pass')
                self.assertTrue(report['wire_layout_validated'])
                self.assertEqual(report['capabilities']['process_architecture'], 'arm64')
                self.assertNotIn('private-', json.dumps(report))
                self.assertNotIn(fixture_ltk(), json.dumps(report))
                bridge.call.return_value['ltk'] = 'dW5zdXBwb3J0ZWQ='
                report = inspect_backend(config)
                self.assertEqual(report['status'], 'missing')
                self.assertIn('attested signing identity', report['detail'])


if __name__ == '__main__':
    unittest.main()
