"""Create independent backend profiles and deploy a separately validated endpoint."""
import argparse
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shlex
import stat
import subprocess
import tempfile

from transport import Endpoint
from configuration import ROOT, load_config
from state import write_state
from macho import require_ios_endpoint
from wire import platform_ltk
from bridgepaths import ENVIRONMENT_SCRIPT, ROOTHIDE_CODE_ROOT, ROOTHIDE_ENTITLEMENTS, ROOTLESS_DEPLOYMENT_ROOT, helper_command
import activity


class RemoteSetupError(RuntimeError):
    def __init__(self, message, code=None, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def destination(config):
    return ('127.0.0.1', config.listen_port) if config.transport == 'usb' else (config.ssh_host, config.ssh_port)


def fingerprints(data):
    values = []
    for line in data.decode('utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split()
        if len(fields) < 3 or fields[1] not in ('ssh-ed25519', 'ecdsa-sha2-nistp256', 'ssh-rsa'):
            raise ValueError('Unsupported SSH host key record')
        blob = base64.b64decode(fields[2], validate=True)
        value = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
        values.append((value, line))
    if not values:
        raise ValueError('No SSH host keys found')
    return values


def exclusive_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Publish complete bytes without replacing an existing file, even if the
    # process exits during writing. A partial profile must never look usable.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.write', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def new_profile(output, user, *, product='', serial='', transport='usb', host='127.0.0.1', port=None,
                device_port=22):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', user):
        raise ValueError('Supply one SSH account name')
    output = Path(output).expanduser().resolve()
    port = (2222 if transport == 'usb' else 22) if port is None else port
    directory = output.parent
    paths = {'user_file': directory / 'user', 'identity_file': directory / 'identity',
             'known_hosts_file': directory / 'known_hosts', 'certificate_file': directory / 'certificate.der'}
    for path in [output, *paths.values(), Path(str(paths['identity_file']) + '.pub')]:
        if path.exists() or path.is_symlink():
            raise FileExistsError('New profile would overwrite ' + str(path))
    base = load_config(ROOT / 'config/example.json')
    configured = replace(base, source=output, product_type=product, serial=serial,
        transport=transport, ssh_host=host, ssh_port=port if transport == 'ssh' else 22,
        listen_port=port if transport == 'usb' else base.listen_port, device_port=device_port, **paths)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Validate all user settings before key creation or device access.
    check = directory / ('.validate-' + secrets.token_hex(8) + '.json')
    try:
        write_state(check, configured.as_dict())
        load_config(check)
    finally:
        check.unlink(missing_ok=True)
    activity.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'Mirra',
                    '-f', str(paths['identity_file'])], check=True, timeout=15)
    paths['identity_file'].chmod(0o600)
    Path(str(paths['identity_file']) + '.pub').chmod(0o600)
    exclusive_write(paths['user_file'], (user + '\n').encode())
    exclusive_write(output, (json.dumps(configured.as_dict(), indent=2) + '\n').encode())
    return {'profile': str(output), 'public_key': str(paths['identity_file']) + '.pub',
            'next': 'Verify and enroll the SSH host fingerprint, then authorize this public key'}


def pin_host(config, expected, key_type='ed25519'):
    if not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', expected):
        raise ValueError('A verified SHA256 SSH host fingerprint is required')
    if config.known_hosts_file.exists() or config.known_hosts_file.is_symlink():
        raise FileExistsError('Host-key enrollment never overwrites an existing pin')
    host, port = destination(config)
    reply = activity.run(['ssh-keyscan', '-T', '5', '-t', key_type, '-p', str(port), host],
                           capture_output=True, timeout=10)
    if reply.returncode:
        raise RuntimeError('Cannot obtain the configured SSH host key')
    matching = [line for fingerprint, line in fingerprints(reply.stdout) if fingerprint == expected]
    if len(set(matching)) != 1:
        raise RuntimeError('The SSH host key does not match the verified fingerprint; no pin saved')
    fields = matching[0].split()
    if config.host_key_alias:
        # HostKeyAlias is the exact known_hosts lookup name, independent of port.
        fields[0] = config.host_key_alias
    exclusive_write(config.known_hosts_file, (' '.join(fields) + '\n').encode())
    return {'host_key_verified': True, 'fingerprint': expected, 'known_hosts': str(config.known_hosts_file)}


