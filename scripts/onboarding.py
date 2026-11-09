"""Resumable first-time bridge setup behind the native window."""
from contextlib import contextmanager
from dataclasses import replace
import json
import hashlib
import os
from pathlib import Path
import re
import secrets
import subprocess

from provisioning import new_profile, fingerprints, pin_host, authorize, deploy, destination, remote, RemoteSetupError, signing_tool, verify_key
from compatibility import inspect_media_profile, supported_apple_app
from configuration import ROOT, load_config
from installation import NativeService
from state import write_state
from forwarder import USBForwarder
from usbmux import list_devices, product_type
from redaction import redact_text, redact_value
import activity


SETUP_MESSAGES = {
    'unsupported_host': 'This Mac or its system frameworks are not supported by this Mirra build. It requires Intel macOS 15 or later with compatible private interfaces. Keep the diagnostics for compatibility review.',
    'host_check_unavailable': 'Mirra could not check this Mac. Reopen a complete copy of Mirra and retry. If the check still fails, keep the diagnostics for review.',
    'app_unavailable': 'Apple’s iPhone Mirroring app could not be read. Check that it is installed in System Applications before continuing.',
    'unsupported_app': 'This copy of Apple’s iPhone Mirroring is not supported by this Mirra build. It requires the original system app from the 1.x version family. Keep the diagnostics for compatibility review.',
    'missing_ldid': 'SSH access works, but Mirra could not find the phone’s signing tool, ldid. Install ldid using the bridge’s jailbreak package manager, then choose Authorize SSH again.',
    'keystore_library_unavailable': 'The bridge cannot load Apple’s keystore library. Keep the setup diagnostics and report its iOS version and jailbreak for compatibility review.',
    'required_keystore_symbols_unavailable': 'The bridge is missing keystore functions needed by Mirra. The setup diagnostics list the missing functions. Report its iOS version and jailbreak for compatibility review.',
    'certificate_api_unavailable': 'This bridge does not expose the device-certificate API used by Mirra. Keep the setup diagnostics for compatibility review.',
    'certificate_issuance_api_unavailable': 'This bridge cannot request the device certificate through Mirra’s current API. Keep the setup diagnostics for compatibility review.',
    'roothide_code_directory_unavailable': 'RootHide could not create Mirra’s code folder. Start a new bridge setup using the phone’s root SSH account, then prepare the connection again. If you already used root, keep the logs for review.',
    'roothide_path_translation_unavailable': 'Mirra could not use RootHide’s filesystem tools. Check that Dopamine/RootHide is active and its jbroot and rootfs tools are installed, then retry.',
    'helper_terminated': 'SSH login worked, but the phone stopped Mirra’s helper. Show log includes the launch status. Keep those logs and report the bridge’s iOS and jailbreak versions.',
}


class SetupIssue(RuntimeError):
    """An actionable message selected locally, never arbitrary remote output."""
    def __init__(self, code):
        super().__init__(SETUP_MESSAGES[code])
        self.code = code


