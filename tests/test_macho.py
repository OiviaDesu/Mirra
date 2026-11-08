from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from macho import require_ios_endpoint


class MachOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.binary = Path(self.temp.name) / 'endpoint'

    def sample(self, platform=2, cpu=0x100000C, size=24, count=1, tail=b'', subtype=0):
        command = struct.pack('<6I', 0x32, size, platform, 15 << 16, 18 << 16, 0)
        self.binary.write_bytes(struct.pack('<8I', 0xFEEDFACF, cpu, subtype, 2, count,
                                           len(command) + len(tail), 0, 0) + command + tail)
        return require_ios_endpoint(self.binary)

    def test_device_ios_arm64_is_accepted_without_external_tools(self):
        self.assertEqual(self.sample(), {'architecture': 'arm64', 'platform': 'ios', 'minimum_os': '15.0.0'})

    def test_macos_and_simulator_are_refused(self):
        for platform in (1, 7):
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, 'device iOS'):
                self.sample(platform=platform)

    def test_wrong_architecture_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'arm64'):
            self.sample(cpu=0x1000007)

    def test_arm64e_cannot_be_mislabeled_as_arm64_for_older_bridges(self):
        for subtype in (1, 2, 0x80000002, 0x80000000):
            with self.subTest(subtype=subtype), self.assertRaisesRegex(ValueError, 'generic arm64'):
                self.sample(subtype=subtype)

    def test_truncated_header_and_commands_are_refused(self):
        self.sample()
        original = self.binary.read_bytes()
        for length in (0, 31, 40, 55):
            self.binary.write_bytes(original[:length])
            with self.subTest(length=length), self.assertRaises(ValueError):
                require_ios_endpoint(self.binary)

    def test_unbounded_misaligned_duplicate_and_trailing_commands_are_refused(self):
        for settings in ({'size': 0}, {'size': 16}, {'size': 25}, {'size': 1048576},
                         {'count': 0}, {'count': 2}, {'tail': b'ignored'},
                         {'count': 2, 'tail': struct.pack('<6I', 0x32, 24, 1, 15 << 16, 0, 0)}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.sample(**settings)


if __name__ == '__main__':
    unittest.main()
