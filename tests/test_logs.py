import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from logs import BoundedLogs,open_append


class BoundedLogTests(unittest.TestCase):
    def test_rotation_preserves_writer_inode_and_new_writes_append_without_holes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'diagnostics.log'
            manager=BoundedLogs(limit=1024)
            try:
                with open_append(path,binary=True) as writer:
                    manager.watch_stream(path,writer)
                    inode=os.fstat(writer.fileno()).st_ino
                    writer.write(b'a'*2048+b'last\n');writer.flush()
                    manager.rotate_if_needed()
                    writer.write(b'next\n');writer.flush()
                    self.assertEqual(path.read_bytes(),b'next\n')
                    self.assertEqual(path.stat().st_ino,inode)
                    backup=path.with_name(path.name+'.1').read_bytes()
                    self.assertEqual(len(backup),1024)
                    self.assertTrue(backup.endswith(b'last\n'))
            finally:manager.close()

    def test_nonappend_writer_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=BoundedLogs(limit=1024)
            try:
                path=Path(directory)/'wrong.log'
                with path.open('w') as writer,self.assertRaisesRegex(ValueError,'O_APPEND'):
                    manager.watch_stream(path,writer)
            finally:manager.close()

    def test_replaced_path_is_not_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=BoundedLogs(limit=1024)
            try:
                path=Path(directory)/'original.log'
                with open_append(path) as writer:
                    manager.watch_stream(path,writer)
                    path.rename(path.with_suffix('.old'))
                    path.write_text('replacement'*200)
                    with self.assertRaisesRegex(RuntimeError,'path changed'):
                        manager.rotate_if_needed()
                    self.assertEqual(path.read_text(),'replacement'*200)
            finally:manager.close()
