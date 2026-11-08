#!/usr/bin/env python3
"""Stream actual Apple Mirroring log messages to the viewer, with redaction."""
from datetime import datetime, timezone
import argparse
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

from redaction import redact_text
from activity import FileLog

NOISY_FRAMEWORKS = {'com.apple.AppKit', 'com.apple.defaults', 'com.apple.launchservices'}
PREDICATE = ('process == "iPhone Mirroring" OR (process == "sharingd" AND composedMessage BEGINSWITH "MIRRA_KEYSTORE")'
             ' OR (process == "avconferenced" AND (composedMessage CONTAINS "MIRRA_DECODER"'
             ' OR composedMessage CONTAINS "VCPDec" OR composedMessage CONTAINS "video decoder lib"'
             ' OR composedMessage CONTAINS "Dec_fps")) OR process BEGINSWITH "VTDecoderXPCService"')


def predicate(verbose=False):
    if verbose:
        return PREDICATE
    frameworks = ' OR '.join('subsystem == "' + name + '"' for name in sorted(NOISY_FRAMEWORKS))
    return '(' + PREDICATE + ') AND NOT (logType == "debug" AND (' + frameworks + '))'


def project(raw, verbose=False):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get('eventMessage'), str):
        return None
    if not verbose and str(value.get('messageType', '')).lower() == 'debug' and value.get('subsystem') in NOISY_FRAMEWORKS:
        return None
    message = redact_text(value['eventMessage'])
    if not message:
        return None
    level = str(value.get('messageType', 'Default')).lower()
    level = 'error' if level in ('error', 'fault') else ('debug' if level == 'debug' else 'info')
    pid = value.get('processID')
    if type(pid) is int:
        message = f'pid={pid} ' + message
    subsystem = value.get('subsystem')
    category = value.get('category')
    prefix = '/'.join(redact_text(v) for v in (subsystem, category) if isinstance(v, str) and v)
    if prefix:
        message = prefix[:160] + ': ' + message
    path = value.get('processImagePath', '')
    process = Path(path).name if isinstance(path, str) else ''
    source = ('AVConference' if process == 'avconferenced' else
              'VideoToolbox' if process.startswith('VTDecoderXPCService') else 'Apple Mirroring')
    return {'source': source, 'level': level, 'message': message[:4000],
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'event_timestamp': redact_text(value.get('timestamp', ''))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verbose', action='store_true', help='Include routine macOS framework debug messages')
    args = parser.parse_args()
    stop = False

    def request_stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    command = ['/usr/bin/log', 'stream', '--style', 'ndjson', '--level', 'debug', '--predicate', predicate(args.verbose)]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, bufsize=0)
    pending = []
    buffers = {'stdout': b'', 'stderr': b''}
    last_flush = time.monotonic()
    storage = FileLog(Path.home() / 'Library/Application Support/Mirra/logs', name='native.log')
    storage_error = False
    maintenance_day = None

    def maintain():
        nonlocal maintenance_day, storage_error
        day = datetime.now().date()
        if maintenance_day == day:
            return
        maintenance_day = day
        try:
            removed = storage.maintain()
            if removed:
                pending.append({'source':'Logs','level':'info',
                    'message':f'Removed {len(removed)} expired log files: ' + ', '.join(removed),
                    'observed_at':datetime.now(timezone.utc).isoformat()})
        except (OSError, ValueError) as error:
            if not storage_error:
                storage_error = True
                pending.append({'source':'Log file','level':'error','message':redact_text(str(error)),
                    'observed_at':datetime.now(timezone.utc).isoformat()})

    def flush():
        nonlocal pending, last_flush
        if pending:
            print(json.dumps({'ok': True, 'entries': pending[-80:]}, ensure_ascii=False), flush=True)
            pending = []
        last_flush = time.monotonic()

    try:
        maintain()
        with selectors.DefaultSelector() as selector:
            selector.register(sys.stdin, selectors.EVENT_READ, 'parent')
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            while not stop:
                maintain()
                for key, _ in selector.select(.2):
                    data = os.read(key.fd, 16384)
                    kind = key.data
                    if kind == 'parent':
                        if not data:
                            stop = True
                        continue
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    buffers[kind] += data
                    while b'\n' in buffers[kind]:
                        line, buffers[kind] = buffers[kind].split(b'\n', 1)
                        item = project(line, args.verbose) if kind == 'stdout' else {
                            'source':'System log', 'level':'error', 'message':redact_text(line),
                            'observed_at':datetime.now(timezone.utc).isoformat()}
                        if item:
                            pending.append(item)
                            try:
                                storage(dict(item, timestamp=item['observed_at']))
                            except (OSError, ValueError) as error:
                                if not storage_error:
                                    storage_error = True
                                    pending.append({'source':'Log file','level':'error','message':redact_text(str(error)),
                                        'observed_at':datetime.now(timezone.utc).isoformat()})
                    if len(buffers[kind]) > 262144:
                        buffers[kind] = b''
                if len(pending) >= 80 or time.monotonic() - last_flush >= .5:
                    flush()
                if process.poll() is not None and not any(k.data != 'parent' for k in selector.get_map().values()):
                    pending.append({'source':'System log', 'level':'warning',
                        'message':f'log stream exited with code {process.returncode}.',
                        'observed_at':datetime.now(timezone.utc).isoformat()})
                    break
        flush()
    except BrokenPipeError:
        pass
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
        process.stderr.close()
    return 0


if __name__ == '__main__':
    main()
