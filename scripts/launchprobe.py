#!/usr/bin/env python3
"""Bounded normal-launch probe; always restore the original CoreServicesUIAgent."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = f'gui/{os.getuid()}'
TARGET = DOMAIN + '/com.apple.coreservices.uiagent'
ORIGINAL = Path('/System/Library/LaunchAgents/com.apple.coreservices.uiagent.plist')
AGENT = '/System/Library/CoreServices/CoreServicesUIAgent.app/Contents/MacOS/CoreServicesUIAgent'
APP = Path('/System/Applications/iPhone Mirroring.app')
EXECUTABLE = APP / 'Contents/MacOS/iPhone Mirroring'


def command(*args, timeout=12):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def agent_state():
    result = command('launchctl', 'print', TARGET)
    if result.returncode:
        return {}
    state = {'injected': 'DYLD_INSERT_LIBRARIES' in result.stdout}
    for line in result.stdout.splitlines():
        key, separator, value = line.strip().partition(' = ')
        if separator and key in ('program', 'pid', 'state'):
            state.setdefault(key, value)
    return state


def main():
    os.umask(0o077)
    if command('pgrep', '-x', 'iPhone Mirroring').returncode == 0:
        raise SystemExit('Close the current Mirroring app before this probe.')
    lock_path = Path.home() / 'Library/Caches/Mirra/session.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = agent_state()
    if before.get('program') != AGENT or before.get('injected'):
        raise SystemExit('Unexpected agent configuration; no changes made.')
    signature = command('codesign', '--verify', '--strict', str(APP))
    if signature.returncode:
        raise SystemExit('Original app signature verification failed.')
    run = ROOT / 'runtime' / ('native-launch-probe-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    run.mkdir(mode=0o700)
    for name in ('launch.dylib', 'marker.dylib'):
        shutil.copy2(ROOT / 'build' / name, run / name)
    settings = run / 'launch-settings.json'
    settings.write_text(json.dumps({'DYLD_INSERT_LIBRARIES': str(run / 'marker.dylib'),
                                   'MIRRA_LAUNCH_PROBE': run.name}) + '\n')
    settings.chmod(0o600)
    # A separate local app tests that unrelated LaunchServices requests receive
    # neither the target's library nor its marker. It displays no user interface.
    control = run / 'Launch Control.app'
    contents = control / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'local.mirra.control',
        'CFBundleExecutable': 'LaunchControl', 'CFBundleName': 'Launch Control',
        'CFBundlePackageType': 'APPL', 'LSUIElement': True,
    }))
    source = run / 'control.c'
    source.write_text('#include <stdio.h>\n#include <stdlib.h>\n'
                      'int main(int argc,char **argv){if(argc!=2)return 64;'
                      'FILE*f=fopen(argv[1],"wx");if(!f)return 1;'
                      'fprintf(f,"{\\"marker_present\\":%s,\\"loader_present\\":%s}\\n",'
                      'getenv("MIRRA_LAUNCH_PROBE")?"true":"false",'
                      'getenv("DYLD_INSERT_LIBRARIES")?"true":"false");return fclose(f);}\n')
    subprocess.run(['xcrun', 'clang', '-Wall', '-Wextra', '-Werror', str(source), '-o',
                    str(contents / 'MacOS/LaunchControl')], check=True, capture_output=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(control)], check=True, capture_output=True)
    configuration = plistlib.loads(ORIGINAL.read_bytes())
    configuration.update(RunAtLoad=True, EnablePressuredExit=False,
        EnvironmentVariables={'DYLD_INSERT_LIBRARIES': str(run / 'launch.dylib'),
                              'MIRRA_LAUNCH_SETTINGS': str(settings)},
        StandardOutPath=str(run / 'agent.stdout.log'), StandardErrorPath=str(run / 'agent.stderr.log'))
    modified = run / 'agent.plist'
    modified.write_bytes(plistlib.dumps(configuration))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    report = {'agent_before': before, 'system_app_sha256_before': hashlib.sha256(EXECUTABLE.read_bytes()).hexdigest(),
              'probe_scope': 'Original LaunchServices startup, marker only; no Mirroring session',
              'native_mirroring_verified': False, 'normal_launch_verified': False}
    unloaded = False
    app_pid = None
    try:
        unloaded = True  # A timeout can occur after launchd has removed the job.
        reply = command('launchctl', 'bootout', TARGET)
        if reply.returncode:
            raise RuntimeError('Agent bootout failed: ' + reply.stderr.strip())
        reply = command('launchctl', 'bootstrap', DOMAIN, str(modified))
        if reply.returncode:
            raise RuntimeError('Probe agent bootstrap failed: ' + reply.stderr.strip())
        for _ in range(80):
            state = agent_state()
            path = run / 'agent.stderr.log'
            log = path.read_text(errors='replace') if path.exists() else ''
            if state.get('pid') and f'MIRRA_LAUNCH_ADAPTER_READY pid={state["pid"]}' in log:
                report['probe_agent_pid'] = int(state['pid'])
                break
            if stop.wait(.2):
                raise RuntimeError('Probe interrupted.')
        else:
            raise RuntimeError('Agent did not load the launch adapter.')
        # No app-specific environment is supplied by these launch commands.
        control_result = run / 'negative-control.json'
        result = command('/usr/bin/open', '-W', str(control), '--args', str(control_result))
        if result.returncode or not control_result.exists():
            raise RuntimeError('Unrelated-app launch control did not finish.')
        report['negative_control'] = json.loads(control_result.read_text())
        result = command('/usr/bin/open', '-b', 'com.apple.ScreenContinuity')
        if result.returncode:
            raise RuntimeError('Normal original-app launch failed: ' + result.stderr.strip())
        for _ in range(40):
            pids = command('pgrep', '-x', 'iPhone Mirroring').stdout.split()
            if len(pids) == 1:
                app_pid = int(pids[0])
                images = command('/usr/sbin/lsof', '-p', str(app_pid)).stdout
                if str(run / 'marker.dylib') in images:
                    report['marker_library_loaded'] = True
                    break
            if stop.wait(.2):
                raise RuntimeError('Probe interrupted.')
        report['app_pid'] = app_pid
        report['target_environment_applied'] = 'MIRRA_LAUNCH target_environment_applied' in (run / 'agent.stderr.log').read_text(errors='replace')
        report['normal_launch_verified'] = bool(report.get('marker_library_loaded') and report['target_environment_applied']
            and report['negative_control'] == {'marker_present': False, 'loader_present': False})
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        report['error'] = str(error)
    finally:
        # The probe does not preserve a marker-loaded app after its agent ends.
        try:
            if app_pid is not None:
                current = command('pgrep', '-x', 'iPhone Mirroring').stdout.split()
                if current == [str(app_pid)]:
                    command('/usr/bin/osascript', '-e', 'tell application id "com.apple.ScreenContinuity" to quit')
        except (OSError, subprocess.SubprocessError) as error:
            report['app_cleanup_error'] = str(error)
        try:
            if unloaded:
                state = agent_state()
                if state.get('injected'):
                    command('launchctl', 'bootout', TARGET)
                if not agent_state():
                    restored = command('launchctl', 'bootstrap', DOMAIN, str(ORIGINAL))
                    report['restore_bootstrap_exit'] = restored.returncode
                    if restored.returncode:
                        report['restore_error'] = restored.stderr.strip()
            report['agent_after'] = agent_state()
        except (OSError, subprocess.SubprocessError) as error:
            report['restore_error'] = str(error)
            report['agent_after'] = {}
        report['original_agent_restored'] = report['agent_after'].get('program') == AGENT and not report['agent_after'].get('injected')
        report['system_app_unchanged'] = hashlib.sha256(EXECUTABLE.read_bytes()).hexdigest() == report['system_app_sha256_before']
        report['system_app_signature_verified'] = command('codesign', '--verify', '--strict', str(APP)).returncode == 0
        (run / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'run': str(run), **report}, indent=2), flush=True)
    return 0 if report['normal_launch_verified'] and report['original_agent_restored'] and report['system_app_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
