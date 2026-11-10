"""Keep failed login-service startup from becoming a privacy-prompt loop."""
import json
import os
from pathlib import Path
import signal
import stat
import threading

from lifetime import emit_event
from processes import process_birth
from state import write_state


class StartupError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require_container(path):
    # Python 3.14's is_dir() hides permission failures as False. Keep a denied
    # request distinct from a system app that has never created its container.
    try:
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise FileNotFoundError(str(path))
    except PermissionError as error:
        raise StartupError('container_access_denied',
                           'macOS denied access to the iPhone Mirroring temporary directory') from error
    except FileNotFoundError as error:
        raise StartupError('container_missing',
                           'Open and quit Apple’s iPhone Mirroring once to initialize its container') from error


def run_service(session, state_path, stop=None):
    """Run one session in this interpreter, then wait for an explicit stop.

    Launchd KeepAlive must not spawn a new requesting Python process after a
    denied permission or failed startup. Backend reconnection stays inside the
    existing session. Waiting here performs no container access or retry.
    """
    state_path = Path(state_path)
    stop = stop if stop is not None else threading.Event()
    birth = process_birth(os.getpid())
    previous = json.loads(state_path.read_text()) if state_path.exists() else {}
    interrupted = previous.get('phase') in ('startup_checks', 'preparing', 'startup_failed')
    state = {'phase': 'startup_checks', 'supervisor_birth': birth}
    if interrupted and 'directory' in previous:
        state['directory'] = previous['directory']
    write_state(state_path, state)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in handlers:
        signal.signal(sig, lambda *_: stop.set())
    code = 'support_startup_failed'
    try:
        try:
            if interrupted:
                raise StartupError('startup_interrupted',
                                   'The previous startup ended before completion; automatic retry is paused')
            status = session(stop)
            message = 'Native support ended with status ' + str(status)
        except StartupError as error:
            code, message = error.code, str(error)
        except PermissionError as error:
            code, message = 'support_access_denied', str(error)
        except (Exception, SystemExit) as error:
            message = type(error).__name__ + ': ' + str(error)
        # Retain the session directory and its cleanup evidence, if one exists.
        current = json.loads(state_path.read_text())
        if current.get('supervisor_birth') != list(birth):
            raise RuntimeError('Support state changed owners during startup')
        state = current
        if not stop.is_set():
            state.update(phase='startup_failed', startup_failure=code)
            write_state(state_path, state)
            emit_event({'NATIVE_STARTUP_PAUSED': True, 'reason': code, 'error': message})
            stop.wait()
        # A deliberate stop rearms startup. An abrupt death while checking
        # prerequisites leaves the marker, so launchd's next run pauses first.
        state['phase'] = 'stopped'
        if 'directory' not in state:
            state['startup_cleanup_verified'] = True
        write_state(state_path, state)
        return 0
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
