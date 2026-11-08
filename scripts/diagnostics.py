#!/usr/bin/env python3
"""Follow actual Mirra log files; redact credentials, never replace log prose.

This read-only observer never contacts a phone or controls a service. File
cursors belong to the log viewer alone. Older lines have no invented event
clock; the native panel displays them as Earlier.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

from redaction import redact_line, redact_text, redact_value
from logfiles import RETENTION_DAYS

TAIL_BYTES = 32768
STATE_BYTES = 65536
MAX_ENTRIES = 160
MAX_MESSAGE = 4000


def entry(source, message, level='info', observed_at=None):
    return {'source': source, 'level': level, 'message': message[:MAX_MESSAGE],
            'observed_at': observed_at}


def project_line(raw, source):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        value = None
    if isinstance(value, dict):
        if value.get('operation') == 'health' and not value.get('error') and not value.get('status'):
            return []  # Skip successful periodic health checks, retain failures.
        if isinstance(value.get('message'), str) and isinstance(value.get('source'), str):
            message = redact_text(value['message'])
            if isinstance(value.get('fields'), dict):
                message += ' ' + json.dumps(redact_value(value['fields']), ensure_ascii=False)
            stamp = value.get('timestamp')
            return [entry(redact_text(value['source'])[:24], message, value.get('level', 'info'),
                          stamp if isinstance(stamp, str) else None)]
        level = 'error' if value.get('error') or value.get('transport_error') or value.get('RESTORE_ERROR') else 'debug'
        status = value.get('status')
        if type(status) is int and status != 0:
            level = 'error'
        state = value.get('NATIVE_BACKEND_STATE')
        if isinstance(state, dict) and state.get('phase') == 'failed':
            level = 'error'
        return [entry(source, redact_line(raw), level)]
    message = redact_text(raw)
    if not message.strip():
        return []
    level = 'error' if re.search(r'(?i)error|failed|failure|denied|timed out|refused', message) else 'debug'
    return [entry(source, message, level)]


class PrivateReader:
    """Open known paths relative to a private root, without following symlinks."""
    def __init__(self, base):
        self.fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            self.check(os.fstat(self.fd), directory=True)
        except BaseException:
            os.close(self.fd)
            raise

    @staticmethod
    def check(info, *, directory=False):
        kind = stat.S_ISDIR if directory else stat.S_ISREG
        if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Diagnostic path is not private and user-owned')

    def close(self):
        os.close(self.fd)

    def open(self, name):
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or not path.parts:
            raise ValueError('Unexpected diagnostic path')
        directory = os.dup(self.fd)
        try:
            for part in path.parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    self.check(os.fstat(child), directory=True)
                except BaseException:
                    os.close(child)
                    raise
                os.close(directory)
                directory = child
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                self.check(os.fstat(fd))
            except BaseException:
                os.close(fd)
                raise
            return fd
        finally:
            os.close(directory)

    def state(self, name):
        fd = self.open(name)
        try:
            data = os.read(fd, STATE_BYTES + 1)
            if len(data) > STATE_BYTES:
                raise ValueError('Oversized diagnostic state')
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError('Invalid diagnostic state')
            return value
        finally:
            os.close(fd)

    def names(self, name, pattern, limit=4):
        # Discover only log basenames in the validated active attempt.
        fd = self.open_directory(name)
        try:
            return sorted(p for p in os.listdir(fd) if re.fullmatch(pattern, p))[-limit:]
        finally:
            os.close(fd)

    def open_directory(self, name):
        directory = os.dup(self.fd)
        try:
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Unexpected diagnostic directory')
            for part in path.parts:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    self.check(os.fstat(child), directory=True)
                except BaseException:
                    os.close(child)
                    raise
                os.close(directory)
                directory = child
            return directory
        except BaseException:
            os.close(directory)
            raise

    def tail(self, name, previous):
        fd = self.open(name)
        try:
            info = os.fstat(fd)
            previous = previous if isinstance(previous, dict) else {}
            offset = previous.get('offset')
            continued = (previous.get('device') == info.st_dev and previous.get('inode') == info.st_ino
                         and type(offset) is int and 0 <= offset <= info.st_size
                         and previous.get('anchor') == self.anchor(fd, offset))
            start = offset if continued else max(0, info.st_size - TAIL_BYTES)
            if info.st_size - start > TAIL_BYTES:
                start = info.st_size - TAIL_BYTES
            data = os.pread(fd, TAIL_BYTES, start)
            # A partial final line waits for the next poll. Oversized lines are
            # skipped; the parser never sees a truncated line as a valid event.
            end = data.rfind(b'\n') + 1
            offset = start + end if end else (start + len(data) if len(data) == TAIL_BYTES else start)
            complete = data[:end]
            if start and (not continued or start != previous.get('offset')):
                complete = complete.partition(b'\n')[2]
            lines = complete.splitlines()
            cursor = {'device': info.st_dev, 'inode': info.st_ino, 'offset': offset,
                      'anchor': self.anchor(fd, offset), 'mtime_ns': info.st_mtime_ns}
            return lines, cursor, continued
        finally:
            os.close(fd)

    @staticmethod
    def anchor(fd, offset):
        return hashlib.sha256(os.pread(fd, min(64, offset), max(0, offset - 64))).hexdigest()


def collect(base=None, cursor=None):
    base = Path(base or Path.home() / 'Library/Application Support/Mirra').absolute()
    cursor = cursor if isinstance(cursor, dict) else {}
    previous_files = cursor.get('files', {})
    previous_files = previous_files if isinstance(previous_files, dict) else {}
    previous_states = cursor.get('states', {})
    previous_states = previous_states if isinstance(previous_states, dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    result = {'ok': True, 'entries': [], 'cursor': {'files': {}, 'states': {}}, 'observed_at': now}
    try:
        reader = PrivateReader(base)
    except FileNotFoundError:
        return result
    except (OSError, ValueError) as error:
        result['ok'] = False
        result['entries'] = [entry('Logs', 'open diagnostics: ' + redact_text(str(error)), 'error', now)]
        return result
    state_entries = []

    def changed(name, data, source, level='debug'):
        projected = redact_value(data)
        digest = hashlib.sha256(json.dumps(projected, sort_keys=True).encode()).hexdigest()
        result['cursor']['states'][name] = digest
        if previous_states.get(name) != digest and projected:
            state_entries.append(entry(source, json.dumps(projected, ensure_ascii=False), level, now))

    def state(name):
        try:
            return reader.state(name)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError) as error:
            changed('unreadable:' + name, {'file': name, 'error': str(error)}, 'Logs', 'error')
            return {}

    try:
        files = [('logs/setup.log', 'Setup'), ('logs/control.log', 'Service'), ('logs/service.log', 'Service')]
        try:
            today = date.today()
            for name in reader.names('logs', r'(?:setup|control)-\d{4}-\d{2}-\d{2}\.log', limit=2 * RETENTION_DAYS):
                try:
                    day = date.fromisoformat(name[-14:-4])
                except ValueError:
                    continue
                if today - timedelta(days=RETENTION_DAYS) < day <= today:
                    files.append(('logs/' + name, 'Setup' if name.startswith('setup-') else 'Service'))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as error:
            changed('dated-log-files', {'error':str(error)}, 'Logs', 'error')
        service = state('service.json')
        directory = service.get('directory')
        run = Path(directory).name if isinstance(directory, str) else ''
        expected = base / 'current/runtime' / run
        if (isinstance(directory, str) and Path(directory) == expected
                and re.fullmatch(r'native-[0-9]{8}-[0-9]{6}(?:-[0-9]+)?', run)):
            runtime = 'current/runtime/' + run
            backend = state(runtime + '/backend-status.json')
            selected = {k: backend[k] for k in ('phase','attempt','ready','automatic_recovery','reason','exit_code','retry_seconds') if k in backend}
            changed('backend:' + run, selected, 'Bridge state')
            files.append((runtime + '/app.log', 'Mirroring'))
            attempt = backend.get('directory')
            if isinstance(attempt, str) and re.fullmatch(r'backend-\d{6}', attempt):
                work = runtime + '/' + attempt
                files += [(work + '/backend.log', 'Bridge'), (work + '/sharingd.stderr.log', 'Keys')]
                try:
                    for name in reader.names(work, r'(?:broker|controller)-kdebug-\d{8}-\d{6}\.log'):
                        files.append((work + '/' + name, 'Keys' if name.startswith('broker') else 'Service'))
                except (OSError, ValueError) as error:
                    changed('attempt-files', {'error': str(error)}, 'Logs', 'error')
        active = state('setup/active.json')
        identifier = active.get('id')
        if isinstance(identifier, str) and re.fullmatch(r'[0-9a-f]{32}', identifier):
            setup = state('setup/' + identifier + '/setup.json')
            fields = ('phase','error','error_code','product_type','os_version','certificate_issued','transport')
            changed('setup:' + identifier, {k: setup[k] for k in fields if k in setup}, 'Setup state')
            if setup.get('error'):
                changed('setup-error:' + identifier, state('setup/' + identifier + '/error.json'), 'Setup', 'error')
        for name, title in files:
            try:
                lines, position, continued = reader.tail(name, previous_files.get(name))
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as error:
                changed('unreadable:' + name, {'file':name,'error':str(error)}, 'Logs', 'error')
                continue
            result['cursor']['files'][name] = position
            for line in lines:
                for item in project_line(line, title):
                    recorded = item['observed_at']
                    if not item['observed_at']:
                        item['observed_at'] = now if continued else None
                    try:
                        clock = datetime.fromisoformat(recorded.replace('Z', '+00:00')).timestamp() if recorded else 0
                    except (ValueError, TypeError):
                        clock = 0
                    # Preserve old untimestamped tails first. A file's mtime
                    # includes healthy polls, so it cannot date old auth lines.
                    item['_order'] = (1, clock) if recorded else (0, position['mtime_ns'])
                    result['entries'].append(item)
        result['entries'].sort(key=lambda item: item['_order'])
        for item in result['entries']:
            item.pop('_order', None)
        result['entries'] = (result['entries'] + state_entries)[-MAX_ENTRIES:]
        return result
    finally:
        reader.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path)
    args = parser.parse_args()
    data = sys.stdin.buffer.read(STATE_BYTES + 1)
    try:
        if len(data) > STATE_BYTES:
            raise ValueError('Oversized viewer cursor')
        value = collect(args.base, json.loads(data or b'{}'))
    except (OSError, ValueError, TypeError, KeyError) as error:
        value = {'ok': False, 'entries': [entry('Logs', 'snapshot: ' + redact_text(str(error)), 'error')], 'cursor': {}}
    print(json.dumps(value, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    main()
