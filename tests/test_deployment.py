from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deployment import compiler_flags, verify_macos_binary, verify_app


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'Requires the macOS linker')
class DeploymentTargetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'main.c'
        self.source.write_text('int main(void) { return 0; }\n')

    def build(self, name, flags):
        binary = self.root / name
        subprocess.run(['xcrun', 'clang', *flags, str(self.source), '-o', str(binary)],
                       capture_output=True, check=True, timeout=30)
        return binary

    def test_shared_flags_produce_a_real_sequoia_executable(self):
        binary = self.build('sequoia', compiler_flags())
        self.assertEqual(verify_macos_binary(binary),
                         {'architecture': 'x86_64', 'minimum_macos': '15.0'})

    def test_newer_binary_cannot_be_packaged_as_sequoia(self):
        binary = self.build('newer', ['-target', 'x86_64-apple-macos26.0'])
        with self.assertRaisesRegex(ValueError, 'requires macOS 26.0'):
            verify_macos_binary(binary)

    def test_old_runtime_is_allowed_and_wrong_architecture_is_rejected(self):
        older = self.build('older', ['-target', 'x86_64-apple-macos10.15'])
        self.assertEqual(verify_macos_binary(older)['minimum_macos'], '10.15')
        arm = self.build('arm', ['-target', 'arm64-apple-macos15.0'])
        with self.assertRaisesRegex(ValueError, 'Missing x86_64'):
            verify_macos_binary(arm)

    def test_app_metadata_cannot_disagree_with_its_target(self):
        contents = self.root / 'Mirra.app/Contents'
        contents.mkdir(parents=True)
        (contents / 'Info.plist').write_bytes(plistlib.dumps({'LSMinimumSystemVersion': '26.0'}))
        with self.assertRaisesRegex(ValueError, 'minimum macOS differs'):
            verify_app(contents.parent)


if __name__ == '__main__':
    unittest.main()
