import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from interpreter import copy_runtime, extract_runtime, fetch, validate_runtime


def fixture_runtime(root):
    files = {'bin/python3': b'\xcf\xfa\xed\xfetest runtime fixture',
             'licenses/LICENSE.cpython.txt': b'license test fixture'}
    for name, data in files.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    (root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'executable': 'bin/python3',
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}))


class InterpreterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, link):
        path = self.root / 'runtime.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            member = tarfile.TarInfo('python/bin/python3.14'); data = b'\xcf\xfa\xed\xfefixture'
            member.size = len(data); archive.addfile(member, io.BytesIO(data))
            alias = tarfile.TarInfo('python/bin/python3'); alias.type = tarfile.SYMTYPE; alias.linkname = link
            archive.addfile(alias)
        return path

    def test_runtime_alias_is_materialized_without_external_symlinks(self):
        destination = self.root / 'python'
        extract_runtime(self.archive('python3.14'), destination)
        self.assertEqual((destination / 'bin/python3').read_bytes(), b'\xcf\xfa\xed\xfefixture')
        self.assertFalse((destination / 'bin/python3').is_symlink())
        self.assertEqual((destination / 'bin/python3').stat().st_mode & 0o777, 0o700)

    def test_escaping_and_cyclic_archive_links_are_refused(self):
        for i, link in enumerate(('/outside', '../../outside', 'python3')):
            with self.subTest(link=link), self.assertRaises(ValueError):
                extract_runtime(self.archive(link), self.root / str(i))

    def test_copy_is_independent_and_ignores_unlisted_runtime_files(self):
        source = self.root / 'source'; fixture_runtime(source)
        (source / 'unexpected.py').write_text('not included')
        destination = self.root / 'relocated/python'
        copy_runtime(source, destination)
        (source / 'bin/python3').write_bytes(b'changed')
        self.assertFalse((destination / 'unexpected.py').exists())
        self.assertTrue(validate_runtime(destination))
        with self.assertRaisesRegex(ValueError, 'changed'):
            validate_runtime(source)

    def test_changed_runtime_prevents_copy(self):
        source = self.root / 'source'; fixture_runtime(source)
        (source / 'bin/python3').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            copy_runtime(source, self.root / 'copy')
        self.assertFalse((self.root / 'copy').exists())

    def test_missing_notice_is_not_a_complete_runtime(self):
        source = self.root / 'source'; fixture_runtime(source)
        (source / 'licenses/LICENSE.cpython.txt').unlink()
        with self.assertRaisesRegex(ValueError, 'changed'):
            validate_runtime(source)

    def test_changed_cached_download_is_not_executed_or_replaced(self):
        cache = self.root / 'downloads'; cache.mkdir()
        digest = 'a' * 64; (cache / digest).write_bytes(b'different content')
        with patch('interpreter.subprocess.run') as download:
            with self.assertRaisesRegex(ValueError, 'reviewed pin'):
                fetch({'sha256': digest, 'url': 'https://example.invalid/runtime'}, cache)
            download.assert_not_called()
