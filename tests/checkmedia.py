#!/usr/bin/env python3
"""Verify media contract invariants and ABI rejection in isolated processes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'


def main():
    directory = ROOT / 'runtime' / ('inspectors-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    directory.mkdir(parents=True, mode=0o700)

    def run(name, command, mismatch=False):
        environment = dict(os.environ)
        environment.pop('DYLD_INSERT_LIBRARIES', None)
        if mismatch:
            environment['DYLD_INSERT_LIBRARIES'] = str(BUILD / 'mismatch.dylib')
        reply = subprocess.run([str(arg) for arg in command], env=environment,
            capture_output=True, text=True, timeout=20)
        (directory / (name + '.json')).write_text(reply.stdout)
        (directory / (name + '.log')).write_text(reply.stderr)
        return reply.returncode, json.loads(reply.stdout)

    with ThreadPoolExecutor(max_workers=4) as executor:
        current = executor.submit(run, 'current', [BUILD / 'mediaprobe', BUILD / 'media.dylib'])
        bad_report = executor.submit(run, 'changed-abi-report', [BUILD / 'inspector'], True)
        bad_hooks = executor.submit(run, 'changed-abi-hooks', [BUILD / 'mediaprobe',
            BUILD / 'media.dylib', '--expect-incompatible'], True)
        new_code, new = current.result()
        report_code, report = bad_report.result()
        hooks_code, hooks = bad_hooks.result()
    passed = (new_code == hooks_code == 0 and new.get('passed') is True and len(new.get('cases', [])) == 40
        and report_code == 1 and report.get('compatible') is False
        and hooks.get('passed') is True and hooks.get('unchanged_hooks') == 4)
    value = {'passed': passed, 'hook_cases': len(new.get('cases', [])),
        'changed_abi_rejected_without_hooks': hooks.get('passed') is True,
        'adapter_sha256': hashlib.sha256((BUILD / 'media.dylib').read_bytes()).hexdigest(),
        'scope': 'Hook behavior and ABI rejection in isolated processes; no real connection or media validation'}
    (directory / 'result.json').write_text(json.dumps(value, indent=2) + '\n')
    print(json.dumps({'passed': passed, 'hook_cases': value['hook_cases'], 'results': str(directory)}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
