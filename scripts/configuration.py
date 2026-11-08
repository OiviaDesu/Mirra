"""Validated device and host settings shared by the command-line tools."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex

from bridgepaths import helper_command

ROOT = Path(__file__).resolve().parents[1]


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    source: Path
    transport: str
    product_type: str
    serial: str
    listen_port: int
    device_port: int
    ssh_host: str
    ssh_port: int
    user_file: Path
    identity_file: Path
    known_hosts_file: Path
    host_key_alias: str
    remote_endpoint: str
    remote_certificate: str
    timeout: int
    certificate_file: Path
    runtime_directory: Path
    system_app: Path
    seconds: int
    path_mode: str = 'native'
    decoder_route: str = 'auto'

    def as_dict(self):
        value = {'schema_version':1,'backend':{
            'transport':self.transport,
            'usb':{'product_type':self.product_type,'serial':self.serial,'listen_port':self.listen_port,'device_port':self.device_port},
            'ssh':{'host':self.ssh_host,'port':self.ssh_port,'user_file':str(self.user_file),
                'identity_file':str(self.identity_file),'known_hosts_file':str(self.known_hosts_file),
                'host_key_alias':self.host_key_alias,'remote_endpoint':self.remote_endpoint,
                'remote_certificate':self.remote_certificate,'timeout_seconds':self.timeout},
            'certificate_file':str(self.certificate_file)},
            'host':{'runtime_directory':str(self.runtime_directory),'system_app':str(self.system_app)},
            'session':{'seconds':self.seconds}}
        if self.path_mode != 'native':
            value['backend']['ssh']['path_mode'] = self.path_mode
        if self.decoder_route != 'auto':
            value['host']['decoder_route'] = self.decoder_route
        return value

    def ssh_command(self, remote_arguments=None):
        try:
            user = self.user_file.read_text().strip()
        except OSError as error:
            raise ConfigError('Missing SSH account file; import or configure device access first') from error
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', user):
            raise ConfigError('SSH account must be a single account name')
        host = '127.0.0.1' if self.transport == 'usb' else self.ssh_host
        port = self.listen_port if self.transport == 'usb' else self.ssh_port
        command = ['ssh', '-T', '-p', str(port), '-l', user, '-i', str(self.identity_file),
                   '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=8',
                   '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile='+ssh_config_path(self.known_hosts_file)]
        if self.host_key_alias:
            command += ['-o', 'HostKeyAlias='+self.host_key_alias]
        # OpenSSH invokes a remote shell even when the local call uses argv.
        arguments = (helper_command([self.remote_endpoint, self.remote_certificate], self.path_mode)
                     if remote_arguments is None else list(remote_arguments))
        if not arguments or any(not isinstance(value, str) or '\x00' in value for value in arguments):
            raise ConfigError('Remote command must contain text arguments without NUL bytes')
        return command + [host, shlex.join(arguments)]


def ssh_config_path(path):
    # OpenSSH parses -o values again, including whitespace-separated filenames
    # and percent tokens. Local argv quoting alone does not protect this path.
    value = str(path).replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"')
    return '"' + value + '"'


def _object(value, allowed, where):
    if not isinstance(value, dict):
        raise ConfigError(where+' must be an object')
    unknown = set(value)-set(allowed)
    if unknown:
        raise ConfigError(where+': unknown setting '+', '.join(sorted(unknown)))
    return value


def _text(value, where, empty=False):
    if not isinstance(value, str) or (not value and not empty) or any(ord(c)<32 for c in value):
        raise ConfigError(where+' must be a string without control characters')
    return value


def _integer(value, low, high, where):
    if type(value) is not int or not low <= value <= high:
        raise ConfigError(f'{where} must be an integer in {low}..{high}')
    return value


def load_config(path=None, *, root=ROOT):
    root = Path(root).resolve()
    explicit = path is not None or bool(os.environ.get('MIRRA_CONFIG'))
    source = Path(path or os.environ.get('MIRRA_CONFIG') or root/'config/local.json').expanduser().resolve()
    if source.is_file():
        try:
            raw = json.loads(source.read_text())
        except (ValueError, OSError) as error:
            raise ConfigError('Cannot read configuration: '+str(source)) from error
    elif explicit:
        raise ConfigError('Configuration file does not exist: '+str(source))
    else:
        raw = {}
    _object(raw, ['schema_version','backend','host','session'], 'configuration')
    if type(raw.get('schema_version',1)) is not int or raw.get('schema_version',1) != 1:
        raise ConfigError('Unsupported configuration schema_version')
    backend = _object(raw.get('backend',{}), ['transport','usb','ssh','certificate_file'], 'backend')
    usb = _object(backend.get('usb',{}), ['product_type','serial','listen_port','device_port'], 'backend.usb')
    ssh = _object(backend.get('ssh',{}), ['host','port','user_file','identity_file','known_hosts_file',
        'host_key_alias','remote_endpoint','remote_certificate','timeout_seconds','path_mode'], 'backend.ssh')
    host = _object(raw.get('host',{}), ['runtime_directory','system_app','decoder_route'], 'host')
    session = _object(raw.get('session',{}), ['seconds'], 'session')
    transport = backend.get('transport','ssh')
    if transport not in ('usb','ssh'):
        raise ConfigError('backend.transport must be usb or ssh')
    path_mode = ssh.get('path_mode', 'native')
    if not isinstance(path_mode, str) or path_mode not in ('native', 'roothide'):
        raise ConfigError('backend.ssh.path_mode must be native or roothide')
    decoder_route = host.get('decoder_route', 'auto')
    if not isinstance(decoder_route, str) or decoder_route not in ('auto', 'normal', 'no_fls'):
        raise ConfigError('host.decoder_route must be auto, normal or no_fls')
    def local(value, name):
        candidate = Path(_text(value,name)).expanduser()
        return (candidate if candidate.is_absolute() else root/candidate).resolve()
    def remote(value, name):
        value = _text(value,name)
        if not value.startswith('/'):
            raise ConfigError(name+' must be an absolute device path')
        return value
    product = _text(usb.get('product_type',''),'backend.usb.product_type',empty=True)
    serial = _text(usb.get('serial',''),'backend.usb.serial',empty=True)
    if transport == 'usb' and not (product or serial):
        raise ConfigError('Select a backend USB serial or product_type')
    if product and not re.fullmatch(r'(iPhone|iPad|iPod)[0-9]+,[0-9]+',product):
        raise ConfigError('backend.usb.product_type must be an Apple product identifier')
    if serial and not re.fullmatch(r'[A-Za-z0-9-]+',serial):
        raise ConfigError('backend.usb.serial must be a device identifier')
    hostname = _text(ssh.get('host','127.0.0.1'),'backend.ssh.host')
    alias = _text(ssh.get('host_key_alias',''),'backend.ssh.host_key_alias',empty=True)
    for value,name in [(hostname,'host'),(alias,'host_key_alias')]:
        if value and (value.startswith('-') or not re.fullmatch(r'[A-Za-z0-9_.:%\[\]-]+',value)):
            raise ConfigError('Invalid backend.ssh.'+name)
    return Config(source, transport, product, serial,
        _integer(usb.get('listen_port',2222),1,65535,'backend.usb.listen_port'),
        _integer(usb.get('device_port',22),1,65535,'backend.usb.device_port'),
        hostname,_integer(ssh.get('port',22),1,65535,'backend.ssh.port'),
        local(ssh.get('user_file','private/bridge/user'),'backend.ssh.user_file'),
        local(ssh.get('identity_file','private/bridge/identity'),'backend.ssh.identity_file'),
        local(ssh.get('known_hosts_file','private/bridge/known_hosts'),'backend.ssh.known_hosts_file'),
        alias,
        remote(ssh.get('remote_endpoint','/var/mobile/Library/Caches/Mirra/bridge/endpoint'),'backend.ssh.remote_endpoint'),
        remote(ssh.get('remote_certificate','/var/mobile/Library/Caches/Mirra/bridge/certificate.der'),'backend.ssh.remote_certificate'),
        _integer(ssh.get('timeout_seconds',12),1,60,'backend.ssh.timeout_seconds'),
        local(backend.get('certificate_file','private/bridge/certificate.der'),'backend.certificate_file'),
        local(host.get('runtime_directory','runtime/host'),'host.runtime_directory'),
        local(host.get('system_app','/System/Applications/iPhone Mirroring.app'),'host.system_app'),
        _integer(session.get('seconds',1800),30,1800,'session.seconds'), path_mode, decoder_route)
