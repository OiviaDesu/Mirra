"""Select the verified decoder route and own it for native support's lifetime."""
from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tempfile
import time

import decoder_route as route
from activity import FileLog, capture, emit
from processes import process_birth
from state import write_state
from lifetime import emit_event
from redaction import redact_text, redact_value

INSPECTED_BUILD = '25G332'


def host_profile():
    def read(*arguments):
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=5, check=True)
        return result.stdout.strip()
    return {'model': read('/usr/sbin/sysctl', '-n', 'hw.model'),
            'architecture': platform.machine(),
            'build': read('/usr/bin/sw_vers', '-buildVersion')}


def select_route(mode, host):
    """Select by inspected ABI; auto filters the actual stream in the hook."""
    if mode not in ('auto', 'normal', 'no_fls'):
        raise ValueError('Unknown decoder route setting')
    compatible = host.get('architecture') == 'x86_64' and host.get('build') == INSPECTED_BUILD
    enabled = mode == 'no_fls' or (mode == 'auto' and compatible)
    if enabled and not compatible:
        raise RuntimeError('The decoder route has been inspected for Intel macOS build 25G332 only')
    reason = ('explicit decoder route' if mode != 'auto' else
              'automatic Display P3/FLS compatibility' if enabled else 'normal decoder for this host')
    return {'requested': mode, 'mode': 'no_fls' if enabled else 'normal',
            'enabled': enabled, 'policy': 'fls_p3' if mode == 'auto' else 'all_h264',
            'host': dict(host), 'reason': reason}


def read_json(path):
    route.private(path)
    return json.loads(Path(path).read_text())


