import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
import struct
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from distribution import ARCHIVE_ROOT, FILE_LIST, export_source, source_payload


class SourceExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        (self.root / 'README.md').write_text('Example source\n')
        self.files = ['README.md', FILE_LIST]
        self.declare()

    def declare(self):
        (self.root / FILE_LIST).write_text(json.dumps({'schema_version': 1, 'files': self.files}))

    def test_unlisted_secrets_and_generated_artifacts_are_excluded(self):
        for name in ('private/key', 'runtime/session.json', 'config/local.json',
                     'docs/active.json', 'src/debug-not-for-sharing.py', 'build/app'):
            path = self.root / name; path.parent.mkdir(exist_ok=True)
            path.write_text('DO-NOT-EXPORT')
        data, manifest = source_payload(self.root)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()), {
                ARCHIVE_ROOT + '/' + name for name in [*self.files, 'manifest.json']})
            self.assertFalse(any(b'DO-NOT-EXPORT' in archive.read(name) for name in archive.namelist()))
        self.assertEqual(set(manifest['files']), set(self.files))

    def test_source_timestamps_and_permissions_do_not_change_archive(self):
        first, _ = source_payload(self.root)
        os.utime(self.root / 'README.md', (1, 1))
        (self.root / 'README.md').chmod(0o600)
        second, _ = source_payload(self.root)
        self.assertEqual(first, second)
        export = export_source(self.root)
        self.assertFalse(export['already_present'])
        self.assertTrue(export_source(self.root)['already_present'])

    def test_path_policy_refuses_private_and_escaping_paths_even_if_listed(self):
        for name in ('docs/active.json', 'config/local.json', 'private/key.py',
                     '../README.md', '/tmp/source.py', 'src/../README.md',
                     'scripts//tool.py', 'scripts/.device-access/key.py', 'build/helper.m'):
            with self.subTest(name=name):
                self.files = ['README.md', FILE_LIST, name]; self.declare()
                with self.assertRaises(ValueError):
                    source_payload(self.root)

    def test_symlink_file_and_parent_are_not_followed(self):
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/tool.py').symlink_to(self.root / 'README.md')
        self.files.append('scripts/tool.py'); self.declare()
        with self.assertRaises(OSError):
            source_payload(self.root)
        (self.root / 'scripts/tool.py').unlink()
        (self.root / 'scripts').rmdir()
        (self.root / 'scripts').symlink_to(self.root / 'config', target_is_directory=True)
        with self.assertRaises(OSError):
            source_payload(self.root)

    def test_binary_and_private_key_content_in_listed_file_are_refused(self):
        for data in (b'MachO\0binary', b'-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n'):
            with self.subTest(data=data):
                (self.root / 'README.md').write_bytes(data)
                with self.assertRaises(ValueError):
                    source_payload(self.root)

    def test_screenshots_are_pixels_only_and_keep_source_path_safety(self):
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
        header = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
        pixels = chunk(b'IDAT', zlib.compress(b'\0\xff\xff\xff\xff'))
        ending = chunk(b'IEND', b'')
        png = b'\x89PNG\r\n\x1a\n' + header + pixels + ending
        path = self.root / 'docs/images/app.png'; path.parent.mkdir(parents=True)
        self.files.append('docs/images/app.png'); self.declare(); path.write_bytes(png)
        data, _ = source_payload(self.root)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.read(ARCHIVE_ROOT + '/docs/images/app.png'), png)
        for corrupt in (png + b'PRIVATE', png[:-5],
                        b'\x89PNG\r\n\x1a\n' + header + chunk(b'tEXt', b'PRIVATE') + pixels + ending):
            with self.subTest(corrupt=corrupt):
                path.write_bytes(corrupt)
                with self.assertRaises(ValueError):
                    source_payload(self.root)
        for name in ('src/app.png', '.github/.private/key.py', '.github/../private/key.py'):
            self.files = ['README.md', FILE_LIST, name]; self.declare()
            with self.assertRaises(ValueError):
                source_payload(self.root)

    def test_duplicate_or_missing_source_and_existing_output_fail_safely(self):
        self.files.append('README.md'); self.declare()
        with self.assertRaises(ValueError):
            source_payload(self.root)
        self.files[-1] = 'scripts/absent.py'; self.declare()
        with self.assertRaises(OSError):
            source_payload(self.root)
        self.files.pop(); self.declare()
        output = self.root / 'review.zip'; output.write_bytes(b'preserve-existing')
        with self.assertRaises(FileExistsError):
            export_source(self.root, output)
        self.assertEqual(output.read_bytes(), b'preserve-existing')
