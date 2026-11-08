#!/usr/bin/env python3
"""Explicit support-window actions using the existing service controller."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from configuration import load_config
from installation import NativeService
from readiness import observe
from activity import capture, FileLog, emit, poll_errors
from preflight import check as check_connection


def control():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'start', 'stop', 'install', 'preflight'))
    parser.add_argument('--profile')
    args = parser.parse_args()
    if args.profile and args.action != 'install':
        parser.error('A profile can only be selected for installation')
    service = NativeService()
    emit('Service', 'request', action=args.action)
    try:
        if args.action == 'preflight':
            checked = check_connection(service)
            value = {'ok': True, 'report': checked['report'], 'preflight': checked}
        elif args.action == 'install':
            if args.profile:
                if service.registration():
                    raise RuntimeError('Stop support before selecting a different bridge')
                service.prepare(load_config(args.profile), replace_package=service.package.exists())
            elif not service.package.exists():
                raise RuntimeError('Choose a provisioned bridge profile first')
            else:
                service.update_saved()
            if args.profile:
                service.install()
        elif args.action in ('start', 'stop'):
            getattr(service, args.action)()
        if args.action != 'preflight':
            value = {'ok': True, 'report': observe(service)}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError):
        # Exception messages can contain private paths/account information.
        # The separate CLI remains available for owner-local detailed diagnosis.
        value = {'ok': False, 'message': 'The action could not finish. Refresh status before trying again. If the problem persists, use the setup guide and keep the existing diagnostics.',
                 'report': observe(service)}
    print(json.dumps(value))
    emit('Service', 'reply', **value)
    return 0 if value['ok'] else 1


def main():
    log = FileLog(Path.home() / 'Library/Application Support/Mirra/logs', name='control.log')
    if sys.argv[1:2] == ['status']:
        log = poll_errors(log)
    with capture(log):
        return control()


if __name__ == '__main__':
    raise SystemExit(main())
