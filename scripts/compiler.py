"""Build the iOS endpoint using a full SDK or a bounded import-stub fallback."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from configuration import ROOT
from state import write_state
from macho import require_ios_endpoint


def command(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Build command failed')
    return result


def write_link_stubs(directory, imports, declarations):
    libraries = declarations['libraries']
    owners = {}
    for name, library in libraries.items():
        for symbol in library['symbols']:
            if symbol in owners:
                raise ValueError('Ambiguous iOS import declaration: ' + symbol)
            owners[symbol] = name
    required = {symbol for symbol in imports if not symbol.startswith('_objc_msgSend$')}
    required.add('_objc_msgSend')  # The linker synthesizes selector-specific stubs.
    unknown = required - owners.keys()
    if unknown:
        raise ValueError('Unreviewed iOS imports: ' + ', '.join(sorted(unknown)))
    for name, library in libraries.items():
        target = (directory / f'{name}.framework/{name}.tbd' if library['framework']
                  else directory / f'lib{name}.tbd')
        target.parent.mkdir(parents=True, exist_ok=True)
        symbols = sorted(required & set(library['symbols']))
        target.write_text('--- !tapi-tbd\ntbd-version: 4\ntargets: [ arm64-ios ]\n'
            'install-name: ' + json.dumps(library['install_name']) + '\nexports:\n'
            '  - targets: [ arm64-ios ]\n    symbols: ' + json.dumps(symbols) + '\n...\n')
    return sorted(required)


def build(sdk_mode='auto', output=None):
    output = Path(output or ROOT / 'build/endpoint').resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    lookup = subprocess.run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'],
                            capture_output=True, text=True, timeout=15)
    full_sdk = lookup.returncode == 0
    if sdk_mode == 'iphoneos' and not full_sdk:
        raise RuntimeError('An iPhoneOS SDK is required for --sdk iphoneos')
    mode = 'iphoneos' if full_sdk and sdk_mode != 'macosx-headers' else 'macosx-headers'
    sdk = lookup.stdout.strip() if mode == 'iphoneos' else command(
        ['xcrun', '--sdk', 'macosx', '--show-sdk-path']).stdout.strip()
    source = ROOT / 'src/phone/endpoint.m'
    report = {'build_method': mode, 'header_sdk': sdk, 'architecture': 'arm64',
        'minimum_ios': '15.0', 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'symbol_map_sha256': hashlib.sha256((source.parent / 'keystore-symbols.h').read_bytes()).hexdigest(),
        'requires_backend_signing': True, 'device_compatibility_verified': False}
    with tempfile.TemporaryDirectory(prefix='endpoint-build-', dir=output.parent) as temporary:
        work = Path(temporary)
        candidate = work / 'endpoint'
        common = ['xcrun', 'clang', '-target', 'arm64-apple-ios15.0', '-isysroot', sdk,
                  '-fobjc-arc', '-fblocks', '-Wall', '-Wextra']
        if mode == 'iphoneos':
            compiled = command([*common, str(source), '-framework', 'Foundation', '-framework', 'Security',
                                '-o', str(candidate)])
            report['compiler_diagnostics'] = compiled.stderr
        else:
            obj = work / 'endpoint.o'
            compiled = command([*common, '-c', str(source), '-o', str(obj)])
            report['compiler_diagnostics'] = compiled.stderr
            imports = command(['xcrun', 'nm', '-u', str(obj)]).stdout.splitlines()
            declarations = json.loads((ROOT / 'config/symbols.json').read_text())
            stubs = work / 'stubs'
            report['reviewed_imports'] = write_link_stubs(stubs, imports, declarations)
            command(['xcrun', 'clang', '-target', 'arm64-apple-ios15.0', '-isysroot', '/',
                '-nostdlib', str(obj), '-L', str(stubs), '-F', str(stubs), '-lSystem', '-lobjc',
                '-framework', 'Foundation', '-framework', 'CoreFoundation', '-framework', 'Security',
                '-o', str(candidate)])
        metadata = command(['xcrun', 'vtool', '-show-build', str(candidate)]).stdout
        report['executable'] = require_ios_endpoint(candidate)
        if 'platform IOS\n' not in metadata or 'minos 15.0\n' not in metadata:
            raise RuntimeError('Built endpoint is not an iOS 15+ Mach-O')
        report['load_commands'] = metadata.replace(str(candidate), output.name)
        report['binary_sha256'] = hashlib.sha256(candidate.read_bytes()).hexdigest()
        os.replace(candidate, output)
    write_state(output.with_suffix('.build.json'), report)
    return {'output': str(output), 'build_method': mode,
            'requires_backend_signing': True, 'report': str(output.with_suffix('.build.json'))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', choices=['auto', 'iphoneos', 'macosx-headers'], default='auto')
    parser.add_argument('--output')
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.sdk, args.output), indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
