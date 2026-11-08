#!/usr/bin/env python3
"""Append an actual UI event to the private app log."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys

from activity import FileLog
from redaction import redact_value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=Path.home() / 'Library/Application Support/Mirra/logs')
    args = parser.parse_args()
    data = sys.stdin.buffer.read(16385)
    if len(data) > 16384:
        return 1
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        return 1
    if not isinstance(value, dict) or not isinstance(value.get('message'), str):
        return 1
    value = redact_value(value)
    value['timestamp'] = datetime.now(timezone.utc).isoformat()
    FileLog(args.base, name='app.log')(value)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