def remote(config, arguments, data=None, timeout=20):
    reply = activity.run(config.ssh_command(arguments), source='SSH', input=data,
                         record_stdout=not (arguments and arguments[0] == 'cat'),
                         capture_output=True, timeout=timeout)
    if reply.returncode:
        stderr = reply.stderr.decode(errors='replace').strip()
        # Verbose SSH output belongs in the live log. Its handshake must not
        # displace the remote launch/dyld error at the end of a saved summary.
        meaningful = [line for line in stderr.splitlines() if not re.match(
            r'^(?:debug[123]:|Transferred:|Bytes per second:)', line)
            or re.search(r'\brtype exit-signal\b|\bExit status -1\b', line)]
        message = '\n'.join(meaningful)[-1500:] or stderr[-1500:]
        details = {'ssh_exit_code': reply.returncode}
        for line in stderr.splitlines():
            if line.startswith('MIRRA_HELPER '):
                try:
                    event = json.loads(line[len('MIRRA_HELPER '):])
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get('event') == 'exit' and type(event.get('exit_status')) is int:
                    details.update(helper_stage=event.get('stage'), remote_exit_status=event['exit_status'])
        if re.search(r'\brtype exit-signal\b', stderr):
            details['remote_exit_signal_received'] = True
        code = None
        try:
            error = json.loads(reply.stdout)
            if isinstance(error, dict) and isinstance(error.get('error'), str):
                code = error['error']
                message = message or code
                missing = error.get('missing_symbols')
                if isinstance(missing, list) and missing and all(
                        isinstance(s, str) and re.fullmatch(r'aks_[a-z0-9_]+', s) for s in missing):
                    message += ': ' + ', '.join(missing)
        except (ValueError, UnicodeError):
            pass
        raise RemoteSetupError('Remote setup command failed: ' + (message or str(reply.returncode)), code, details)
    return reply.stdout


def bridge_environment(config):
    value = json.loads(remote(config, ['sh', '-c', ENVIRONMENT_SCRIPT]))
    if not isinstance(value, dict) or value.get('path_mode') not in ('native', 'roothide'):
        raise RemoteSetupError('The bridge returned an invalid filesystem report',
                               'roothide_path_translation_unavailable')
    if value['path_mode'] == 'roothide' and value.get('path_translation_verified') is not True:
        raise RemoteSetupError('RootHide path conversion could not be verified',
                               'roothide_path_translation_unavailable')
    activity.emit('Bridge', 'filesystem inspected', **value)
    return value


def remote_helper(config, arguments, timeout=20):
    try:
        return remote(config, helper_command(arguments, config.path_mode, observed=True), timeout=timeout)
    except RemoteSetupError as error:
        status = error.details.get('remote_exit_status')
        if error.code is None and (error.details.get('remote_exit_signal_received')
                                  or type(status) is int and 128 <= status <= 255):
            error.code = 'helper_terminated'
        raise


def signing_tool(config):
    # Noninteractive SSH shells need not include the jailbreak prefix in PATH.
    # /var/jb is the rootless bootstrap's stable link, including relocated roots.
    script = '''mirra_signer=$(command -v ldid 2>/dev/null)
if test -n "$mirra_signer" && test -x "$mirra_signer"; then
    printf '%s\\n' "$mirra_signer"
    exit 0
fi
for mirra_signer in /var/jb/usr/bin/ldid /usr/bin/ldid; do
    if test -x "$mirra_signer"; then
        printf '%s\\n' "$mirra_signer"
        exit 0
    fi
done
printf '%s\\n' '{"error":"missing_ldid"}'
exit 1
'''
    path = remote(config, ['sh', '-c', script]).decode().strip()
    if not path.startswith('/') or any(ord(c) < 32 for c in path):
        raise RemoteSetupError('The phone returned an invalid signing-tool path', 'missing_ldid')
    return path


