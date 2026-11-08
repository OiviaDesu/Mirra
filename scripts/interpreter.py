"""Pin, package and verify a relocatable Python runtime and its notices."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import stat
import subprocess
import tarfile
import tempfile

from configuration import ROOT
from state import write_state

ENTRYPOINT = 'bin/python3'
MACHO = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}


def digest(path):
    with Path(path).open('rb') as stream:
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
        return value.hexdigest()


def fetch(specification, cache):
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / specification['sha256']
    if not destination.exists():
        descriptor, name = tempfile.mkstemp(prefix='.download', dir=cache)
        os.close(descriptor)
        temporary = Path(name)
        try:
            subprocess.run(['/usr/bin/curl', '--fail', '--silent', '--show-error', '--location',
                            '--proto', '=https', '--proto-redir', '=https', '--max-time', '180',
                            specification['url'], '--output', str(temporary)], check=True, timeout=190)
            if digest(temporary) != specification['sha256']:
                raise ValueError('Python archive digest differs from the reviewed pin')
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    if destination.is_symlink() or digest(destination) != specification['sha256']:
        raise ValueError('Cached Python archive differs from the reviewed pin')
    return destination


def member_bytes(archive, member, seen=()):
    if member.name in seen or len(seen) > 8:
        raise ValueError('Cyclic runtime archive link')
    if member.issym() or member.islnk():
        linked = member.linkname if member.islnk() else posixpath.join(posixpath.dirname(member.name), member.linkname)
        name = posixpath.normpath(linked)
        if not name.startswith('python/') or '..' in PurePosixPath(name).parts:
            raise ValueError('Runtime archive link leaves its directory')
        return member_bytes(archive, archive.getmember(name), (*seen, member.name))
    if not member.isfile() or not 0 <= member.size <= 128 * 1024 * 1024:
        raise ValueError('Unexpected runtime archive member')
    return archive.extractfile(member).read()


def extract_runtime(archive_path, destination):
    destination.mkdir(parents=True)
    total = 0
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not member.name.startswith('python/'):
                raise ValueError('Invalid runtime archive path')
            relative = name.relative_to('python')
            if member.isdir() or (str(relative) != ENTRYPOINT and relative.parts[0] != 'lib'):
                continue
            data = member_bytes(archive, member)
            total += len(data)
            if total > 256 * 1024 * 1024:
                raise ValueError('Runtime archive is larger than expected')
            target = destination / str(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(data)
            target.chmod(0o700 if data[:4] in MACHO else 0o600)
    if not (destination / ENTRYPOINT).is_file():
        raise ValueError('Runtime has no Python executable')


def extract_licenses(archive_path, destination):
    # Run this mode with the newly unpacked Python (3.14+ reads zstd natively).
    # All archive bytes are pinned before this process is started.
    with tarfile.open(archive_path) as archive:
        metadata = json.load(archive.extractfile('python/PYTHON.json'))
        paths = {metadata['license_path']}
        for alternatives in metadata['build_info']['extensions'].values():
            for extension in alternatives:
                paths.update(extension.get('license_path', []))
        for member in archive.getmembers():
            if member.name.startswith('python/licenses/') and member.isfile():
                paths.add(member.name.removeprefix('python/'))
        for name in sorted(paths):
            if not name.startswith('licenses/') or '..' in PurePosixPath(name).parts:
                raise ValueError('Invalid upstream license path')
            data = member_bytes(archive, archive.getmember('python/' + name))
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data); target.chmod(0o600)
        write_state(destination / 'distribution.json', metadata)


def validate_runtime(root):
    root = Path(root)
    value = json.loads((root / 'manifest.json').read_text())
    if value.get('schema_version') != 1 or value.get('executable') != ENTRYPOINT:
        raise ValueError('Invalid Python runtime manifest')
    files = value.get('files', {})
    if not isinstance(files, dict) or ENTRYPOINT not in files or 'licenses/LICENSE.cpython.txt' not in files:
        raise ValueError('Incomplete Python runtime')
    for name, expected in files.items():
        relative = PurePosixPath(name)
        path = root / name
        if relative.is_absolute() or '..' in relative.parts or path.is_symlink():
            raise ValueError('Invalid Python runtime path')
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or digest(path) != expected:
            raise ValueError('Python runtime file changed: ' + name)
    return value


def copy_runtime(source, destination):
    manifest = validate_runtime(source)
    destination.mkdir(parents=True)
    files = []
    for name in [*manifest['files'], 'manifest.json']:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        with target.open('rb') as stream:
            executable = stream.read(4) in MACHO
        target.chmod(0o700 if executable else 0o600)
        files.append(target)
    validate_runtime(destination)
    return files


def build_runtime(root=ROOT):
    root = Path(root)
    specification = json.loads((root / 'config/python.json').read_text())
    cache = root / 'build/downloads'
    archive = fetch(specification['runtime'], cache)
    licenses = fetch(specification['licenses'], cache)
    build = root / 'build/python' / specification['runtime']['sha256'][:12]
    if build.exists():
        validate_runtime(build)
        return build
    build.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.stage', dir=build.parent)) / 'python'
    extract_runtime(archive, stage)
    subprocess.run([str(stage / ENTRYPOINT), '-E', '-s', '-B', str(root / 'scripts/interpreter.py'),
                    '--licenses', str(licenses), '--output', str(stage)], check=True, timeout=60)
    write_state(stage / 'origin.json', specification)
    # Resign the copied Mach-O files, never the download cache. This is local
    # signing, not Developer ID signing or notarization.
    for path in sorted(stage.rglob('*')):
        if not path.is_file():
            continue
        with path.open('rb') as stream:
            is_code = stream.read(4) in MACHO
        if is_code:
            subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(path)],
                           capture_output=True, check=True, timeout=15)
    files = {str(p.relative_to(stage)): digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}
    write_state(stage / 'manifest.json', {'schema_version': 1, 'executable': ENTRYPOINT,
                                        'version': specification['version'], 'files': files})
    validate_runtime(stage)
    stage.rename(build)
    return build


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--licenses', type=Path)
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    if arguments.licenses:
        extract_licenses(arguments.licenses, arguments.output)
    else:
        print(build_runtime())
