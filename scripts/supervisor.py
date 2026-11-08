"""Restart backend workers while native app launch support remains available.

A worker's terminal Popen result is the trigger for recovery. A separate gate
requires restoration of sharingd, release of the session lock and termination
of the previous worker's recorded children before another worker may start.
"""
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import threading
import time

from logs import BoundedLogs, open_append
from configuration import ROOT
from processes import process_birth
from state import write_state
from lifetime import emit_event
from sharingd import SharingdAgent
from workspace import cleanup as cleanup_workspace
from retention import track, retire, prune


def recovery_gate(previous=None):
    """Read-only ownership checks. Any uncertain observation defers recovery."""
    if previous is not None:
        path = Path(previous) / 'backend-children.json'
        if path.exists():
            record = json.loads(path.read_text())
            for birth in record.values():
                if not isinstance(birth, list) or len(birth) != 4:
                    raise ValueError('Invalid backend child identity')
                if process_birth(birth[0]) == tuple(birth):
                    return False, 'previous backend child still running'
    directory = Path.home() / 'Library/Caches/Mirra'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / 'session.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False, 'backend controller still owns sharingd'
        reply = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/com.apple.sharingd'],
                               capture_output=True, text=True, timeout=5)
        if reply.returncode:
            return False, 'sharingd restoration not yet observable'
        values = {}
        for line in reply.stdout.splitlines():
            key, separator, value = line.strip().partition(' = ')
            if separator:
                values.setdefault(key, value)
        if (values.get('path') != '/System/Library/LaunchAgents/com.apple.sharingd.plist'
                or values.get('program') != '/usr/libexec/sharingd'
                or 'DYLD_INSERT_LIBRARIES' in reply.stdout or 'MIRRA_KEYSTORE' in reply.stdout):
            return False, 'sharingd is not in its original configuration'
    return True, None


