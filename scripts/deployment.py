"""Shared macOS build target and verification of every shipped native binary."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

MINIMUM_MACOS = '15.0'
ARCHITECTURE = 'x86_64'
MAGICS = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe',
          b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def compiler_flags(sdk=None):
    sdk = sdk or os.environ.get('MIRRA_MACOS_SDK') or subprocess.check_output(
        ['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
    return ['-target', f'{ARCHITECTURE}-apple-macos{MINIMUM_MACOS}', '-isysroot', sdk,
            '-Werror=unguarded-availability-new']


def version(value):
    return tuple(int(part) for part in value.split('.')) + (0,) * (3 - len(value.split('.')))


def verify_macos_binary(path):
    architectures = subprocess.check_output(['xcrun', 'lipo', '-archs', str(path)], text=True).split()
    if ARCHITECTURE not in architectures:
        raise ValueError(f'Missing {ARCHITECTURE} slice: {path}')
    output = subprocess.check_output(
        ['xcrun', 'vtool', '-arch', ARCHITECTURE, '-show-build', str(path)], text=True)
    modern = re.findall(r'platform\s+(\w+)\s+minos\s+([\d.]+)', output)
    legacy = re.findall(r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([\d.]+)', output)
    records = modern + [('MACOS', item) for item in legacy]
    if len(records) != 1 or records[0][0] != 'MACOS':
        raise ValueError(f'Expected one macOS deployment target: {path}')
    minimum = records[0][1]
    if version(minimum) > version(MINIMUM_MACOS):
        raise ValueError(f'{path} requires macOS {minimum}; the app promises {MINIMUM_MACOS}')
    return {'architecture': ARCHITECTURE, 'minimum_macos': minimum}


def verify_app(app):
    import plistlib
    from macho import require_ios_endpoint
    app = Path(app)
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if info.get('LSMinimumSystemVersion') != MINIMUM_MACOS:
        raise ValueError('App minimum macOS differs from the compiled target')
    endpoint = app / 'Contents/Resources/Support/build/endpoint'
    require_ios_endpoint(endpoint)
    binaries = {}
    for path in sorted(app.rglob('*')):
        if not path.is_file() or path == endpoint:
            continue
        with path.open('rb') as stream:
            magic = stream.read(4)
        if magic in MAGICS:
            binaries[str(path.relative_to(app))] = verify_macos_binary(path)
    required = {'Contents/MacOS/Mirra', 'Contents/MacOS/password',
                'Contents/Resources/Support/python/bin/python3'}
    if not required.issubset(binaries):
        raise ValueError('App is missing a required native executable')
    return {'minimum_macos': MINIMUM_MACOS, 'architecture': ARCHITECTURE,
            'binaries': binaries, 'ios_endpoint_checked': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk')
    parser.add_argument('--flags', action='store_true')
    parser.add_argument('--stamp', type=Path)
    parser.add_argument('--app', type=Path)
    args = parser.parse_args()
    if args.app:
        print(json.dumps(verify_app(args.app), indent=2))
    elif args.stamp:
        contents = json.dumps(compiler_flags(args.sdk)) + '\n'
        if not args.stamp.exists() or args.stamp.read_text() != contents:
            args.stamp.write_text(contents)
    elif args.flags:
        print(shlex.join(compiler_flags(args.sdk)))
    else:
        parser.error('Choose --flags, --stamp or --app')


if __name__ == '__main__':
    main()
