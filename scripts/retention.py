"""Retain recent, explicitly retired runtime directories; preserve uncertain data.

No glob of old research output grants deletion authority. Only new directories
carrying this module's marker can be retired, and the caller must first verify
that their app/agent/worker resources have been released.
"""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time

from processes import process_birth
from state import write_state

MARKER = '.runtime-retention.json'
PATTERNS = {'backend': r'backend-[0-9]{6,}', 'native': r'native-[0-9]{8}-[0-9]{6}'}
KEEP_COMPLETED = 3


def _owned(path, directory=False, private=True):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & (0o077 if private else 0o022)):
        raise RuntimeError('Runtime retention requires private owned files')
    return info


def _read(directory):
    info = _owned(directory, directory=True)
    _owned(directory / MARKER)
    value = json.loads((directory / MARKER).read_text())
    if (value.get('schema_version') != 1 or value.get('kind') not in PATTERNS
            or re.fullmatch(PATTERNS[value['kind']], directory.name) is None
            or value.get('inode') != info.st_ino or value.get('device') != info.st_dev):
        raise RuntimeError('Runtime retention marker does not identify this directory')
    birth = value.get('owner_birth')
    if (not isinstance(birth, list) or len(birth) != 4
            or any(type(n) is not int for n in birth) or birth[0] <= 0 or birth[1] != os.getuid()):
        raise RuntimeError('Runtime retention owner identity is invalid')
    return value


def track(directory, kind):
    directory = Path(directory)
    info = _owned(directory, directory=True)
    if kind not in PATTERNS or re.fullmatch(PATTERNS[kind], directory.name) is None:
        raise ValueError('Unexpected managed runtime directory name')
    if (directory / MARKER).exists():
        raise RuntimeError('Runtime directory is already tracked')
    birth = process_birth(os.getpid())
    if birth is None:
        raise RuntimeError('Cannot identify the runtime owner')
    write_state(directory / MARKER, {'schema_version': 1, 'kind': kind,
        'inode': info.st_ino, 'device': info.st_dev, 'owner_birth': list(birth),
        'retired': False})


def retire(directory):
    """Called only after verified cleanup; existing research output stays unmarked."""
    directory = Path(directory)
    if not (directory / MARKER).exists():
        return False
    value = _read(directory)
    if value['owner_birth'] != list(process_birth(os.getpid()) or ()):
        raise RuntimeError('Only the owning controller can retire its runtime directory')
    if value['retired'] is not True:
        value.update(retired=True, retired_at=time.time())
        write_state(directory / MARKER, value)
    return True


def _check_tree(directory):
    # Refuse links, sockets, unexpected ownership and writable public files before any
    # deletion. rmtree's descriptor-based traversal protects against link swaps.
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in directories:
            _owned(Path(parent) / name, directory=True)
        for name in files:
            # Every containing directory is private. launchd may create 0644
            # logs there, which remain inaccessible outside the owned tree.
            _owned(Path(parent) / name, private=False)


def prune(root, kind, *, protected=(), keep=KEEP_COMPLETED):
    root = Path(root)
    if kind not in PATTERNS or keep < 1:
        raise ValueError('Invalid runtime retention policy')
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError('Safe directory removal is unavailable on this Python platform')
    protected = {Path(p).absolute() for p in protected}
    eligible = []
    deferred = 0
    for directory in root.iterdir():
        if (not re.fullmatch(PATTERNS[kind], directory.name)
                or not (directory / MARKER).exists()):
            continue
        try:
            value = _read(directory)
            if value['kind'] != kind or value.get('retired') is not True:
                continue
            # Backend directories are retired by a still-live supervisor after
            # its cleanup gate passes. Native supervisors must themselves exit.
            if kind == 'native' and process_birth(value['owner_birth'][0]) == tuple(value['owner_birth']):
                continue
            retired_at = value.get('retired_at')
            if type(retired_at) not in (int, float):
                raise ValueError('Invalid retirement timestamp')
            _check_tree(directory)
            eligible.append((retired_at, directory))
        except (OSError, ValueError, RuntimeError):
            deferred += 1
    eligible.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    removed = []
    for _, directory in eligible[keep:]:
        if directory.absolute() in protected:
            continue
        try:
            _read(directory)  # Recheck the same inode immediately before removal.
            shutil.rmtree(directory)
            removed.append(directory.name)
        except (OSError, ValueError, RuntimeError):
            deferred += 1
    return {'removed': removed, 'deferred': deferred, 'keep_completed': keep}
