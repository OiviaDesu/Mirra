#!/usr/bin/env python3
"""Build a standalone support window with project code, never device access files."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

from configuration import ROOT
from installation import PACKAGE_ADAPTERS, HOST_TOOLS
from distribution import source_payload
from interpreter import build_runtime, copy_runtime
from release import release_info


def main():
    os.umask(0o077)
    release = release_info(ROOT)
    _, manifest = source_payload(ROOT)
    runtime = build_runtime(ROOT)
    build = ROOT / 'build'
    build.mkdir(exist_ok=True)
    versions = build / 'versions.noindex'
    versions.mkdir(exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.stage', dir=versions))
    app = staging / 'Mirra.app'
    contents = app / 'Contents'
    executable = contents / 'MacOS/Mirra'
    support = contents / 'Resources/Support'
    executable.parent.mkdir(parents=True)
    support.mkdir(parents=True)
    files = {}
    for name in manifest['files']:
        if (name.startswith(('scripts/', 'docs/'))
                or '/' not in name and name.endswith('.md')
                or name in ('LICENSE', 'VERSION', 'config/release.json',
                            'config/example.json', 'config/endpoint.plist')):
            destination = support / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
            files[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    for name in PACKAGE_ADAPTERS + HOST_TOOLS:
        source = build / name
        subprocess.run(['codesign', '--verify', str(source)], check=True, capture_output=True, timeout=10)
        destination = support / 'build' / name
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o700)
        files['build/' + name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    endpoint = support / 'build/endpoint'
    shutil.copyfile(build / 'endpoint', endpoint)
    files['build/endpoint'] = hashlib.sha256(endpoint.read_bytes()).hexdigest()
    for path in copy_runtime(runtime, support / 'python'):
        files[str(path.relative_to(support))] = hashlib.sha256(path.read_bytes()).hexdigest()
    # The signed runtime is immutable. Cache writers must not add resources
    # after signing; the service gets its own writable copy during setup.
    embedded = support / 'python'
    for path in [*embedded.rglob('*'), embedded]:
        path.chmod(path.stat().st_mode & ~0o222)
    subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-fblocks', '-Wall', '-Wextra', '-Werror',
                    '-framework', 'AppKit', str(ROOT / 'src/app/app.m'), str(ROOT / 'src/app/wizard.m'), str(ROOT / 'src/app/interface.m'), str(ROOT / 'src/app/logs.m'), str(ROOT / 'src/app/alerts.m'), str(ROOT / 'src/app/authorization.m'), '-o', str(executable)], check=True)
    password = executable.parent / 'password'
    subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror', '-framework', 'AppKit',
                    str(ROOT / 'src/app/password.m'), '-o', str(password)], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(password)], check=True)
    artwork = build / 'artwork'
    subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror', '-framework', 'AppKit',
                    str(ROOT / 'src/app/artwork.m'), '-o', str(artwork)], check=True)
    icons = build / 'Mirra.iconset'
    subprocess.run([str(artwork), str(icons)], check=True)
    subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(icons), '-o', str(contents / 'Resources/Mirra.icns')], check=True)
    info = {'CFBundleExecutable': 'Mirra', 'CFBundleIdentifier': 'local.mirra',
            'CFBundleName': 'Mirra', 'CFBundleDisplayName': 'Mirra',
            'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': release['version'], 'CFBundleVersion': release['build'],
            'CFBundleIconFile': 'Mirra.icns',
            'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '15.0',
            'SupportPython': 'Support/python/bin/python3'}
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    (support / 'manifest.json').write_text(json.dumps({'files': files}, indent=2) + '\n')
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(app)], check=True)
    # Each build gets a separate reviewable path; never replace an open app.
    destination = versions / staging.name.removeprefix('.stage')
    staging.rename(destination)
    link = build / 'Mirra.app'
    temporary_link = build / ('.link' + destination.name)
    temporary_link.symlink_to(Path('versions.noindex') / destination.name / app.name)
    if link.exists() and not link.is_symlink():
        raise RuntimeError('The app shortcut belongs to an existing directory; built app preserved at ' + str(destination))
    os.replace(temporary_link, link)
    print(json.dumps({'app': str(link), 'version': str(destination / app.name), 'bundled_project_files': len(files),
                      'device_credentials_included': False, 'bundled_python': info['SupportPython'],
                      'requires_existing_python': False}, indent=2))


if __name__ == '__main__':
    main()
