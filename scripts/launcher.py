"""Scoped, reversible CoreServicesUIAgent support for ordinary app launches."""
import fcntl
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import threading
import time
from ownership import LaunchOwnerJournal,cleanup_abandoned_socket
from processes import NativeAppObserver

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = Path('/System/Library/LaunchAgents/com.apple.coreservices.uiagent.plist')
AGENT = '/System/Library/CoreServices/CoreServicesUIAgent.app/Contents/MacOS/CoreServicesUIAgent'


class NativeLaunchAgent:
    def __init__(self, directory, environment, stop=None, *, runtime_root=None):
        self.directory = Path(directory)
        self.environment = dict(environment)
        self.stop = stop if stop is not None else threading.Event()
        self.domain = f'gui/{os.getuid()}'
        self.target = self.domain + '/com.apple.coreservices.uiagent'
        self.settings = self.directory / 'native-launch-settings.json'
        self.plist = self.directory / 'native-launch-agent.plist'
        self.log = self.directory / 'native-launch-agent.stderr.log'
        self.changed = False
        self.lock = None
        self.journal=LaunchOwnerJournal(ROOT/'runtime' if runtime_root is None else runtime_root)
        self.journal_owned=False

    def _recover_under_lock(self):
        previous=self.journal.abandoned()
        if previous is None:return None
        directory=Path(previous['directory'])
        old=NativeLaunchAgent(directory,{},runtime_root=self.journal.runtime_root)
        old.changed=True
        # close() accepts only the journal's exact plist or Apple's original job.
        restored=old.close()
        observer=NativeAppObserver('/System/Applications/iPhone Mirroring.app/Contents/MacOS/iPhone Mirroring',
                                   directory/'sockets.dylib')
        observer.terminate_owned()
        cleanup_abandoned_socket(previous.get('helper_socket'))
        self.journal.clear(directory)
        return {'directory':str(directory),'original_agent_restored':restored is not None}

    @classmethod
    def recover_abandoned(cls, *, runtime_root=None):
        root=ROOT/'runtime' if runtime_root is None else Path(runtime_root)
        directory=Path.home()/'Library/Caches/Mirra'
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        with (directory/'native-launch-agent.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return cls(root/'native-recovery',{},runtime_root=root)._recover_under_lock()

    def command(self, *args):
        return subprocess.run(['launchctl', *args], capture_output=True, text=True, timeout=12)

    def state(self):
        reply = self.command('print', self.target)
        if reply.returncode:
            # Missing is distinct from a failed observation of a live job.
            if 'Could not find service' in reply.stderr:
                return {}
            raise RuntimeError('Cannot inspect CoreServicesUIAgent: ' + reply.stderr.strip())
        values = {'injected': 'DYLD_INSERT_LIBRARIES' in reply.stdout}
        for line in reply.stdout.splitlines():
            key, separator, value = line.strip().partition(' = ')
            if separator and key in ('program', 'path', 'pid', 'state'):
                values.setdefault(key, value)
        return values

    def wait_unloaded(self, path, timeout=8):
        """bootout may return before print stops exposing the exiting job.

        Observe that exact job until it disappears or changes. Never repeat a
        mutation because a query timed out, failed, or still shows the old job.
        """
        deadline = time.monotonic() + timeout
        while True:
            current = self.state()
            if not current or current.get('path') != str(path):
                return current
            if time.monotonic() >= deadline:
                raise RuntimeError('CoreServicesUIAgent is still unloading; restoration remains pending')
            time.sleep(.2)

    def wait_original(self, timeout=8):
        deadline = time.monotonic() + timeout
        while True:
            current = self.state()
            if current:
                if (current.get('path') == str(ORIGINAL) and current.get('program') == AGENT
                        and not current.get('injected')):
                    return current
                raise RuntimeError('Original CoreServicesUIAgent restoration could not be verified')
            if time.monotonic() >= deadline:
                raise RuntimeError('Original CoreServicesUIAgent registration is still pending')
            time.sleep(.2)

    def start(self):
        directory = Path.home() / 'Library/Caches/Mirra'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = (directory / 'native-launch-agent.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._recover_under_lock()
            before = self.state()
            if before.get('program') != AGENT or before.get('injected'):
                raise RuntimeError('CoreServicesUIAgent has an unexpected configuration; no change made')
            library = self.directory / 'launch.dylib'
            shutil.copy2(ROOT / 'build/launch.dylib', library)
            library.chmod(0o600)
            self.settings.write_text(json.dumps(self.environment) + '\n')
            self.settings.chmod(0o600)
            config = plistlib.loads(ORIGINAL.read_bytes())
            config.update(RunAtLoad=True, EnablePressuredExit=False,
                Umask=0o077,
                EnvironmentVariables={'DYLD_INSERT_LIBRARIES': str(library),
                                      'MIRRA_LAUNCH_SETTINGS': str(self.settings)},
                StandardOutPath=str(self.directory / 'native-launch-agent.stdout.log'),
                StandardErrorPath=str(self.log))
            for name in ('StandardOutPath', 'StandardErrorPath'):
                Path(config[name]).touch(mode=0o600, exist_ok=False)
            self.plist.write_bytes(plistlib.dumps(config))
            self.plist.chmod(0o600)
            self.journal.publish(self.directory,self.environment)
            self.journal_owned=True
            self.changed = True  # bootout can succeed before a timeout is reported.
            reply = self.command('bootout', self.target)
            if reply.returncode:
                raise RuntimeError('CoreServicesUIAgent bootout failed: ' + reply.stderr.strip())
            if self.wait_unloaded(before['path']):
                raise RuntimeError('CoreServicesUIAgent changed during startup; no replacement attempted')
            reply = self.command('bootstrap', self.domain, str(self.plist))
            if reply.returncode:
                raise RuntimeError('CoreServicesUIAgent bootstrap failed: ' + reply.stderr.strip())
            deadline = time.monotonic() + 16
            while time.monotonic() < deadline and not self.stop.is_set():
                state = self.state()
                log = self.log.read_text(errors='replace') if self.log.exists() else ''
                if state.get('pid') and f'MIRRA_LAUNCH_ADAPTER_READY pid={state["pid"]}' in log:
                    return state
                self.stop.wait(.2)
            raise RuntimeError('CoreServicesUIAgent did not load its adapter, or startup was interrupted')
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            settings_error = None
            if self.settings.exists():
                try:
                    self.settings.rename(self.settings.with_suffix('.disabled'))
                except OSError as error:
                    # A file cleanup failure must not skip restoration of the
                    # system agent. Unloading it also disables the launch hook.
                    settings_error = str(error)
            if not self.changed:
                return None
            current = self.state()
            if current and current.get('path') == str(self.plist):
                reply = self.command('bootout', self.target)
                if reply.returncode:
                    raise RuntimeError('Cannot unload the owned launch adapter: ' + reply.stderr.strip())
                current = self.wait_unloaded(self.plist)
            if current and (current.get('path') != str(ORIGINAL)
                            or current.get('injected') or current.get('program') != AGENT):
                raise RuntimeError('CoreServicesUIAgent ownership changed; refusing to replace another configuration')
            if not current:
                reply = self.command('bootstrap', self.domain, str(ORIGINAL))
                if reply.returncode:
                    raise RuntimeError('Cannot restore CoreServicesUIAgent: ' + reply.stderr.strip())
            after = self.wait_original()
            self.changed = False
            if not self.environment.get('MIRRA_BIND_SOCKET'):
                self.release_ownership()
            if settings_error:
                after['settings_disable_error'] = settings_error
            return after
        finally:
            if self.lock is not None:
                self.lock.close()
                self.lock = None

    def release_ownership(self):
        """Call only after the helper and owned apps have also been cleaned up."""
        if self.journal_owned and not self.changed:
            self.journal.clear(self.directory)
            self.journal_owned=False