def authorize(config, bootstrap=None, interactive=False, askpass=None):
    public = Path(str(config.identity_file) + '.pub').read_text().strip()
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+=*(?: [^\r\n]*)?', public):
        raise ValueError('Expected one generated Ed25519 public key')
    if bootstrap is not None:
        if destination(bootstrap) != destination(config) or bootstrap.user_file.read_text().strip() != config.user_file.read_text().strip():
            raise ValueError('Bootstrap access must target the same host, port and account')
        if {v for v, _ in fingerprints(bootstrap.known_hosts_file.read_bytes())} != {v for v, _ in fingerprints(config.known_hosts_file.read_bytes())}:
            raise ValueError('Bootstrap and new profile must trust the same SSH host key')
    elif not interactive:
        raise ValueError('Use --interactive for SSH password entry, or an existing --bootstrap-config')
    script = '''umask 077
mkdir -p "$HOME/.ssh" || exit 1
chmod 700 "$HOME/.ssh" || exit 1
mirra_authorized_keys="$HOME/.ssh/authorized_keys"
test ! -L "$mirra_authorized_keys" || exit 1
touch "$mirra_authorized_keys" || exit 1
chmod 600 "$mirra_authorized_keys" || exit 1
IFS= read -r mirra_public_key || exit 1
if ! grep -Fqx -- "$mirra_public_key" "$mirra_authorized_keys"; then
    printf '%s\\n' "$mirra_public_key" >> "$mirra_authorized_keys" || exit 1
fi
'''
    args = (bootstrap or config).ssh_command(['sh', '-c', script])
    if interactive:
        # OpenSSH gets the password from the terminal or a native askpass UI.
        # The provisioning process never reads or stores it.
        args = [value.replace('BatchMode=yes', 'BatchMode=no') for value in args]
        args[1:1] = ['-o', 'PreferredAuthentications=password,keyboard-interactive', '-o', 'PubkeyAuthentication=no']
    options = {}
    if askpass is not None:
        if not interactive or not Path(askpass).is_absolute() or not os.access(askpass, os.X_OK):
            raise ValueError('A native password helper is required for graphical SSH authorization')
        options = {'env': {**os.environ, 'SSH_ASKPASS': str(askpass), 'SSH_ASKPASS_REQUIRE': 'force',
                           'DISPLAY': os.environ.get('DISPLAY', 'mirra-setup')}, 'start_new_session': True}
    activity.run(args, source='SSH', input=(public + '\n').encode(), check=True, timeout=120, **options)
    remote(config, ['true'])
    return {'public_key_authorized': True, 'key_login_verified': True}


def deployment_input(config):
    value = config.as_dict()
    if config.transport == 'usb':
        # Each wizard invocation owns a new temporary forwarder port. Device
        # selector, device SSH port and host pin still identify the same phone.
        value['backend']['usb'].pop('listen_port')
    return value


def verify_key(config, digest):
    activity.emit('Certificate', 'attested-key verification begin', certificate_sha256=digest)
    with Endpoint(config, expected_certificate_sha256=digest) as endpoint:
        if endpoint.ready.get('dcrt_sha256') != digest:
            raise RuntimeError('Endpoint is using a different device certificate')
        key = endpoint.call('generate', attested=True, key_class=9)
        if key.get('status') != 0 or not key.get('identity'):
            raise RuntimeError('The device cannot create the required attested key: ' +
                               str(key.get('status_hex', key.get('error', key.get('status')))))
        validated = endpoint.call('validate', identity=key['identity'])
        if validated.get('status') != 0:
            raise RuntimeError('The device rejected its fresh attested key: ' +
                               str(validated.get('status_hex', validated.get('error', validated.get('status')))))
        # A key accepted by the bridge may still have a layout the host broker
        # cannot adapt. Check the actual broker wire format before publishing.
        _, proof = platform_ltk(key.get('ltk', ''), 3)
        activity.emit('Certificate', 'attested-key verification complete', wire_layout=proof,
                      generate_status=key.get('status'), validate_status=validated.get('status'))
        return proof


def publish_deployment(config, output, record):
    if record.get('schema_version') != 1 or record.get('input') != deployment_input(config):
        raise ValueError('Interrupted deployment belongs to different bridge settings')
    certificate = output.parent / 'certificate.der'
    data = base64.b64decode(record['certificate'], validate=True)
    if hashlib.sha256(data).hexdigest() != record['sha256'] or not 0 < len(data) <= 1048576:
        raise ValueError('Interrupted certificate record changed')
    directory = record['report']['remote_directory']
    if not isinstance(directory, str) or not directory.startswith('/') or any(ord(c) < 32 for c in directory):
        raise ValueError('Invalid saved endpoint directory')
    report = record['report']
    binary_path = report.get('remote_endpoint', directory + '/endpoint')
    certificate_path = report.get('remote_certificate', directory + '/certificate.der')
    for path in (binary_path, certificate_path):
        if not isinstance(path, str) or not path.startswith('/') or any(ord(c) < 32 for c in path):
            raise ValueError('Invalid saved bridge path')
    path_mode = report.get('path_mode', 'native')
    if path_mode not in ('native', 'roothide'):
        raise ValueError('Invalid saved bridge filesystem mode')
    deployed = replace(config, source=output, remote_endpoint=binary_path,
                       remote_certificate=certificate_path, certificate_file=certificate, path_mode=path_mode)
    verify_key(deployed, record['sha256'])
    # The ready profile is the commit marker: publish it after both artifacts.
    # A retry may reuse only identical, private, user-owned files.
    artifacts = [(certificate, data),
                 (output.with_suffix('.provisioning.json'), (json.dumps(record['report'], indent=2) + '\n').encode()),
                 (output, (json.dumps(deployed.as_dict(), indent=2) + '\n').encode())]
    for path, contents in artifacts:
        try:
            exclusive_write(path, contents)
        except FileExistsError:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or path.read_bytes() != contents):
                raise ValueError('Existing deployment file differs; preserved for inspection')
    return record['report']


