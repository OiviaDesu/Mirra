#!/usr/bin/env python3
"""One native-window setup action. Passwords go directly from askpass to SSH."""
import argparse
import fcntl
import json
import os
import sys
import subprocess
from onboarding import SetupWizard
from activity import capture, FileLog, emit


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'devices', 'begin', 'scan', 'trust', 'connect', 'provision', 'finish'))
    parser.add_argument('--askpass')
    args = parser.parse_args()
    data = sys.stdin.buffer.read(16385)
    if len(data) > 16384:
        parser.error('Setup request is too large')
    payload = json.loads(data or b'{}')
    if not isinstance(payload, dict):
        parser.error('Expected one setup request object')
    wizard = SetupWizard(askpass=args.askpass)
    identifier = payload.get('id')
    # Provisioning commands may print SSH diagnostics; only the final structured
    # result goes to the UI's stdout channel. No password enters this process.
    descriptor = os.dup(1)
    with os.fdopen(descriptor, 'w') as output, capture(FileLog(wizard.service.base / 'logs')):
        os.dup2(2, 1)
        wizard.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (wizard.base / 'lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action not in ('status', 'devices'):
                emit('Setup', 'action started', action=args.action)
            if args.action == 'status': result = wizard.describe()
            elif args.action == 'devices': result = wizard.devices()
            elif args.action == 'begin': result = wizard.begin(payload)
            elif args.action == 'trust': result = wizard.trust(identifier, payload.get('fingerprint'))
            else: result = getattr(wizard, args.action)(identifier)
            reply = {'ok': True, 'setup': result}
            if args.action not in ('status', 'devices'):
                emit('Setup', 'action result', action=args.action, result=result)
        except BlockingIOError:
            reply = {'ok': False, 'setup': {'phase': 'new', 'error': 'Another setup action is still running. Wait for it to finish, then reopen setup.'}}
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
            emit('Setup', 'action failed', 'error', action=args.action, error_type=type(error).__name__, error=str(error))
            reply = {'ok': False, 'setup': wizard.failure(identifier, args.action, error)}
        finally:
            lock.close()
        output.write(json.dumps(reply) + '\n'); output.flush()
    return 0 if reply['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
