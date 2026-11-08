#!/usr/bin/env python3
"""Run an app helper, retain its exact stdout, and record live stderr."""
import argparse
from pathlib import Path
import sys

from activity import capture, FileLog, run, poll_errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('helper', choices=('setup', 'control'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    data = sys.stdin.buffer.read(16385) if args.helper == 'setup' else None
    script = Path(__file__).resolve().with_name(args.helper + '.py')
    base = Path.home() / 'Library/Application Support/Mirra/logs'
    log = FileLog(base, name=args.helper + '.log')
    if args.arguments[:1] in (['status'], ['devices']):
        log = poll_errors(log)
    with capture(log):
        result = run([sys.executable, '-E', '-s', '-B', str(script), *args.arguments],
                     source=args.helper.capitalize() + ' helper', input=data, capture_output=True)
    # Progress/stderr is in the log file; the existing JSON reply channel is
    # byte-identical to running the helper directly.
    sys.stdout.buffer.write(result.stdout)
    sys.stdout.buffer.flush()
    return result.returncode


if __name__ == '__main__':
    main()