class NativeBackend:
    def __init__(self, config, directory, *, recover=False):
        self.config = config
        self.directory = Path(directory)
        self.recover = recover
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.process = None
        self.state = {'phase': 'pending', 'attempt': 0, 'ready': False,
                      'automatic_recovery': recover}
        self.error = None

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def publish(self, phase, **values):
        with self.lock:
            self.state.update(phase=phase, ready=phase == 'ready', **values)
            state = dict(self.state)
        write_state(self.directory / 'backend-status.json', state)
        emit_event({'NATIVE_BACKEND_STATE': state})

    def start(self):
        self.thread = threading.Thread(target=self._run, name='native-backend-supervisor', daemon=True)
        self.thread.start()
        return self

    def _read_worker(self, process, output):
        """Drain a pipe directly; rotated diagnostic files never carry status."""
        buffered = b''
        deadline = time.monotonic() + 30 + 3 * self.config.timeout
        ready_at = None
        requested_stop = False
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while process.poll() is None or selector.get_map():
                if (self.stop.is_set() or (ready_at is None and time.monotonic() > deadline)) and not requested_stop:
                    if process.poll() is None:
                        process.terminate()
                    requested_stop = True
                    self.publish('stopping_worker', reason='requested stop' if self.stop.is_set() else 'backend readiness deadline')
                for key, _ in selector.select(.2):
                    chunk = os.read(key.fd, 16384)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.write(chunk)
                    output.flush()
                    buffered += chunk
                    while b'\n' in buffered:
                        line, buffered = buffered.split(b'\n', 1)
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeError):
                            continue
                        if not isinstance(event, dict):
                            continue
                        if event.get('NATIVE_WORKSPACE_READY') and not requested_stop:
                            ready_at = time.monotonic()
                            self.publish('ready', reason=None, controller_pid=event.get('controller_pid'))
                        elif event.get('BACKEND_LOST'):
                            self.publish('restoring', reason='backend health failed')
                    if len(buffered) > 65536:
                        buffered = b''  # Invalid oversized diagnostic line, never a state event.
        return process.wait(), 0 if ready_at is None else time.monotonic() - ready_at

    def _run(self):
        previous = None
        delay = 2
        try:
            while not self.stop.is_set():
                try:
                    recovered = SharingdAgent.recover_abandoned()
                    if recovered:
                        emit_event({'ABANDONED_SHARINGD_RECOVERED': recovered})
                    allowed, reason = recovery_gate(previous)
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    allowed, reason = False, 'ownership observation deferred'
                if not allowed:
                    if self.snapshot().get('reason') != reason:
                        self.publish('waiting_for_cleanup', reason=reason)
                    if not self.recover:
                        raise RuntimeError(reason)
                    self.stop.wait(2)
                    continue
                if previous is not None:
                    # Only a terminal worker and a successful restoration gate
                    # authorize release of its temporary certificate and files.
                    workspace = cleanup_workspace(previous)
                    if workspace.get('reason') == 'lease_in_use':
                        self.publish('waiting_for_cleanup', reason='previous broker workspace is still in use')
                        self.stop.wait(2)
                        continue
                    retire(previous)
                    retention = prune(self.directory, 'backend', protected=(previous,))
                    if retention['removed'] or retention['deferred']:
                        emit_event({'BACKEND_RETENTION': retention})
                attempt = self.snapshot()['attempt'] + 1
                current = self.directory / f'backend-{attempt:06d}'
                current.mkdir(mode=0o700)
                track(current, 'backend')
                settings = current / 'config.json'
                write_state(settings, replace(self.config, runtime_directory=current).as_dict())
                shutil.copy2(self.directory / 'keystore.dylib', current / 'keystore.dylib')
                (current / 'keystore.dylib').chmod(0o600)
                self.publish('starting', attempt=attempt, directory=current.name,
                             reason=None, worker_pid=None, controller_pid=None,
                             worker_birth=None, exit_code=None, retry_seconds=None)
                limits = BoundedLogs(on_error=self.stop.set)
                try:
                    with open_append(current / 'backend.log', binary=True) as output:
                        limits.watch_stream(current / 'backend.log', output)
                        self.process = subprocess.Popen([sys.executable, '-E', '-s', '-B', str(ROOT / 'scripts/backend.py'),
                            '--config', str(settings), '--until-stopped', '--parent-lifeline'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                        self.publish('starting', worker_pid=self.process.pid, worker_birth=process_birth(self.process.pid))
                        code, healthy_seconds = self._read_worker(self.process, output)
                        self.process.stdin.close()
                        self.process.stdout.close()
                finally:
                    report = limits.close()
                    write_state(current / 'backend-log-limits.json', report)
                if report.get('error'):
                    raise RuntimeError('Backend diagnostic log maintenance failed')
                previous = current
                self.publish('worker_ended', exit_code=code, worker_pid=None, worker_birth=None)
                if self.stop.is_set() or not self.recover:
                    break
                if healthy_seconds >= 60:
                    delay = 2
                self.publish('retry_wait', retry_seconds=delay, reason='backend worker ended')
                self.stop.wait(delay)
                delay = min(delay * 2, 30)
            self.publish('stopped')
        except BaseException as error:
            self.error = str(error)
            self.publish('failed', reason=self.error)
        finally:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                # Never kill a restoration worker to make a retry appear ready.
                try:
                    self.process.wait(timeout=80)
                except subprocess.TimeoutExpired:
                    self.error = 'Backend restoration worker has not stopped'

    def close(self, timeout=85):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout)
            if self.thread.is_alive():
                self.error = 'Backend restoration is still running'
        result = self.snapshot()
        result['cleanup_error'] = self.error
        # Direct observations are authoritative even if a worker lost its final log.
        if self.process is not None and self.process.poll() is None:
            result['cleanup_error'] = 'Backend worker is still running'
        try:
            recovered = SharingdAgent.recover_abandoned()
            if recovered:
                result['abandoned_sharingd_recovered'] = recovered
            result['cleanup_verified'], result['cleanup_reason'] = recovery_gate(
                self.directory / result['directory'] if result.get('directory') else None)
            if result['cleanup_verified'] and not result['cleanup_error'] and result.get('directory'):
                current = self.directory / result['directory']
                result['workspace_cleanup'] = cleanup_workspace(current)
                if result['workspace_cleanup'].get('reason') != 'lease_in_use':
                    retire(current)
                else:
                    result['cleanup_verified'] = False
                    result['cleanup_reason'] = 'broker workspace is still in use'
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            result['cleanup_verified'] = False
            result['cleanup_reason'] = 'ownership observation deferred'
        return result
