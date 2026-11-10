"""Install and control per-user native support from a stable private directory."""
import argparse
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from configuration import ROOT, load_config
from processes import process_birth
from state import write_state
from compatibility import require_media_profile
from interpreter import copy_runtime, ENTRYPOINT
import activity

LABEL = 'local.mirra.agent'
ADAPTERS = ['keystore.dylib', 'media.dylib',
            'sockets.dylib', 'launch.dylib']
HOST_TOOLS = ['inspector']
DECODER_ADAPTERS = ['decoder-route.dylib']
PACKAGE_ADAPTERS = ADAPTERS + DECODER_ADAPTERS
PACKAGE_SCHEMA = 5
SUPPORT_REVISION = 20


class NativeService:
    def __init__(self, base=None, source=ROOT):
        self.base = Path(base or Path.home() / 'Library/Application Support/Mirra').resolve()
        self.source = Path(source).resolve()
        self.package = self.base / 'current'
        self.plist = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
        self.domain = f'gui/{os.getuid()}'
        self.target = self.domain + '/' + LABEL
        self.log = self.base / 'logs/service.log'
        self.state = self.base / 'service.json'

    def command(self, *args):
        return activity.run(['launchctl', *args], source='launchctl', capture_output=True, text=True, timeout=15)

    def registration(self):
        reply = self.command('print', self.target)
        if reply.returncode:
            if 'Could not find service' in reply.stderr:
                return {}
            raise RuntimeError('Cannot inspect native support service: ' + reply.stderr.strip())
        values = {}
        for line in reply.stdout.splitlines():
            key, separator, value = line.strip().partition(' = ')
            if separator and key in {'path', 'program', 'pid', 'state', 'last exit code'}:
                values.setdefault(key, value)
        if values.get('path') != str(self.plist):
            raise RuntimeError('The service label belongs to another configuration')
        return values

    def python(self):
        return str(self.package / 'python' / ENTRYPOINT)

    def definition(self, config):
        arguments = [self.python(), '-E', '-s', '-B', str(self.package / 'scripts/native.py'),
            '--config', str(self.package / 'config.json'), '--launch-services',
            '--recover-backend', '--until-stopped', '--service-log', str(self.log),
            '--service-state', str(self.state)]
        if config.transport == 'usb':
            arguments.append('--manage-usb')
        return {'Label': LABEL, 'ProgramArguments': arguments,
            'WorkingDirectory': str(self.package), 'RunAtLoad': True, 'KeepAlive': True,
            'LimitLoadToSessionType': 'Aqua', 'ThrottleInterval': 10, 'ExitTimeOut': 120,
            'ProcessType': 'Background', 'Umask': 0o077,
            'StandardOutPath': str(self.log), 'StandardErrorPath': str(self.log)}

    def validate_package(self):
        manifest = self.package / 'manifest.json'
        self.private_file(manifest)
        value = json.loads(manifest.read_text())
        if (value.get('schema_version') not in (4, PACKAGE_SCHEMA) or value.get('product') != 'Mirra' or value.get('package') != str(self.package)
                or not isinstance(value.get('files'), dict)):
            raise RuntimeError('Invalid Mirra package manifest')
        required = {'scripts/native.py', 'config.json'} | {'build/' + name for name in ADAPTERS + HOST_TOOLS}
        required |= {'python/' + ENTRYPOINT, 'python/manifest.json', 'python/licenses/LICENSE.cpython.txt'}
        if value['schema_version'] == PACKAGE_SCHEMA:
            required |= {'scripts/decoder.py', 'scripts/decoder_route.py', 'build/decoder-route.dylib'}
            if type(value.get('support_revision')) is not int or value['support_revision'] < 1:
                raise RuntimeError('Invalid Mirra support revision')
            if value['support_revision'] >= 20:
                required.add('scripts/startup.py')
        if not required.issubset(value['files']):
            raise RuntimeError('Native support package is missing required files')
        for name, digest in value['files'].items():
            path = self.package / name
            if (not path.resolve().is_relative_to(self.package.resolve())
                    or '..' in Path(name).parts or Path(name).is_absolute()):
                raise RuntimeError('Package manifest contains an unexpected path')
            self.private_file(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError('Installed support file changed: ' + name)
        return value

    def needs_update(self, manifest=None):
        value = manifest if manifest is not None else self.validate_package()
        return value.get('schema_version', 0) < PACKAGE_SCHEMA or value.get('support_revision', 0) < SUPPORT_REVISION

    def update_saved(self):
        """Upgrade recognized support, preserving its selected bridge and keys."""
        manifest = self.validate_package()
        if self.needs_update(manifest):
            with activity.operation('Service', 'check updated support'):
                self.validate_source()
            config = load_config(self.package / 'config.json')
            if self.plist.exists() or self.plist.is_symlink():
                self.private_file(self.plist)
                if plistlib.loads(self.plist.read_bytes()) != self.definition(config):
                    raise RuntimeError('Existing support definition differs; it was preserved')
            elif self.registration():
                raise RuntimeError('Registered support has no verified login definition; it was preserved')
            with activity.operation('Service', 'stop support for update'):
                self.stop()
            with activity.operation('Service', 'update support using saved bridge'):
                self.prepare(replace_package=True)
        return self.install()

    def validate_source(self):
        for name in ('native.py', 'decoder.py', 'decoder_route.py', 'startup.py'):
            if not (self.source / 'scripts' / name).is_file():
                raise RuntimeError('Updated support is incomplete; missing ' + name)
        for name in PACKAGE_ADAPTERS + HOST_TOOLS:
            source = self.source / 'build' / name
            if not source.is_file():
                raise RuntimeError('Build the native support adapters first; missing ' + name)
            subprocess.run(['codesign', '--verify', str(source)], capture_output=True, check=True, timeout=10)
        require_media_profile(self.source)

    @staticmethod
    def private_file(path):
        info = Path(path).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('Expected a private, user-owned support file: ' + str(path))

    def prepare(self, config=None, replace_package=False):
        os.umask(0o077)
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.base.stat().st_uid != os.getuid() or self.base.stat().st_mode & 0o077:
            raise RuntimeError('Native support installation directory is not private')
        previous_definition = None
        if self.package.exists():
            self.validate_package()
            if not replace_package:
                return {'package': str(self.package), 'already_prepared': True}
            if self.registration():
                raise RuntimeError('Stop the service before replacing its package')
            if self.state.exists():
                self.private_file(self.state)
                birth = json.loads(self.state.read_text())['supervisor_birth']
                if process_birth(birth[0]) == tuple(birth):
                    raise RuntimeError('Wait for native support cleanup before replacing its package')
            previous_definition = self.definition(load_config(self.package / 'config.json'))
            # Never move runtime paths referenced by pending recovery journals.
            for name in ('launch.json', 'sharing.json', 'decoder-route.json'):
                journal = Path.home() / 'Library/Caches/Mirra' / name
                if journal.exists():
                    raise RuntimeError('Finish native session cleanup before replacing the package')
        if config is None:
            # Updating source must not reset a working bridge to repo defaults.
            config = load_config(self.package / 'config.json' if self.package.exists() else None)
        self.validate_source()
        staging = Path(tempfile.mkdtemp(prefix='.package-', dir=self.base))
        files = []
        for source in sorted((self.source / 'scripts').glob('*.py')):
            target = staging / 'scripts' / source.name
            target.parent.mkdir(mode=0o700, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o600)
            files.append(target.relative_to(staging))
        embedded = self.source / 'python'
        if not embedded.exists() and (self.package / 'python').exists():
            embedded = self.package / 'python'
        if not embedded.exists():
            embedded = self.source / 'build/Mirra.app/Contents/Resources/Support/python'
        if not embedded.exists():
            raise RuntimeError('Build Mirra.app before installing its background service')
        files.extend(path.relative_to(staging) for path in copy_runtime(embedded, staging / 'python'))
        for name in PACKAGE_ADAPTERS + HOST_TOOLS:
            target = staging / 'build' / name
            target.parent.mkdir(mode=0o700, exist_ok=True)
            shutil.copyfile(self.source / 'build' / name, target)
            target.chmod(0o700)
            files.append(target.relative_to(staging))
        paths = {}
        for attribute, name in [('user_file', 'user'), ('identity_file', 'identity'),
                                ('known_hosts_file', 'known_hosts'), ('certificate_file', 'certificate.der')]:
            source = getattr(config, attribute)
            if not source.is_file() or not source.stat().st_size:
                raise RuntimeError('Backend provisioning is incomplete: ' + attribute)
            target = staging / 'private' / name
            target.parent.mkdir(mode=0o700, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o600)
            files.append(target.relative_to(staging))
            paths[attribute] = self.package / 'private' / name
        installed = replace(config, **paths, runtime_directory=self.package / 'runtime/host')
        write_state(staging / 'config.json', installed.as_dict())
        files.append(Path('config.json'))
        manifest = {'schema_version': PACKAGE_SCHEMA, 'support_revision': SUPPORT_REVISION,
            'product': 'Mirra', 'package': str(self.package),
            'prepared_at': datetime.now().isoformat(), 'files': {
                str(path): hashlib.sha256((staging / path).read_bytes()).hexdigest() for path in files}}
        if previous_definition is not None:
            manifest['previous_definition'] = previous_definition
        write_state(staging / 'manifest.json', manifest)
        subprocess.run([sys.executable, '-E', '-s', '-B', '-m', 'compileall', '-q', str(staging / 'scripts')], check=True, timeout=30)
        if self.package.exists():
            backup = self.base / ('previous-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
            self.package.rename(backup)
        staging.rename(self.package)
        (self.base / 'logs').mkdir(mode=0o700, exist_ok=True)
        if not self.log.exists():
            self.log.touch(mode=0o600)
        self.private_file(self.log)
        review = self.base / 'service.plist'
        review.write_bytes(plistlib.dumps(self.definition(installed)))
        review.chmod(0o600)
        self.validate_package()
        return {'package': str(self.package), 'prepared_plist': str(review),
                'copied_files': len(files), 'login_enabled': False}

    def install(self):
        package = self.validate_package()
        config = load_config(self.package / 'config.json')
        definition = self.definition(config)
        previous = None
        backup = None
        if self.plist.exists() or self.plist.is_symlink():
            self.private_file(self.plist)
            previous = self.plist.read_bytes()
            old_definition = plistlib.loads(previous)
            if old_definition != definition:
                other_transport = 'ssh' if config.transport == 'usb' else 'usb'
                recognized = [self.definition(replace(config, transport=transport))
                              for transport in (config.transport, other_transport)]
                # prepare() records the exact previously recognized definition
                # before replacing the package. This permits a stopped upgrade
                # while preserving the owner's installed bridge configuration.
                if package.get('previous_definition'):
                    recognized.append(package['previous_definition'])
                if old_definition not in recognized:
                    raise RuntimeError('Existing service plist differs; inspect it before replacement')
                # Only the known USB-forwarding flag may change. A registered
                # job, or an unregistered supervisor still restoring, is live.
                if self.registration():
                    raise RuntimeError('Stop the service before changing its transport')
                if self.state.exists():
                    self.private_file(self.state)
                    birth = json.loads(self.state.read_text())['supervisor_birth']
                    if process_birth(birth[0]) == tuple(birth):
                        raise RuntimeError('Wait for native support cleanup before changing its transport')
                descriptor, name = tempfile.mkstemp(prefix='service.previous.', suffix='.plist', dir=self.base)
                backup = Path(name)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(previous)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.write_definition(definition, previous)
        else:
            self.write_definition(definition)
        result = self.start()
        if backup is not None:
            result['previous_plist'] = str(backup)
        return result

    def write_definition(self, definition, previous=None):
        """Publish a complete private plist; preserve the old file on failure."""
        self.plist.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='.' + LABEL + '-', suffix='.plist', dir=self.plist.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(plistlib.dumps(definition))
                stream.flush()
                os.fsync(stream.fileno())
            if previous is None:
                # Unlike replace, this cannot clobber a concurrently created job.
                os.link(temporary, self.plist)
            else:
                self.private_file(self.plist)
                if self.plist.read_bytes() != previous or self.registration():
                    raise RuntimeError('Service definition changed during installation; retry after cleanup')
                os.replace(temporary, self.plist)
        finally:
            temporary.unlink(missing_ok=True)

    def start(self):
        self.validate_package()
        self.private_file(self.plist)
        if plistlib.loads(self.plist.read_bytes()) != self.definition(load_config(self.package / 'config.json')):
            raise RuntimeError('Service plist no longer matches the installed package')
        if not self.registration():
            reply = self.command('bootstrap', self.domain, str(self.plist))
            if reply.returncode:
                raise RuntimeError('Cannot start native support: ' + reply.stderr.strip())
        return self.status()

    def stop(self):
        before = self.registration()
        birth = process_birth(int(before['pid'])) if before.get('pid') else None
        if birth is None and self.state.exists():
            self.private_file(self.state)
            saved = tuple(json.loads(self.state.read_text())['supervisor_birth'])
            if process_birth(saved[0]) == saved:
                birth = saved
        if before:
            reply = self.command('bootout', self.target)
            if reply.returncode:
                raise RuntimeError('Cannot stop native support: ' + reply.stderr.strip())
        deadline = time.monotonic() + 110
        while birth is not None and process_birth(birth[0]) == birth:
            if time.monotonic() > deadline:
                raise RuntimeError('Native support is still restoring; leave it running and inspect status')
            time.sleep(.2)
        # Inspect the installed controller's last report, not just job removal.
        current = self.status()
        current['login_enabled'] = self.plist.exists()
        if birth is not None and not current.get('last_cleanup_verified'):
            raise RuntimeError('Service process ended but native cleanup is not verified; restart support to recover before removing it')
        return current

    def remove(self):
        self.private_file(self.plist)
        if plistlib.loads(self.plist.read_bytes()) != self.definition(load_config(self.package / 'config.json')):
            raise RuntimeError('Refusing to remove an unfamiliar service definition')
        self.stop()
        self.plist.unlink()
        return {'login_enabled': False, 'package_preserved': str(self.package)}

    def status(self):
        registration = self.registration()
        value = {'installed': self.package.exists(), 'login_enabled': self.plist.exists(),
                 'registered': bool(registration), 'registration': registration,
                 'native_launch_support_ready': False, 'backend_ready': False}
        if not self.state.exists():
            return value
        self.private_file(self.state)
        state = json.loads(self.state.read_text())
        birth = state['supervisor_birth']
        alive = process_birth(birth[0]) == tuple(birth)
        value.update(supervisor_alive=alive, supervisor_birth=birth)
        if state.get('startup_failure'):
            value['startup_failure'] = state['startup_failure']
        if 'directory' not in state:
            if state['phase'] not in ('startup_checks', 'startup_failed', 'stopped'):
                raise RuntimeError('Unexpected native startup state')
            value['last_cleanup_verified'] = bool(not alive and state.get('startup_cleanup_verified'))
            return value
        directory = Path(state['directory'])
        if directory.parent != self.package / 'runtime' or not directory.name.startswith('native-'):
            raise RuntimeError('Unexpected native support state directory')
        value.update(run=directory.name, supervisor_alive=alive, supervisor_birth=birth)
        if not alive:
            result = directory / 'result.json'
            if result.exists():
                data = json.loads(result.read_text())
                launch_clean = bool(data.get('launch_agent_restored')) or data.get('launch_agent_attempted') is False
                if 'launch_agent_attempted' not in data:
                    # Builds 341/342 save only the selected decoder decision
                    # when decoder.start() fails, before trying a launch agent.
                    # Accept that old record only with verified AVC restoration.
                    decoder = data.get('decoder_route', {})
                    launch_clean = launch_clean or (decoder.get('enabled') is True and 'ready' not in decoder
                        and not data.get('launch_agent')
                        and data.get('decoder_cleanup', {}).get('original_avc_restored') is True)
                value['last_cleanup_verified'] = bool(data.get('backend_cleanup', {}).get('cleanup_verified')
                    and launch_clean and not data.get('launch_agent_restore_error')
                    and not data.get('app_cleanup_error') and not data.get('decoder_restore_error')
                    and (not data.get('decoder_route', {}).get('enabled')
                         or data.get('decoder_cleanup', {}).get('cleanup_verified')))
            return value
        value['native_launch_support_ready'] = state['phase'] == 'running'
        decoder = state.get('decoder_route', {})
        if decoder.get('enabled'):
            # Startup state cannot establish that a later decoder process has
            # loaded the hook. Use the supervisor's current observation.
            decoder_path = directory / 'decoder-route.json'
            try:
                self.private_file(decoder_path)
                decoder = json.loads(decoder_path.read_text())
                decoder_ready = isinstance(decoder, dict) and decoder.get('ready') is True
            except (OSError, ValueError, RuntimeError):
                decoder_ready = False
            if not decoder_ready:
                value['native_launch_support_ready'] = False
        backend = directory / 'backend-status.json'
        if backend.exists():
            info = json.loads(backend.read_text())
            worker = info.get('worker_birth')
            value.update(backend_phase=info['phase'], backend_attempt=info['attempt'],
                backend_ready=bool(info.get('ready') and worker and process_birth(worker[0]) == tuple(worker)))
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'install', 'start', 'stop', 'status', 'remove'])
    parser.add_argument('--config')
    parser.add_argument('--replace', action='store_true', help='Archive a stopped package before preparing its replacement')
    args = parser.parse_args()
    service = NativeService()
    try:
        if (args.config or args.replace) and args.action not in ('prepare', 'install'):
            parser.error('--config and --replace apply only to prepare/install')
        if args.config and service.package.exists() and not args.replace:
            raise RuntimeError('A prepared configuration already exists. Stop support and use prepare --replace --config PATH to change it')
        if args.action == 'prepare':
            result = service.prepare(load_config(args.config) if args.config else None, args.replace)
        elif args.action == 'install':
            if args.config or args.replace or not service.package.exists():
                service.prepare(load_config(args.config) if args.config else None, args.replace)
                result = service.install()
            else:
                result = service.update_saved()
        else:
            result = getattr(service, args.action)()
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
