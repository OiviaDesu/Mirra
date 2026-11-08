"""Private broker sockets/certificates with a lease inherited by their users."""
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

from state import write_state

RECORD = 'broker-workspace.json'
LEASE = '.lease'
FILES = {LEASE, 'broker.sock', 'certificate.der'}


class BrokerWorkspace:
    def __init__(self, runtime):
        self.path = Path(tempfile.mkdtemp(prefix='mirra-', dir='/tmp'))
        self.path.chmod(0o700)
        self.lease = os.open(self.path / LEASE, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = self.path.stat()
        write_state(Path(runtime) / RECORD, {'schema_version': 1,
            'path': str(self.path), 'device': info.st_dev, 'inode': info.st_ino})

    def close(self):
        # Do not LOCK_UN: children share the lease's open-file description.
        if self.lease is not None:
            os.close(self.lease)
            self.lease = None


def cleanup(runtime):
    """Caller first restores sharingd; a live lease always prevents removal."""
    record = Path(runtime) / RECORD
    if not record.exists():
        return {'removed': False, 'reason': 'untracked'}
    info = record.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('Broker workspace record is not a private owned file')
    value = json.loads(record.read_text())
    path = Path(value.get('path', ''))
    if (value.get('schema_version') != 1 or path.parent.resolve() != Path('/tmp').resolve()
            or not path.name.startswith('mirra-')):
        raise RuntimeError('Unexpected broker workspace path')
    if not path.exists():
        return {'removed': False, 'reason': 'already_absent'}
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
            or (info.st_dev, info.st_ino) != (value.get('device'), value.get('inode'))):
        raise RuntimeError('Broker workspace ownership changed')
    descriptor = os.open(path / LEASE, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'removed': False, 'reason': 'lease_in_use'}
        entries = list(path.iterdir())
        for entry in entries:
            info = entry.lstat()
            expected = stat.S_ISSOCK if entry.name == 'broker.sock' else stat.S_ISREG
            if (entry.name not in FILES or not expected(info.st_mode)
                    or info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise RuntimeError('Broker workspace contains an unexpected entry')
        # Lease is removed last so an interrupted cleanup can still be retried.
        for entry in entries:
            if entry.name != LEASE:
                entry.unlink()
        (path / LEASE).unlink()
        path.rmdir()
        return {'removed': True}
    finally:
        os.close(descriptor)
