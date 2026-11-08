"""Explicit session deadlines and a pipe lifeline for restoration controllers."""
import os
import json
import threading
import time


def add_lifetime_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--seconds', type=int, help='Bounded session: 30..1800 seconds')
    group.add_argument('--until-stopped', action='store_true', help='Remove the duration limit; recovery policy is selected separately')


def duration_from_arguments(parser, args, default):
    if args.until_stopped:
        return None
    seconds = default if args.seconds is None else args.seconds
    if not 30 <= seconds <= 1800:
        parser.error('Bounded session duration must be 30..1800 seconds')
    return seconds


def deadline_for(seconds):
    return float('inf') if seconds is None else time.monotonic() + seconds


def emit_event(event):
    """A lost diagnostic reader must not prevent native-agent restoration."""
    try:
        print(json.dumps(event),flush=True)
    except BrokenPipeError:
        sink=os.open(os.devnull,os.O_WRONLY)
        try:os.dup2(sink,1)
        finally:os.close(sink)


def watch_parent_pipe(fd, stop_callback):
    """Parent retains the write end; EOF triggers restoration even after SIGKILL.

    This uses pipe ownership, not a PID check or an observation timeout. Bytes
    are ignored, so diagnostic input cannot accidentally request shutdown.
    """
    def watch():
        while True:
            try:
                value = os.read(fd, 256)
            except InterruptedError:
                continue
            except OSError:
                stop_callback()
                return
            if not value:
                stop_callback()
                return
    thread = threading.Thread(target=watch, name='parent-lifeline', daemon=True)
    thread.start()
    return thread