class SetupWizard:
    def __init__(self, *, service=None, root=ROOT, askpass=None):
        self.service = service or NativeService()
        self.root = Path(root)
        self.base = self.service.base / 'setup'
        self.active = self.base / 'active.json'
        self.askpass = askpass
        self.started_id = None

    def read(self, path):
        self.service.private_file(path)
        value = json.loads(Path(path).read_text())
        if not isinstance(value, dict):
            raise ValueError('Invalid setup record')
        return value

    def paths(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('Invalid setup identifier')
        directory = self.base / identifier
        return directory, directory / 'access.json', directory / 'ready.json'

    def load(self, identifier=None):
        if identifier is None:
            identifier = self.read(self.active)['id']
        directory, _, _ = self.paths(identifier)
        value = self.read(directory / 'setup.json')
        if value.get('id') != identifier or value.get('schema_version') != 1:
            raise ValueError('Setup identity changed')
        return value

    def save(self, state):
        directory, _, _ = self.paths(state['id'])
        write_state(directory / 'setup.json', state)
        write_state(self.active, {'id': state['id']})

    def host_check(self):
        try:
            report = inspect_media_profile(self.root)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise SetupIssue('host_check_unavailable') from error
        if not report['compatible']:
            raise SetupIssue('unsupported_host')
        import plistlib
        try:
            info = plistlib.loads(Path('/System/Applications/iPhone Mirroring.app/Contents/Info.plist').read_bytes())
            if not isinstance(info, dict):
                raise ValueError('Invalid application metadata')
        except (OSError, ValueError, plistlib.InvalidFileException) as error:
            raise SetupIssue('app_unavailable') from error
        if not supported_apple_app(info):
            raise SetupIssue('unsupported_app')

    def defaults(self):
        result = {'transport': 'ssh', 'host': '', 'port': 22, 'user': 'mobile'}
        try:
            self.service.validate_package()
            config = load_config(self.service.package / 'config.json')
            result.update(transport=config.transport, host=config.ssh_host,
                          port=config.device_port if config.transport == 'usb' else config.ssh_port,
                          user=config.user_file.read_text().strip())
        except (OSError, ValueError, RuntimeError):
            pass
        return result

    def describe(self, state=None):
        if state is None:
            if not self.active.exists():
                return {'phase': 'new', 'defaults': self.defaults()}
            state = self.load()
        # This local wizard report intentionally includes the host and public
        # fingerprint for the owner's trust decision, but never credentials.
        result = {k: state[k] for k in ('id', 'phase', 'host', 'transport', 'user', 'fingerprint',
                  'host_key_type', 'error', 'certificate_issued', 'product_type', 'os_version') if k in state}
        result['defaults'] = self.defaults()
        if state.get('error') and state.get('error_code'):
            result['error_code'] = state['error_code']
        if state.get('phase') == 'installed':
            from readiness import observe
            result['support_report'] = observe(self.service)
            result['support_state'] = result['support_report']['state']
        return result

    def devices(self):
        result = []
        for device in list_devices():
            serial = device.get('Properties', {}).get('SerialNumber')
            if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9-]+', serial):
                continue
            try:
                product = product_type(device['DeviceID'])
            except (OSError, ValueError, KeyError):
                product = None
            title = product if isinstance(product, str) and product else 'USB device'
            # A stable selector suffices for SSH. Requiring lockdown model
            # discovery would hide some locked/unpaired jailbroken bridges.
            result.append({'product_type': product or '', 'serial': serial,
                           'title': title + ' · ' + serial[-6:]})
        return {'devices': result}

    @contextmanager
    def connection(self, config):
        forwarder = None
        try:
            if config.transport == 'usb':
                # An isolated temporary port cannot steal the running service's
                # forwarder. HostKeyAlias keeps trust independent of this port.
                forwarder = USBForwarder(replace(config, listen_port=0)).start()
                config = replace(config, listen_port=forwarder.server.server_address[1])
            yield config
        finally:
            if forwarder is not None:
                forwarder.close()

    def begin(self, settings):
        self.host_check()
        os.umask(0o077)
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.base.stat().st_uid != os.getuid() or self.base.stat().st_mode & 0o077:
            raise RuntimeError('Setup directory must be private')
        identifier = secrets.token_hex(16)
        directory, access, _ = self.paths(identifier)
        directory.mkdir(mode=0o700)
        transport = settings.get('transport', 'ssh')
        if transport == 'usb' and not settings.get('serial'):
            raise ValueError('Choose the USB bridge from the device list')
        port = int(settings.get('port', 22))
        new_profile(access, settings.get('user', 'mobile'), transport=transport,
                    host=settings.get('host', '127.0.0.1') if transport == 'ssh' else '127.0.0.1',
                    serial=settings.get('serial', '') if transport == 'usb' else '',
                    port=port if transport == 'ssh' else 2222,
                    device_port=port if transport == 'usb' else 22)
        config = replace(load_config(access), host_key_alias='mirra' + identifier)
        write_state(access, config.as_dict())
        state = {'schema_version': 1, 'id': identifier, 'phase': 'contact', 'transport': transport,
                 'host': config.ssh_host if transport == 'ssh' else 'USB bridge',
                 'user': settings.get('user', 'mobile')}
        self.save(state)
        self.started_id = identifier
        return self.scan(identifier)

    def scan(self, identifier):
        state = self.load(identifier)
        if state['phase'] not in ('contact', 'trust'):
            raise ValueError('SSH trust has already been enrolled for this setup')
        _, access, _ = self.paths(identifier)
        with self.connection(load_config(access)) as config:
            response = self.scan_keys(config)
            if response.returncode:
                raise RuntimeError('The phone’s SSH server could not be reached')
            candidates = fingerprints(response.stdout)
            rank = {'ssh-ed25519': 0, 'ecdsa-sha2-nistp256': 1, 'ssh-rsa': 2}
            fingerprint, line = min(candidates, key=lambda item: rank[item[1].split()[1]])
        state.update(phase='trust', fingerprint=fingerprint,
                     host_key_type={'ssh-ed25519': 'ed25519', 'ecdsa-sha2-nistp256': 'ecdsa', 'ssh-rsa': 'rsa'}[line.split()[1]], error=None)
        self.save(state)
        return self.describe(state)

    @staticmethod
    def scan_keys(config):
        host, port = destination(config)
        return activity.run(['ssh-keyscan', '-T', '5', '-t', 'ed25519,ecdsa,rsa',
                               '-p', str(port), host], capture_output=True, timeout=12)

    def trust(self, identifier, approved_fingerprint):
        state = self.load(identifier)
        if state['phase'] != 'trust' or approved_fingerprint != state['fingerprint']:
            raise ValueError('Approve the fingerprint displayed for this setup')
        _, access, _ = self.paths(identifier)
        with self.connection(load_config(access)) as config:
            if config.known_hosts_file.exists():
                if {x[0] for x in fingerprints(config.known_hosts_file.read_bytes())} != {approved_fingerprint}:
                    raise ValueError('The saved host key differs from the approved phone')
            else:
                pin_host(config, approved_fingerprint, state['host_key_type'])
        state.update(phase='authorize', error=None); self.save(state)
        return self.describe(state)

    def existing_access(self, config):
        try:
            self.service.validate_package()
            candidate = load_config(self.service.package / 'config.json')
            if candidate.transport == 'usb' and config.transport == 'usb':
                if ((candidate.serial and candidate.serial != config.serial)
                        or (candidate.product_type and candidate.product_type != config.product_type)):
                    return None
                candidate = replace(candidate, listen_port=config.listen_port)
            if destination(candidate) != destination(config) or candidate.user_file.read_text() != config.user_file.read_text():
                return None
            if {x[0] for x in fingerprints(candidate.known_hosts_file.read_bytes())} != {x[0] for x in fingerprints(config.known_hosts_file.read_bytes())}:
                return None
            return candidate
        except (OSError, ValueError, RuntimeError):
            return None

    def connect(self, identifier):
        state = self.load(identifier)
        if state['phase'] != 'authorize':
            raise ValueError('Verify the phone’s SSH host key first')
        _, access, _ = self.paths(identifier)
        with self.connection(load_config(access)) as config:
            try:
                remote(config, ['true'])  # A prior interrupted authorization may already have succeeded.
            except RuntimeError:
                bootstrap = self.existing_access(config)
                authorize(config, bootstrap=bootstrap, interactive=bootstrap is None,
                          askpass=self.askpass if bootstrap is None else None)
            try:
                signing_tool(config)
            except RemoteSetupError as error:
                if error.code == 'missing_ldid':
                    raise SetupIssue('missing_ldid') from error
                raise
        state.update(phase='deploy', error=None); self.save(state)
        return self.describe(state)

    def provision(self, identifier):
        state = self.load(identifier)
        if state['phase'] != 'deploy':
            raise ValueError('Authorize SSH before creating the certificate and helper')
        directory, access, ready = self.paths(identifier)
        base = load_config(access)
        with self.connection(base) as config:
            if ready.exists():
                # Recover after a complete deployment whose UI result was lost.
                deployed = load_config(ready)
                check = replace(deployed, listen_port=config.listen_port) if base.transport == 'usb' else deployed
                verify_key(check, hashlib.sha256(check.certificate_file.read_bytes()).hexdigest())
                details = self.read(ready.with_suffix('.provisioning.json'))
            else:
                details = deploy(config, ready, binary=self.root / 'build/endpoint',
                                 issue_if_missing=True, resume=True)
                # The temporary setup proxy is not the installed service port.
                if base.transport == 'usb':
                    write_state(ready, replace(load_config(ready), listen_port=base.listen_port).as_dict())
        capabilities = details.get('capabilities', {})
        state.update(phase='ready', error=None, certificate_issued=bool(details.get('certificate_issued')),
                     product_type=capabilities.get('product_type', ''), os_version=capabilities.get('os_version', ''))
        self.save(state)
        return self.describe(state)

    def finish(self, identifier):
        state = self.load(identifier)
        if state['phase'] not in ('ready', 'installed'):
            raise ValueError('Verify the certificate and helper before enabling Mirroring')
        _, _, ready = self.paths(identifier)
        self.host_check()
        config = load_config(ready)
        # The final UI action explicitly switches to this verified bridge.
        with activity.operation('Service', 'stop previous bridge'):
            self.service.stop()
        with activity.operation('Service', 'prepare selected bridge'):
            self.service.prepare(config, replace_package=self.service.package.exists())
        with activity.operation('Service', 'install selected bridge'):
            self.service.install()
        state.update(phase='installed', error=None); self.save(state)
        return self.describe(state)

    def failure(self, identifier, action, error):
        hints = {'begin': 'Check the Mac requirements and SSH settings, then try again.',
                 'scan': 'Check the phone’s Wi-Fi address, SSH port and active jailbreak.',
                 'trust': 'The phone’s SSH key did not match. Check its fingerprint before retrying.',
                 'connect': 'Check the SSH account and password. The phone also needs ldid available in its SSH shell.',
                 'provision': 'Certificate or helper setup did not finish. Keep the phone online with its jailbreak active, then retry.',
                 'finish': 'Support could not switch to this bridge. Keep diagnostics and check service status before retrying.'}
        code = error.code if isinstance(error, (SetupIssue, RemoteSetupError)) and error.code in SETUP_MESSAGES else None
        message = SETUP_MESSAGES[code] if code else hints.get(action, 'Setup could not finish. Reopen setup and try again.')
        if action == 'begin':
            identifier = self.started_id
        elif identifier is None and self.active.exists():
            identifier = self.read(self.active)['id']
        if identifier is not None:
            state = self.load(identifier)
            state.update(error=message, error_code=code); self.save(state)
            # Detailed errors stay in private local files, never in the public status.
            directory, _, _ = self.paths(identifier)
            detail = {'action': action, 'type': type(error).__name__, 'message': redact_text(str(error))[:2000]}
            if isinstance(error, RemoteSetupError):
                detail.update(error_code=error.code, details=redact_value(error.details))
            write_state(directory / 'error.json', detail)
            return self.describe(state)
        return {'phase': 'new', 'defaults': self.defaults(), 'error': message, 'error_code': code}