def deploy(config, output, binary=None, remote_root='/var/mobile/Library/Caches/Mirra', issue=False,
           issue_if_missing=False, resume=False):
    output = Path(output).expanduser().resolve()
    journal = output.with_suffix('.deployment.json')
    if resume and (journal.exists() or journal.is_symlink()):
        info = journal.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Expected a private deployment record')
        return publish_deployment(config, output, json.loads(journal.read_text()))
    certificate = output.parent / 'certificate.der'
    for path in (output, certificate):
        if path.exists() or path.is_symlink():
            raise FileExistsError('Deployment output already exists: ' + str(path))
    if not remote_root.startswith('/') or any(ord(c) < 32 for c in remote_root):
        raise ValueError('Remote installation root must be an absolute path without control characters')
    binary = Path(binary or ROOT / 'build/endpoint').resolve()
    require_ios_endpoint(binary)
    input_config = config
    environment = bridge_environment(config)
    config = replace(config, path_mode=environment['path_mode'])
    # Traditional rootless bootstraps need executable files inside /var/jb.
    # Keep RootHide's separate virtual code/data handling, and honor a custom
    # deployment root. Never replace or symlink a previous installation.
    if (config.path_mode == 'native' and environment.get('rootless') is True
            and remote_root == '/var/mobile/Library/Caches/Mirra'):
        remote_root = ROOTLESS_DEPLOYMENT_ROOT
    activity.emit('Bridge', 'deployment location selected', path_mode=config.path_mode,
                  rootless=environment.get('rootless') is True, remote_root=remote_root)
    signer = signing_tool(config)
    identifier = secrets.token_hex(8)
    remote_directory = remote_root.rstrip('/') + '/' + identifier
    # RootHide maps bootstrap /var and /tmp into a data container, where iOS
    # refuses Mach-O files. Code must live in its bundle-backed code area.
    code_root = ROOTHIDE_CODE_ROOT if config.path_mode == 'roothide' else remote_root
    code_directory = code_root.rstrip('/') + '/' + identifier
    remote_binary = code_directory + '/endpoint'
    remote_certificate = remote_directory + '/certificate.der'
    remote_entitlements = code_directory + '/endpoint.plist'
    quote = shlex.quote
    if config.path_mode == 'roothide':
        remote(config, ['sh', '-c', 'umask 077; if ! mkdir -p ' + quote(code_root)
            + '; then printf \'%s\\n\' \'{"error":"roothide_code_directory_unavailable"}\'; exit 1; fi; '
            + 'mkdir ' + quote(code_directory) + ' && mkdir -p ' + quote(remote_root)
            + ' && mkdir ' + quote(remote_directory)])
        activity.emit('Bridge', 'RootHide code and data directories created',
                      code_directory=code_directory, data_directory=remote_directory)
        remote(config, ['sh', '-c', 'umask 077; cat > ' + quote(remote_binary)], binary.read_bytes())
    else:
        remote(config, ['sh', '-c', 'umask 077; mkdir -p ' + quote(remote_root) + ' && mkdir ' + quote(remote_directory)
            + ' && cat > ' + quote(remote_binary)], binary.read_bytes())
    entitlements = (ROOT / 'config/endpoint.plist').read_bytes()
    if config.path_mode == 'roothide':
        values = plistlib.loads(entitlements)
        values.update({name: True for name in ROOTHIDE_ENTITLEMENTS})
        entitlements = plistlib.dumps(values)
        activity.emit('Bridge', 'RootHide signing entitlements selected', entitlements=list(values))
    remote(config, ['sh', '-c', 'umask 077; cat > ' + quote(remote_entitlements)],
           entitlements)
    remote(config, [signer, '-S' + remote_entitlements, remote_binary])
    remote(config, ['chmod', '700', remote_binary])
    capabilities = json.loads(remote_helper(config, [remote_binary, '--capabilities']))
    if capabilities.get('required_symbols_available') is not True:
        raise RuntimeError('The backend lacks required keystore operations')
    action = '--issue-certificate' if issue else '--export-certificate'
    certificate_issued = issue
    try:
        with activity.operation('Certificate', action, path=remote_certificate):
            exported = json.loads(remote_helper(config, [remote_binary, action, remote_certificate], timeout=55))
    except RemoteSetupError as error:
        if not issue_if_missing or issue or error.code != 'cached_certificate_unavailable':
            raise
        activity.emit('Certificate', 'cached certificate unavailable', 'warning', error_code=error.code)
        with activity.operation('Certificate', '--issue-certificate', path=remote_certificate):
            exported = json.loads(remote_helper(config, [remote_binary, '--issue-certificate', remote_certificate], timeout=55))
        certificate_issued = True
    if not exported.get('certificate_exported'):
        raise RuntimeError('The device certificate could not be exported')
    data = remote(config, ['cat', remote_certificate])
    if not 0 < len(data) <= 1048576 or hashlib.sha256(data).hexdigest() != exported.get('sha256'):
        raise RuntimeError('Downloaded device certificate does not match the export')
    activity.emit('Certificate', 'certificate download verified', bytes=len(data), sha256=exported.get('sha256'))
    activity.run(['openssl', 'x509', '-inform', 'DER', '-noout'], source='Certificate', input=data,
                   capture_output=True, check=True, timeout=10)
    deployed = replace(config, source=output, remote_endpoint=remote_binary,
        remote_certificate=remote_certificate, certificate_file=certificate)
    wire = verify_key(deployed, exported['sha256'])
    report = {'profile': str(output), 'remote_directory': remote_directory,
        'remote_endpoint': remote_binary, 'remote_certificate': remote_certificate, 'path_mode': config.path_mode,
        'capabilities': capabilities, 'certificate_match': True, 'certificate_bytes': len(data),
        'certificate_issued': certificate_issued,
        'fresh_attested_key_validated': True, 'wire_layout_validated': wire['original_fields_byte_identical'],
        'endpoint_sha256_before_signing': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'native_authentication_and_media_verified': False}
    if resume:
        record = {'schema_version': 1, 'input': deployment_input(input_config), 'report': report,
                  'certificate': base64.b64encode(data).decode(), 'sha256': exported['sha256']}
        write_state(journal, record)
        return publish_deployment(input_config, output, record)
    exclusive_write(certificate, data)
    exclusive_write(output.with_suffix('.provisioning.json'), (json.dumps(report, indent=2) + '\n').encode())
    exclusive_write(output, (json.dumps(deployed.as_dict(), indent=2) + '\n').encode())
    activity.emit('Certificate', 'profile published', profile=str(output), certificate_bytes=len(data))
    return report


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'pin-host', 'authorize', 'deploy'])
    parser.add_argument('--config')
    parser.add_argument('--output')
    parser.add_argument('--user', default='mobile')
    parser.add_argument('--product', default='')
    parser.add_argument('--serial', default='')
    parser.add_argument('--transport', choices=['usb', 'ssh'], default='usb')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int)
    parser.add_argument('--device-port', type=int, default=22, help='SSH port on a USB bridge (default: 22)')
    parser.add_argument('--fingerprint')
    parser.add_argument('--host-key-type', choices=['ed25519', 'ecdsa', 'rsa'], default='ed25519')
    parser.add_argument('--bootstrap-config')
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--binary')
    parser.add_argument('--remote-root', default='/var/mobile/Library/Caches/Mirra')
    parser.add_argument('--issue-certificate', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'init':
            if not args.output:
                parser.error('init requires a new --output profile path')
            result = new_profile(args.output, args.user, product=args.product, serial=args.serial,
                transport=args.transport, host=args.host, port=args.port, device_port=args.device_port)
        else:
            config = load_config(args.config)
            if args.action == 'pin-host':
                result = pin_host(config, args.fingerprint or '', args.host_key_type)
            elif args.action == 'authorize':
                result = authorize(config, load_config(args.bootstrap_config) if args.bootstrap_config else None, args.interactive)
            else:
                if not args.output:
                    parser.error('deploy requires a new --output profile path')
                result = deploy(config, args.output, args.binary, args.remote_root, args.issue_certificate)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
