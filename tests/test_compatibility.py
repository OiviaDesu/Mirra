import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from compatibility import inspect_media_profile, require_media_profile, supported_apple_app


class MediaCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'build').mkdir()
        (self.root / 'build/inspector').touch()

    def reply(self, compatible, code):
        return subprocess.CompletedProcess([], code, json.dumps({'schema_version': 1,
            'profile': 'awdl-single-tile', 'compatible': compatible,
            'architecture': 'x86_64', 'os_build': 'unreviewed', 'reference_build': '25G332'}), '')

    def test_incompatible_host_is_reportable_but_cannot_start_support(self):
        with patch('compatibility.subprocess.run', return_value=self.reply(False, 1)):
            self.assertFalse(inspect_media_profile(self.root)['compatible'])
            with self.assertRaisesRegex(RuntimeError, 'unsupported on this host'):
                require_media_profile(self.root)

    def test_inconsistent_and_failed_probe_are_rejected(self):
        replies = [self.reply(True, 1), self.reply(False, 0),
                   subprocess.CompletedProcess([], 0, '{}', ''),
                   subprocess.CompletedProcess([], -11, '', 'crash')]
        for reply in replies:
            with self.subTest(reply=reply), patch('compatibility.subprocess.run', return_value=reply):
                with self.assertRaises(RuntimeError):
                    require_media_profile(self.root)

    def test_missing_probe_has_an_actionable_build_error(self):
        with self.assertRaisesRegex(RuntimeError, 'make build'):
            require_media_profile(self.root / 'absent')

    def test_sequoia_and_tahoe_app_versions_use_the_same_identity_check(self):
        for version, build in [('1.0', '1'), ('1.3', '40.2'), ('1.6', '98.5')]:
            self.assertTrue(supported_apple_app({'CFBundleIdentifier': 'com.apple.ScreenContinuity',
                'CFBundleShortVersionString': version, 'CFBundleVersion': build}))

    def test_missing_identity_and_unreviewed_app_family_are_rejected(self):
        valid = {'CFBundleIdentifier': 'com.apple.ScreenContinuity',
                 'CFBundleShortVersionString': '1.6', 'CFBundleVersion': '98.5'}
        for replacement in ({'CFBundleIdentifier': 'other.app'}, {'CFBundleIdentifier': None},
                            {'CFBundleShortVersionString': '2.0'}, {'CFBundleShortVersionString': 1},
                            {'CFBundleVersion': ''}, {'CFBundleVersion': None}):
            self.assertFalse(supported_apple_app(dict(valid, **replacement)))
