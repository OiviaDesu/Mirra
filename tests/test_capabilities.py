import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from capabilities import inspect_native_features, summarize


class NativeFeatureTests(unittest.TestCase):
    def settings(self, raw=0, label='NotSupported'):
        return {'notification_settings_signature_supported': True,
                'notification_settings_returned': True,
                'remote_notifications_setting_raw': raw,
                'remote_notifications_setting_label': label}

    def test_apple_setting_and_catalog_never_prove_delivery_or_launch(self):
        catalog = {'remote_placeholder_count': 114, 'count_capped': False,
                   'known_apple_apps': ['com.apple.mobiletimer']}
        for raw, label, expected in ((0, 'NotSupported', 'not_supported'),
                                     (1, 'Disabled', 'disabled'), (2, 'Enabled', 'enabled')):
            with self.subTest(raw=raw):
                report = summarize(self.settings(raw, label), catalog)
                self.assertEqual(report['notifications']['setting'], expected)
                self.assertEqual(report['phone_apps']['catalog'], 'present')
                self.assertIsNone(report['notifications']['delivery_verified'])
                self.assertIsNone(report['phone_apps']['launch_verified'])

    def test_missing_inconsistent_or_changed_private_api_is_unknown(self):
        examples = [{}, self.settings(0, 'Enabled'), self.settings(8, 'Future'),
                    self.settings(True, 'Disabled')]
        for key in ('notification_settings_signature_supported', 'notification_settings_returned'):
            data = self.settings(); data[key] = False; examples.append(data)
        for data in examples:
            with self.subTest(data=data):
                report = summarize(data, {'remote_placeholder_count': True})
                self.assertEqual(report['notifications']['setting'], 'unknown')
                self.assertEqual(report['phone_apps']['catalog'], 'unknown')

    def test_shareable_report_discards_private_metadata_and_paths(self):
        secret = 'PRIVATE-LOCAL-VALUE'
        settings = self.settings()
        settings.update(classes={secret: []}, notification_feature_flags={secret: True},
                        notification_eligibility={'domain': 27, 'status': 0,
                            'answer_raw': 4, 'gate_passes': True, 'device': secret})
        catalog = {'remote_placeholder_count': 1, 'count_capped': False,
                   'known_apple_apps': [secret], 'known_apple_records': [{'url': secret}]}
        self.assertNotIn(secret, json.dumps(summarize(settings, catalog)))

    def test_timeout_and_failed_helper_leave_independent_catalog_observable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'build').mkdir()
            (root / 'build/capabilities').touch()
            catalog = subprocess.CompletedProcess([], 0, json.dumps({'schema_version': 1,
                'remote_placeholder_count': 0, 'count_capped': False}), '')
            failures = [subprocess.TimeoutExpired('tool', 15),
                        subprocess.CompletedProcess([], -11, '', 'private stderr'),
                        subprocess.CompletedProcess([], 0, 'invalid', ''),
                        subprocess.CompletedProcess([], 0, '{"schema_version":2}', '')]
            for failure in failures:
                with self.subTest(failure=failure), patch('capabilities.subprocess.run',
                                                        side_effect=[failure, catalog]):
                    result = inspect_native_features(root)
                    self.assertEqual(result['notifications']['setting'], 'unknown')
                    self.assertEqual(result['phone_apps']['catalog'], 'empty')
                    self.assertIn('query_error', result['notifications'])
                    self.assertNotIn('private stderr', json.dumps(result))
