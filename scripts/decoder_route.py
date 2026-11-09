#!/usr/bin/env python3
"""Own one temporary AVConference job, with a separate restoration guardian."""
import fcntl
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from processes import process_birth

ORIGINAL = Path('/System/Library/LaunchAgents/com.apple.avconferenced.plist')
PROGRAM = '/usr/libexec/avconferenced'
LABEL = 'com.apple.videoconference.camera'
STAGES = Path('/private/var/tmp/AVConference')
CACHE = Path.home() / 'Library/Caches/Mirra'
JOURNAL = CACHE / 'decoder-route.json'
STAGE_PATTERN = re.compile(r'mirra-decoder-[0-9]{8}-[0-9]{6}-[a-z0-9_]+')


def private(path, kind=stat.S_ISREG):
    info = Path(path).lstat()
    if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('Decoder route path must be private and owned by this user')
    return info


def write_json(path, value):
    path = Path(path)
    descriptor, name = tempfile.mkstemp(prefix='.decoder-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(value, output)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ctl(*arguments):
    return subprocess.run(['/bin/launchctl', *arguments], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=12)


def snapshot(reply):
    if reply.returncode:
        if 'Could not find service' in reply.stderr:
            return {}
        raise RuntimeError('AVConference state could not be inspected')
    values = {'injected': 'DYLD_INSERT_LIBRARIES' in reply.stdout or 'MIRRA_NO_FLS' in reply.stdout,
              'endpoints': {}}
    endpoints = False
    depth = 0
    endpoint = None
    for line in reply.stdout.splitlines():
        if line.startswith('\t') and not line.startswith('\t\t'):
            key, sep, value = line[1:].partition(' = ')
            if sep and key in ('path', 'program', 'pid', 'state', 'active count'):
                values[key] = int(value) if value.isdecimal() else value
        stripped = line.strip()
        if stripped == 'endpoints = {':
            endpoints, depth = True, 1
            continue
        if endpoints:
            match = re.fullmatch(r'"([^"]+)" = \{', stripped) if depth == 1 else None
            if match:
                endpoint = match[1]
            elif endpoint:
                match = re.fullmatch(r'active = ([0-9]+)', stripped)
                if match:
                    values['endpoints'][endpoint] = int(match[1])
                    endpoint = None
            depth += line.count('{') - line.count('}')
            if depth <= 0:
                endpoints = False
    return values


class DecoderJob:
    def __init__(self, stage, command=ctl):
        self.stage = Path(stage)
        self.command = command
        self.domain = f'gui/{os.getuid()}'
        self.target = self.domain + '/' + LABEL
        self.plist = self.stage / 'job.plist'
        self.wrapper = self.stage / 'wrapper'

    def state(self):
        return snapshot(self.command('print', self.target))

    @staticmethod
    def original(value):
        return (value.get('path') == str(ORIGINAL) and value.get('program') == PROGRAM
                and not value.get('injected'))

    def owned(self, value):
        return value.get('path') == str(self.plist) and value.get('program') == str(self.wrapper)

    def wait_removed(self, path, timeout=8):
        deadline = time.monotonic() + timeout
        while True:
            value = self.state()
            if not value or value.get('path') != str(path):
                return value
            if time.monotonic() >= deadline:
                raise RuntimeError('AVConference is still unloading; restoration remains pending')
            time.sleep(.1)

    def wait_original(self, timeout=8):
        deadline = time.monotonic() + timeout
        while True:
            value = self.state()
            if value:
                if self.original(value):
                    return value
                raise RuntimeError('AVConference belongs to another configuration')
            if time.monotonic() >= deadline:
                raise RuntimeError('Original AVConference registration remains pending')
            time.sleep(.1)

    def require_idle(self):
        value = self.state()
        if not self.original(value):
            raise RuntimeError('AVConference already has another configuration; stop that session first')
        if value['endpoints'].get('com.apple.videoconference.avconference') != 0:
            raise RuntimeError('AVConference is active; quit iPhone Mirroring and other video calls first')
        return value

    def prepare(self, library, *, policy='all_h264'):
        if policy not in ('all_h264', 'fls_p3'):
            raise ValueError('Unknown decoder compatibility policy')
        private(self.stage, stat.S_ISDIR)
        config = plistlib.loads(ORIGINAL.read_bytes())
        if config.get('Label') != LABEL:
            raise RuntimeError('Unexpected Apple AVConference service definition')
        original_args = config.get('ProgramArguments', [PROGRAM])
        if not isinstance(original_args, list) or not original_args or original_args[0] != PROGRAM:
            raise RuntimeError('Unexpected Apple AVConference arguments')
        library = Path(library)
        subprocess.run(['/usr/bin/codesign', '--verify', str(library)], check=True,
                       capture_output=True, timeout=10)
        copied = self.stage / 'decoder-route.dylib'
        shutil.copyfile(library, copied)
        copied.chmod(0o600)
        wrapper = ('#!/bin/sh\nexport DYLD_INSERT_LIBRARIES=' + shlex.quote(str(copied)) +
                   '\nexec ' + shlex.join(original_args) + '\n')
        self.wrapper.write_text(wrapper)
        self.wrapper.chmod(0o700)
        config.update(Program=str(self.wrapper), ProgramArguments=[str(self.wrapper)],
                      RunAtLoad=True, EnablePressuredExit=False, Umask=0o077,
                      StandardOutPath=str(self.stage / 'stdout.log'),
                      StandardErrorPath=str(self.stage / 'stderr.log'))
        config.setdefault('EnvironmentVariables', {})['MIRRA_NO_FLS'] = '1'
        config['EnvironmentVariables']['MIRRA_DECODER_POLICY'] = policy
        for name in ('stdout.log', 'stderr.log'):
            (self.stage / name).touch(mode=0o600, exist_ok=False)
        self.plist.write_bytes(plistlib.dumps(config))
        self.plist.chmod(0o600)
        return {'decoder_hook_sha256': hashlib.sha256(copied.read_bytes()).hexdigest(),
                'original_plist_sha256': hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()}

    def start(self):
        before = self.require_idle()
        # The guardian sees this before a bootout can succeed or time out.
        write_json(self.stage / 'armed.json', {'armed': True, 'original_state': before,
                    'original_was_running': bool(before.get('pid') and before.get('state') == 'running')})
        reply = self.command('bootout', self.target)
        if reply.returncode:
            raise RuntimeError('AVConference could not be unloaded for this session')
        if self.wait_removed(ORIGINAL):
            raise RuntimeError('AVConference changed during startup; no replacement attempted')
        reply = self.command('bootstrap', self.domain, str(self.plist))
        if reply.returncode:
            raise RuntimeError('Mirra AVConference registration failed')

    def restore(self):
        current = self.state()
        if current and self.owned(current):
            reply = self.command('bootout', self.target)
            if reply.returncode:
                raise RuntimeError('The owned decoder route could not be unloaded')
            current = self.wait_removed(self.plist)
        if current and not self.original(current):
            raise RuntimeError('AVConference ownership changed; another configuration was preserved')
        if not current:
            reply = self.command('bootstrap', self.domain, str(ORIGINAL))
            if reply.returncode:
                raise RuntimeError('Original AVConference could not be registered')
        current = self.wait_original()
        armed = self.stage / 'armed.json'
        if armed.exists():
            private(armed)
            previous = json.loads(armed.read_text())
            if previous.get('original_was_running') and not current.get('pid'):
                # Restore demand state as well as registration. Never kill an
                # already-running original service, or force a hardware decoder.
                reply = self.command('kickstart', self.target)
                if reply.returncode:
                    raise RuntimeError('Original AVConference could not resume its previous running state')
                deadline = time.monotonic() + 8
                while True:
                    current = self.wait_original()
                    if current.get('pid') and current.get('state') == 'running':
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Original AVConference restart remains pending')
                    time.sleep(.1)
        return current


def valid_stage(stage):
    stage = Path(stage)
    if stage.parent != STAGES or not STAGE_PATTERN.fullmatch(stage.name):
        raise RuntimeError('Unrecognized decoder route directory')
    private(stage, stat.S_ISDIR)
    return stage


def owner_alive(birth):
    if not isinstance(birth, (list, tuple)) or len(birth) != 4 or birth[1] != os.getuid():
        raise RuntimeError('Invalid decoder route owner identity')
    try:
        return process_birth(birth[0]) == tuple(birth)
    except OSError:
        # An observation error is not evidence that the owner died.
        return True


@contextmanager
def transaction(stage):
    descriptor = os.open(Path(stage) / 'transaction.lock',
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('Decoder transaction lock must be private')
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def clear_journal(stage):
    if JOURNAL.exists():
        private(JOURNAL)
        value = json.loads(JOURNAL.read_text())
        if value.get('stage') == str(stage):
            JOURNAL.unlink()


def recover_abandoned():
    if not JOURNAL.exists():
        return None
    private(JOURNAL)
    value = json.loads(JOURNAL.read_text())
    stage = valid_stage(value['stage'])
    guardian = value.get('guardian_birth')
    if guardian and owner_alive(guardian):
        raise RuntimeError('An earlier decoder session is still restoring AVConference')
    if owner_alive(value['owner_birth']):
        raise RuntimeError('Another decoder session is still running')
    with transaction(stage):
        restored = DecoderJob(stage).restore()
        write_json(stage / 'restored.json', {'original_avc_restored': True, 'state': restored, 'recovered': True})
        clear_journal(stage)
    return restored


def acquire():
    CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    private(CACHE, stat.S_ISDIR)
    descriptor = os.open(CACHE / 'decoder-route.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('Decoder route lock must be private')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        recover_abandoned()
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def guardian(stage, descriptor):
    stage = valid_stage(stage)
    private(stage / 'owner.json')
    value = json.loads((stage / 'owner.json').read_text())
    expected = private(CACHE / 'decoder-route.lock')
    held = os.fstat(descriptor)
    if (held.st_dev, held.st_ino) != (expected.st_dev, expected.st_ino):
        raise RuntimeError('Guardian does not own the decoder route lock')
    birth = process_birth(os.getpid())
    write_json(stage / 'guardian-ready.json', {'guardian_birth': birth})
    job = DecoderJob(stage)
    try:
        # Production sessions follow the supervisor's lifetime. A diagnostic
        # caller can still supply an explicit deadline; there is no fixed TTL.
        while ((value.get('deadline_monotonic') is None or time.monotonic() < value['deadline_monotonic']) and
               not (stage / 'stop.json').exists() and owner_alive(value['owner_birth']) and
               (not value.get('gui_birth') or owner_alive(value['gui_birth']))):
            time.sleep(.2)
        with transaction(stage):
            state = job.restore()
            result = {'original_avc_restored': True, 'state': state}
            write_json(stage / 'restored.json', result)
            clear_journal(stage)
        return 0
    except Exception as error:
        write_json(stage / 'restored.json', {'original_avc_restored': False,
                                          'error_type': type(error).__name__, 'error': str(error)})
        return 1
    finally:
        os.close(descriptor)


def prune_stages(protected=(), days=7):
    """Delete only our expired, private directories after restoration."""
    removed = []
    cutoff = time.time() - days * 86400
    for path in STAGES.iterdir():
        if path in protected or not STAGE_PATTERN.fullmatch(path.name):
            continue
        try:
            info = private(path, stat.S_ISDIR)
            if info.st_mtime >= cutoff:
                continue
            private(path / 'restored.json')
            if json.loads((path / 'restored.json').read_text()).get('original_avc_restored') is not True:
                continue
            shutil.rmtree(path)
            removed.append(path.name)
        except (OSError, ValueError, RuntimeError):
            continue
    return removed


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--guardian':
        raise SystemExit(guardian(sys.argv[2], int(sys.argv[3])))
    raise SystemExit('This restoration helper is started by Mirra native support.')
