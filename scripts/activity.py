"""Record actual operations and stream process output without changing replies."""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from datetime import date, datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import threading
import time

from redaction import redact_line, redact_text, redact_value
from logfiles import dated_name, migrate_legacy, prune

_sink = ContextVar('mirra_activity_sink', default=None)


def enabled():
    return _sink.get() is not None


def emit(source, message, level='info', **fields):
    sink = _sink.get()
    if sink is None:
        return
    value = {'timestamp': datetime.now(timezone.utc).isoformat(), 'source': source,
             'level': level, 'message': redact_text(message)}
    if fields:
        value['fields'] = redact_value(fields)
    try:
        sink(value)
    except (OSError, ValueError):
        # Diagnostic storage never authorizes or aborts a connection operation.
        pass


@contextmanager
def capture(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def poll_errors(sink):
    """Keep actual errors/stderr from routine polls without repeated success dumps."""
    def record(value):
        if value.get('level') in ('error', 'warning') or value.get('source', '').endswith('stderr'):
            sink(value)
    return record


class FileLog:
    def __init__(self, base, name='setup.log', limit=2 * 1024 * 1024, clock=None):
        self.base = Path(base).absolute()
        self.name = name
        self.limit = limit
        self.lock = threading.Lock()
        self.clock = clock or date.today
        self.cleaned_day = None

    @property
    def path(self):
        return self.base / dated_name(self.name, self.clock())

    def directory(self):
        missing = []
        ancestor = self.base
        while not ancestor.exists() and not ancestor.is_symlink():
            missing.append(ancestor)
            ancestor = ancestor.parent
        for path in reversed(missing):
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                pass
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Activity parent directory must be private')
        info = self.base.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Activity log directory must be private')
        return os.open(self.base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def clean(self, folder, day):
        if self.cleaned_day == day:
            return []
        migrate_legacy(folder)
        removed = prune(folder, day)
        self.cleaned_day = day
        return removed

    def maintain(self):
        day = self.clock()
        if self.cleaned_day == day:
            return []
        folder = self.directory()
        try:
            with self.lock:
                return self.clean(folder, day)
        finally:
            os.close(folder)

    def __call__(self, value):
        day = self.clock()
        name = dated_name(self.name, day)
        folder = self.directory()
        try:
            with self.lock:
                self.clean(folder, day)
            fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=folder)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise ValueError('Activity log must be private')
                data = (json.dumps(value, ensure_ascii=False) + '\n').encode()
                with self.lock:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    info = os.fstat(fd)
                    if info.st_size + len(data) > self.limit:
                        old = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
                        try:
                            tail = os.pread(old, self.limit // 2, max(0, info.st_size - self.limit // 2))
                        finally:
                            os.close(old)
                        backup = os.open(name + '.1', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=folder)
                        try:
                            saved = os.fstat(backup)
                            if not stat.S_ISREG(saved.st_mode) or saved.st_uid != os.getuid() or saved.st_mode & 0o077:
                                raise ValueError('Activity backup must be a private regular file')
                            os.ftruncate(backup, 0)
                            os.write(backup, tail)
                        finally:
                            os.close(backup)
                        os.ftruncate(fd, 0)
                    position = 0
                    while position < len(data):
                        position += os.write(fd, data[position:])
            finally:
                os.close(fd)
        finally:
            os.close(folder)


@contextmanager
def operation(source, name, **fields):
    start = time.monotonic()
    emit(source, name + ' begin', **fields)
    try:
        yield
    except BaseException as error:
        emit(source, name + ' failed', 'error', error_type=type(error).__name__,
             error=str(error), elapsed_ms=round((time.monotonic() - start) * 1000), **fields)
        raise
    else:
        emit(source, name + ' complete', elapsed_ms=round((time.monotonic() - start) * 1000), **fields)


def ssh_command(arguments):
    arguments = list(arguments)
    if enabled() and Path(arguments[0]).name == 'ssh' and '-v' not in arguments:
        arguments.insert(1, '-v')
    return arguments


def run(arguments, *, source='Process', record_stdout=True, **options):
    """Like subprocess.run; when enabled, tee output into the live log file.

    Input is counted, never logged. Return bytes and timeout/check semantics
    are retained, including binary certificate downloads. With no logger, the
    original runner remains unchanged (also preserving CLI test fixtures).
    """
    if not enabled():
        return subprocess.run(arguments, **options)
    options = dict(options)
    input_data = options.pop('input', None)
    timeout = options.pop('timeout', None)
    check = options.pop('check', False)
    options.pop('capture_output', None)
    text = options.pop('text', False) or options.pop('universal_newlines', False)
    encoding = options.pop('encoding', None) or 'utf-8'
    errors = options.pop('errors', None) or 'strict'
    if isinstance(input_data, str):
        input_data = input_data.encode(encoding, errors)
    command = ssh_command(arguments) if source == 'SSH' else list(arguments)
    emit(source, '$ ' + shlex.join(command), input_bytes=len(input_data or b''))
    started = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, **options)
    emit(source, 'process started', pid=process.pid)
    output = {'stdout': bytearray(), 'stderr': bytearray()}

    def drain(stream, kind):
        pending = b''
        try:
            while True:
                data = os.read(stream.fileno(), 16384)
                if not data:
                    break
                output[kind].extend(data)
                if kind == 'stdout' and not record_stdout:
                    continue
                pending += data
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    if line:
                        emit(source + ' ' + kind, redact_line(line), 'debug' if kind == 'stderr' else 'info')
                if len(pending) > 8192:
                    emit(source + ' ' + kind, '<oversized output line: ' + str(len(pending)) + ' bytes>', 'debug')
                    pending = b''
            if pending:
                emit(source + ' ' + kind, redact_line(pending), 'debug' if kind == 'stderr' else 'info')
        finally:
            stream.close()

    def feed():
        try:
            if input_data:
                process.stdin.write(input_data)
                process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    threads = [threading.Thread(target=copy_context().run, args=(drain, process.stdout, 'stdout'), daemon=True),
               threading.Thread(target=copy_context().run, args=(drain, process.stderr, 'stderr'), daemon=True),
               threading.Thread(target=feed, daemon=True)]
    for thread in threads:
        thread.start()
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        for thread in threads:
            thread.join(2)
        emit(source, 'process timed out', 'error', pid=process.pid, timeout_seconds=timeout)
        raise subprocess.TimeoutExpired(command, timeout, bytes(output['stdout']), bytes(output['stderr']))
    for thread in threads:
        thread.join(2)
    emit(source, 'process exited', 'info' if code == 0 else 'error', pid=process.pid,
         exit_code=code, elapsed_ms=round((time.monotonic() - started) * 1000),
         stdout_bytes=len(output['stdout']), stderr_bytes=len(output['stderr']))
    stdout, stderr = bytes(output['stdout']), bytes(output['stderr'])
    if text:
        stdout, stderr = stdout.decode(encoding, errors), stderr.decode(encoding, errors)
    if check and code:
        raise subprocess.CalledProcessError(code, command, stdout, stderr)
    return subprocess.CompletedProcess(command, code, stdout, stderr)