class DecoderRoute:
    def __init__(self, root, directory, *, mode='auto', stop=None, host=None, log_limits=None):
        self.root = Path(root)
        self.directory = Path(directory)
        self.stop = stop
        self.log_limits = log_limits
        self.decision = select_route(mode, host if host is not None else host_profile())
        self.log = FileLog(Path.home() / 'Library/Application Support/Mirra/logs', name='native.log')
        self.stage = self.job = self.guardian = None
        self.lock = None
        self.positions = {}
        self.events = deque(maxlen=64)
        self.event_count = 0
        self.ready_pids = {}
        self.ready = False
        self.pid = None
        self.metadata = {}
        self.observation = 'starting'

    def record(self, message, level='info', **fields):
        with capture(self.log):
            emit('Decoder', message, level, **fields)
        # service.log is already followed by the compact viewer, including
        # startup steps that happened before the Mirra window opened.
        emit_event({'source': 'Decoder', 'message': redact_text(message), 'level': level,
                    'timestamp': datetime.now(timezone.utc).isoformat(), 'fields': redact_value(fields)})

    def save(self, **extra):
        value = dict(self.decision, ready=self.ready, hook_pid=self.pid,
                     native_event_count=self.event_count, native_decoder_events=list(self.events),
                     **self.metadata, **extra)
        if self.stage is not None:
            value['stage'] = str(self.stage)
        write_state(self.directory / 'decoder-route.json', value)
        return value

    def interrupted(self):
        return self.stop is not None and self.stop.is_set()

    def start(self):
        self.record('Selected decoder route', **self.decision)
        if not self.decision['enabled']:
            # Explicit normal mode does not inspect or restart AVConference.
            # A journal from an interrupted diagnostic still needs restoration.
            if route.JOURNAL.exists():
                self.record('Recovering an earlier decoder session')
                descriptor = route.acquire()
                os.close(descriptor)
                self.record('Earlier decoder session restored')
            self.ready = True
            return self.save()
        self.lock = route.acquire()
        route.STAGES.mkdir(exist_ok=True)
        if not stat.S_ISDIR(route.STAGES.lstat().st_mode):
            raise RuntimeError('AVConference staging directory is unavailable')
        removed = route.prune_stages()
        if removed:
            self.record('Removed expired decoder logs', directories=removed, retention_days=7)
        self.stage = Path(tempfile.mkdtemp(prefix='mirra-decoder-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-',
                                          dir=route.STAGES))
        self.stage.chmod(0o700)
        self.job = route.DecoderJob(self.stage)
        self.job.require_idle()
        self.record('Verified that original AVConference is idle')
        self.metadata = self.job.prepare(self.root / 'build/decoder-route.dylib',
                                         policy=self.decision['policy'])
        route.write_json(self.stage / 'metadata.json', self.metadata)
        birth = process_birth(os.getpid())
        if birth is None:
            raise RuntimeError('Native support process identity could not be verified')
        owner = {'stage': str(self.stage), 'owner_birth': birth, 'deadline_monotonic': None,
                 'session': 'native support'}
        route.write_json(self.stage / 'owner.json', owner)
        route.write_json(route.JOURNAL, owner)
        self.spawn_guardian()
        deadline = time.monotonic() + 12
        while not (self.stage / 'guardian-ready.json').exists():
            if self.interrupted() or self.guardian.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Restoration guardian could not start; no decoder change made')
            time.sleep(.1)
        owner.update(read_json(self.stage / 'guardian-ready.json'))
        if not route.owner_alive(owner['guardian_birth']):
            raise RuntimeError('Restoration guardian identity could not be verified')
        route.write_json(route.JOURNAL, owner)
        self.record('Restoration guardian started', guardian_birth=owner['guardian_birth'])
        try:
            with route.transaction(self.stage):
                if self.interrupted() or self.guardian.poll() is not None or (self.stage / 'restored.json').exists():
                    raise RuntimeError('Native support stopped before decoder startup')
                self.job.start()
        except route.DecoderUnloadError as error:
            self.record('AVConference unload failed', 'error',
                        launchctl_exit=error.returncode, launchctl_stderr=error.stderr)
            if self.decision['requested'] != 'auto':
                raise
            return self.use_normal_after_restore(error)
        self.record('Started AVConference with the decoder route')
        deadline = time.monotonic() + 15
        while True:
            self.drain_native()
            if self.interrupted() or self.guardian.poll() is not None:
                raise RuntimeError('Decoder startup interrupted; restoration requested')
            current = self.job.state()
            pid = current.get('pid')
            if (self.job.owned(current) and pid in self.ready_pids
                    and process_birth(pid) == self.ready_pids[pid]):
                self.pid = pid
                break
            if current and not self.job.owned(current):
                raise RuntimeError('AVConference changed during decoder startup')
            if time.monotonic() >= deadline:
                raise RuntimeError('AVConference did not load the decoder hook')
            time.sleep(.1)
        if self.log_limits is not None:
            for fd, name in ((1, 'stdout.log'), (2, 'stderr.log')):
                self.log_limits.watch_native(self.stage / name, self.pid, fd)
        self.ready = True
        self.observation = 'ready'
        self.record('Decoder route is ready', pid=self.pid, **self.metadata)
        return self.save()

    def use_normal_after_restore(self, error):
        # A nonzero bootout status does not establish whether it changed the
        # job. Complete and verify restoration before accepting a normal route.
        restored = self.close()
        if (not restored.get('cleanup_verified') or not restored.get('original_avc_restored')
                or route.JOURNAL.exists() or self.interrupted()):
            raise RuntimeError('Automatic decoder fallback requires verified AVConference restoration') from error
        fallback = {'reason': 'avconference_unload_failed', 'launchctl_exit': error.returncode,
                    'original_avc_restored': True, 'stage': str(self.stage)}
        self.metadata['decoder_fallback'] = fallback
        self.stage = self.job = self.guardian = None
        self.decision.update(mode='normal', enabled=False,
                             reason='normal decoder after automatic compatibility setup was unavailable')
        self.ready = True
        self.observation = 'ready'
        self.record('Continuing with the normal decoder after verified restoration', 'warning',
                    compatibility_hook_active=False, **fallback)
        return self.save()

    def spawn_guardian(self):
        with (self.stage / 'guardian.log').open('xb') as output:
            (self.stage / 'guardian.log').chmod(0o600)
            self.guardian = subprocess.Popen([sys.executable, '-E', '-s', '-B',
                str(self.root / 'scripts/decoder_route.py'), '--guardian', str(self.stage), str(self.lock)],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                pass_fds=(self.lock,), start_new_session=True)

    def drain_native(self):
        if self.stage is None:
            return
        changed = False
        for name in ('stdout.log', 'stderr.log'):
            path = self.stage / name
            if not path.exists():
                continue
            route.private(path)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                size = os.fstat(descriptor).st_size
                offset = self.positions.get(name, 0)
                if offset > size:
                    offset = 0
                raw = os.pread(descriptor, min(65536, max(0, size - offset)), offset)
                complete, separator, _ = raw.rpartition(b'\n')
                if not separator:
                    continue
                self.positions[name] = offset + len(complete) + 1
                for line in complete.decode('utf-8', errors='replace').splitlines():
                    if not line.startswith(('MIRRA_DECODER_ROUTE ', 'MIRRA_DECODER_HOOK_READY ')):
                        continue
                    fields = {key: int(value) for key, value in re.findall(r'([a-z_]+)=(-?\d+)', line)}
                    self.events.append(dict(message=line[:4096], **fields))
                    self.event_count += 1
                    changed = True
                    if line.startswith('MIRRA_DECODER_HOOK_READY ') and fields.get('version') == 1:
                        pid = fields.get('pid')
                        birth = process_birth(pid) if type(pid) is int and pid > 0 else None
                        if birth is not None:
                            self.ready_pids[pid] = birth
                    self.record(line[:4096], **fields)
            finally:
                os.close(descriptor)
        if changed:
            self.save()

    def availability(self, observation, pid=None):
        ready = observation in ('ready', 'idle')
        if (observation, pid, ready) == (self.observation, self.pid, self.ready):
            return
        self.observation, self.pid, self.ready = observation, pid, ready
        messages = {'ready': 'AVConference loaded the decoder route',
                    'idle': 'AVConference is idle; decoder support remains registered',
                    'starting': 'Waiting for AVConference to load the decoder route',
                    'unknown': 'Decoder observation deferred; support remains registered'}
        self.record(messages[observation], pid=pid)
        self.save(observation=observation)

    def poll(self):
        if not self.decision['enabled']:
            return
        if self.guardian.poll() is not None or (self.stage / 'restored.json').exists():
            raise RuntimeError('The decoder guardian ended before native support stopped')
        try:
            self.drain_native()
            current = self.job.state()
            pid = current.get('pid')
            birth = process_birth(pid) if type(pid) is int and pid > 0 else None
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            self.availability('unknown')
            return
        if not current:
            self.availability('unknown')
            return
        if not self.job.owned(current):
            raise RuntimeError('AVConference changed to another configuration')
        # Launchd may leave the registered service idle or launch a new process
        # on demand. Keep the bridge and registration intact while its fresh
        # hook marker arrives; stale PIDs and unavailable observations are not
        # evidence that the entire native support session must be torn down.
        if pid is None and current.get('state') in ('not running', 'exited'):
            self.availability('idle')
        elif birth is None or self.ready_pids.get(pid) != birth:
            self.availability('starting', pid)
        else:
            self.availability('ready', pid)

    def close(self):
        self.ready = False
        result = {'enabled': self.decision['enabled'], 'cleanup_verified': False, 'original_avc_restored': None}
        try:
            if self.stage is None:
                result.update(cleanup_verified=True, avc_change_armed=False)
                return result
            result['avc_change_armed'] = (self.stage / 'armed.json').exists()
            self.record('Restoring original AVConference')
            route.write_json(self.stage / 'stop.json', {'stop': True})
            if self.guardian is not None and self.guardian.poll() is None:
                try:
                    self.guardian.wait(timeout=40)
                except subprocess.TimeoutExpired:
                    # The guardian retains its lock and restoration opportunity.
                    result.update(original_avc_restored=False, restoration_pending=True)
                    return result
            restored = self.stage / 'restored.json'
            if not restored.exists() or not read_json(restored).get('original_avc_restored'):
                # Also covers a guardian failure before or during startup.
                with route.transaction(self.stage):
                    current = self.job.restore()
                    route.write_json(restored, {'original_avc_restored': True, 'state': current,
                                                'recovered_by_supervisor': True})
                    route.clear_journal(self.stage)
            result.update(read_json(restored))
            current = self.job.state()
            if not self.job.original(current):
                raise RuntimeError('Original AVConference restoration could not be verified')
            self.drain_native()
            result['cleanup_verified'] = True
            self.record('Original AVConference restored and verified')
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            result.update(original_avc_restored=False, error=str(error))
            self.record('Decoder restoration remains pending', 'error', error_type=type(error).__name__, error=str(error))
        finally:
            if self.lock is not None:
                os.close(self.lock)
                self.lock = None
            self.save(restoration=result)
        return result
