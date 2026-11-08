"""Dated app logs and seven-day retention, limited to Mirra's log filenames."""
from datetime import date, datetime, timedelta
import os
import re
import stat

RETENTION_DAYS = 7
SOURCES = ('app', 'setup', 'control', 'native')
MANAGED = re.compile(r'^(app|setup|control|native)(?:-(\d{4}-\d{2}-\d{2}))?\.log(?:\.1)?$')


def dated_name(name, day=None):
    day = day or date.today()
    if name in {source + '.log' for source in SOURCES}:
        return name[:-4] + '-' + day.isoformat() + '.log'
    return name


def file_day(name, info):
    match = MANAGED.fullmatch(name)
    if not match:
        return None
    try:
        return date.fromisoformat(match[2]) if match[2] else datetime.fromtimestamp(info.st_mtime).date()
    except (ValueError, OverflowError, OSError):
        return None


def prune(folder, today=None):
    """Unlink expired, private regular logs relative to an open directory."""
    today = today or date.today()
    cutoff = today - timedelta(days=RETENTION_DAYS)
    removed = []
    for name in os.listdir(folder):
        if not MANAGED.fullmatch(name):
            continue
        try:
            info = os.stat(name, dir_fd=folder, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                continue
            day = file_day(name, info)
            if day is not None and day <= cutoff:
                os.unlink(name, dir_fd=folder)
                removed.append(name)
        except FileNotFoundError:
            continue
    return removed


def migrate_legacy(base):
    """Preserve old undated logs under their last recording date; never overwrite."""
    folder = os.dup(base) if isinstance(base, int) else os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    renamed = []
    try:
        info = os.fstat(folder)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Log directory must be private')
        for source in SOURCES:
            for suffix in ('', '.1'):
                name = source + '.log' + suffix
                try:
                    info = os.stat(name, dir_fd=folder, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                        continue
                    target = dated_name(source + '.log', datetime.fromtimestamp(info.st_mtime).date()) + suffix
                    # link/unlink preserves the inode for a writer that is finishing,
                    # and fails if the dated destination already exists.
                    os.link(name, target, src_dir_fd=folder, dst_dir_fd=folder, follow_symlinks=False)
                    os.unlink(name, dir_fd=folder)
                    renamed.append({'from':name,'to':target})
                except (FileNotFoundError, FileExistsError):
                    continue
        return renamed
    finally:
        os.close(folder)
