#!/usr/bin/env python3
"""Run the accepted XS-backed profile and restore sharingd when the session ends."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from configuration import load_config
from lifetime import add_lifetime_arguments,duration_from_arguments,watch_parent_pipe
from logs import BoundedLogs,open_append
from processes import process_birth
from state import write_state
from workspace import BrokerWorkspace, cleanup as cleanup_workspace
from health import health, wait_for_backend_loss

os.umask(0o077)
ROOT = Path(__file__).resolve().parents[1] / "runtime"
SCRIPTS = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
add_lifetime_arguments(parser)
parser.add_argument("--config")
parser.add_argument('--parent-lifeline',action='store_true',help=argparse.SUPPRESS)
args = parser.parse_args()
configuration = load_config(args.config)
duration = duration_from_arguments(parser,args,configuration.seconds)
WORKSPACE = configuration.runtime_directory
WORKSPACE.mkdir(parents=True, exist_ok=True, mode=0o700)
config_args = ["--config", str(configuration.source)] if configuration.source.is_file() else []
# The lock is shared across configured output directories: every session owns
# the same per-user sharingd job. A stale PID file cannot provide that guarantee.
lock_directory = Path.home() / "Library/Caches/Mirra"
lock_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
session_lock = (lock_directory / "session.lock").open("a")
try:
    fcntl.flock(session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("Another Mirroring session controller is running")
STATE = WORKSPACE / "broker-state.json"
if STATE.exists():
    old = json.loads(STATE.read_text())
    existing = subprocess.run(["ps", "-p", str(old.get("pid", 0)), "-o", "command="],
                              capture_output=True, text=True, timeout=5)
    if str(SCRIPTS / "broker.py") in existing.stdout:
        raise RuntimeError("An existing broker is still live; preserve it")
RUN = datetime.now().strftime("%Y%m%d-%H%M%S")
workspace = BrokerWorkspace(WORKSPACE)
private = workspace.path
broker = controller = None
stopping = False
watch_stop = threading.Event()


def stop_handler(*_):
    global stopping
    stopping = True
    if controller is not None and controller.poll() is None:
        controller.terminate()


signal.signal(signal.SIGTERM, stop_handler)
signal.signal(signal.SIGINT, stop_handler)
if args.parent_lifeline:
    watch_parent_pipe(0,stop_handler)
log_limits=BoundedLogs(on_error=stop_handler)


def monitor_backend():
    reason = wait_for_backend_loss(broker, private / 'broker.sock', watch_stop, configuration.timeout + 3)
    if reason is not None and not watch_stop.is_set():
        record['backend_lost'] = True
        record['backend_loss_reason'] = reason
        print(json.dumps({'BACKEND_LOST': True, 'reason': reason, 'action': 'restoring sharingd'}), flush=True)
        if controller.poll() is None:
            controller.terminate()


broker_log = WORKSPACE / ("broker-kdebug-" + RUN + ".log")
control_log = WORKSPACE / ("controller-kdebug-" + RUN + ".log")
record = {"wrapper_pid": os.getpid(), "duration_seconds": duration, "run": RUN,
          "wire_platform": 3, "assert_delay_mode": 2, "pairing_transport": "wifi"}
children = {}


def record_child(name, process):
    birth = process_birth(process.pid)
    if birth is not None:
        children[name] = birth
        write_state(WORKSPACE / 'backend-children.json', children)


try:
    with open_append(broker_log) as output:
        log_limits.watch_stream(broker_log,output)
        broker_command = [sys.executable, '-E', '-s', '-B', str(SCRIPTS / "broker.py"), "--socket",
                          str(private / "broker.sock"), "--state-file", str(STATE), '--parent-lifeline', *config_args]
        broker = subprocess.Popen(broker_command,
                                  stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True,
                                  pass_fds=(workspace.lease,))
        record["broker_pid"] = broker.pid
        record_child('broker', broker)
        deadline = time.monotonic() + 13 + 3 * configuration.timeout
        while time.monotonic() < deadline:
            if stopping:
                raise InterruptedError("requested stop")
            if broker.poll() is not None:
                raise RuntimeError("USB broker exited; inspect private broker log")
            try:
                if health(private / "broker.sock").get("ready"):
                    break
            except (OSError, ValueError, ConnectionError):
                pass
            time.sleep(.2)
        else:
            raise RuntimeError("USB broker did not become ready")
        print(json.dumps({"USB_BROKER_READY": True, "run": RUN}), flush=True)
        with open_append(control_log) as control_output:
            log_limits.watch_stream(control_log,control_output)
            lifetime_args = ['--until-stopped'] if duration is None else ['--seconds',str(duration)]
            controller_command = [sys.executable, '-E', '-s', '-B', str(SCRIPTS / "sharing.py"), *lifetime_args, '--parent-lifeline', *config_args]
            controller = subprocess.Popen(controller_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, bufsize=1,
                                          pass_fds=(workspace.lease,))
            record_child('controller', controller)
            record.update(controller_pid=controller.pid, broker_log=broker_log.name,
                          controller_log=control_log.name)
            threading.Thread(target=monitor_backend, daemon=True).start()
            (WORKSPACE / "session-active.json").write_text(json.dumps(record, indent=2) + "\n")
            for line in controller.stdout:
                control_output.write(line)
                control_output.flush()
                try:
                    event = json.loads(line)
                    if "WORKSPACE_RUNNING" in event:
                        print(json.dumps({"NATIVE_WORKSPACE_READY": True, "controller_pid": controller.pid,
                                          "run": RUN}), flush=True)
                    elif "ORIGINAL_RESTORED" in event:
                        print(json.dumps({"ORIGINAL_RESTORED": event["ORIGINAL_RESTORED"]}), flush=True)
                    elif "RESTORE_ERROR" in event:
                        print(json.dumps({"RESTORE_ERROR": True}), flush=True)
                except ValueError:
                    pass
            record["controller_exit_code"] = controller.wait(timeout=10)
finally:
    watch_stop.set()
    if controller is not None and controller.poll() is None:
        controller.terminate()
        try:
            controller.wait(timeout=55)
        except subprocess.TimeoutExpired:
            record["controller_restore_timeout"] = True
    if broker is not None and broker.poll() is None:
        broker.terminate()
        try:
            broker.wait(timeout=25)
        except subprocess.TimeoutExpired:
            record["broker_cleanup_timeout"] = True
    workspace.close()
    state = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.apple.sharingd"],
                           capture_output=True, text=True, timeout=15)
    record["original_sharingd_restored"] = (state.returncode == 0
        and "path = /System/Library/LaunchAgents/com.apple.sharingd.plist" in state.stdout
        and "program = /usr/libexec/sharingd" in state.stdout
        and "DYLD_INSERT_LIBRARIES" not in state.stdout and "MIRRA_KEYSTORE" not in state.stdout)
    if record['original_sharingd_restored']:
        try:
            record['broker_workspace_cleanup'] = cleanup_workspace(WORKSPACE)
        except (OSError, ValueError, RuntimeError) as error:
            record['broker_workspace_cleanup'] = {'removed': False, 'error': str(error)}
    record["finished"] = True
    record['log_limits']=log_limits.close()
    (WORKSPACE / ("session-result-" + RUN + ".json")).write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"SESSION_FINISHED": True, "original_sharingd_restored": record["original_sharingd_restored"],
                      "broker_terminal": broker is None or broker.poll() is not None, "run": RUN}), flush=True)

if (record.get("controller_exit_code") != 0 or not record["original_sharingd_restored"]
        or record.get("backend_lost") or record.get("broker_cleanup_timeout")
        or record.get("controller_restore_timeout") or record['log_limits'].get('error')):
    sys.exit(1)
