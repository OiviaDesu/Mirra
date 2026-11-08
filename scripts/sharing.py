#!/usr/bin/env python3
"""Temporarily run sharingd with the Mirra keystore adapter, then restore Apple's job."""
import argparse
import json
import os
import plistlib
import signal
import socket
import struct
import threading
import time
from pathlib import Path
from configuration import load_config
from lifetime import add_lifetime_arguments,duration_from_arguments,deadline_for,watch_parent_pipe,emit_event
from logs import BoundedLogs
from sharingd import SharingdAgent,service,is_original,ORIGINAL_PLIST

ROOT = Path(__file__).resolve().parents[1] / "runtime"
WORKSPACE = ROOT / "host"
DOMAIN = f"gui/{os.getuid()}"
ORIGINAL = f"{DOMAIN}/com.apple.sharingd"
parser = argparse.ArgumentParser(description=__doc__)
add_lifetime_arguments(parser)
parser.add_argument("--config")
parser.add_argument('--parent-lifeline',action='store_true',help=argparse.SUPPRESS)
args = parser.parse_args()
configuration = load_config(args.config)
WORKSPACE = configuration.runtime_directory
duration = duration_from_arguments(parser,args,configuration.seconds)
LABEL = "com.apple.sharingd"
TARGET = ORIGINAL


def readn(sock, count):
    data = b""
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise ConnectionError("broker disconnected")
        data += part
    return data


broker = json.loads((WORKSPACE / "broker-state.json").read_text())
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(15)
    connection.connect(broker["socket"])
    message = json.dumps({"operation": "health"}).encode()
    connection.sendall(struct.pack(">I", len(message)) + message)
    size = struct.unpack(">I", readn(connection, 4))[0]
    if not 0 < size <= 262144:
        raise SystemExit("Invalid broker health response length")
    health = json.loads(readn(connection, size))
if not health.get("ready"):
    raise SystemExit("Live USB/SEP broker health check failed")
if 'reset_owned_sessions' not in health.get('capabilities', []):
    raise SystemExit('Broker/shim protocol mismatch: rebuild and restart support together')
original_before = service(ORIGINAL)
if not is_original(original_before):
    raise SystemExit(f"Unexpected original service state: {original_before}")
if original_before.get("injected"):
    raise SystemExit("sharingd already has an injected configuration; restore that session before starting another")
if TARGET != ORIGINAL and service(TARGET):
    raise SystemExit("A Mirra sharingd job is already registered")
WORKSPACE.chmod(0o700)
configuration = plistlib.loads(ORIGINAL_PLIST.read_bytes())
configuration.update({
    "Label": LABEL,
    "Program": "/usr/libexec/sharingd",
    "KeepAlive": False,
    "EnvironmentVariables": {
        "DYLD_INSERT_LIBRARIES": str(WORKSPACE / "keystore.dylib"),
        "MIRRA_KEYSTORE_BROKER_SOCKET": broker["socket"],
        "MIRRA_KEYSTORE_BROKER_TCP_PORT": str(broker["tcp_port"]),
        "MIRRA_KEYSTORE_BROKER_TOKEN": broker["auth_token"],
        "MIRRA_KEYSTORE_DCRT_PATH": broker["certificate"],
        "MIRRA_KEYSTORE_DIAGNOSTIC_ERRORS": "1",
    },
    "StandardOutPath": str(WORKSPACE / "sharingd.stdout.log"),
    "StandardErrorPath": str(WORKSPACE / "sharingd.stderr.log"),
})
configuration["EnvironmentVariables"]["MIRRA_ASSERT_DELAY_MODE"] = "2"
configuration["EnvironmentVariables"]["MIRRA_PAIRING_TRANSPORT"] = "wifi"
for name in ["sharingd.stdout.log", "sharingd.stderr.log"]:
    path = WORKSPACE / name
    path.write_text("")
    path.chmod(0o600)
stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop.set())
signal.signal(signal.SIGINT, lambda *_: stop.set())
if args.parent_lifeline:
    watch_parent_pipe(0,stop.set)
log_limits=BoundedLogs(on_error=stop.set)
agent = SharingdAgent(WORKSPACE)
try:
    if stop.is_set():
        raise InterruptedError('Parent ended before sharingd startup')
    agent.start(configuration)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if stop.is_set():
            raise InterruptedError('Parent ended during sharingd startup')
        state = service(TARGET)
        if state.get("state") == "running" and state.get("pid"):
            marker = f"MIRRA_KEYSTORE_SHIM_LOADED pid={state['pid']} enabled=1"
            if marker in (WORKSPACE / "sharingd.stderr.log").read_text(errors="replace"):
                break
        time.sleep(0.2)
    else:
        raise RuntimeError(f"sharingd did not load the adapter: {state}")
    for fd,name in [(1,'sharingd.stdout.log'),(2,'sharingd.stderr.log')]:
        log_limits.watch_native(WORKSPACE/name,int(state['pid']),fd)
    record = {"controller_pid": os.getpid(), "job": TARGET, "state": state,
              "original_before": original_before, "automatic_restore_seconds": duration}
    (WORKSPACE / "sharing.json").write_text(json.dumps(record, indent=2) + "\n")
    emit_event({"WORKSPACE_RUNNING": record})
    deadline = deadline_for(duration)
    while not stop.wait(1) and time.monotonic() < deadline:
        if not service(TARGET).get("pid"):
            raise RuntimeError("Bridge controller exited")
finally:
    try:
        if agent.owned:
            state = agent.close()
            emit_event({"ORIGINAL_RESTORED": state})
    finally:
        (WORKSPACE/'sharingd-log-limits.json').write_text(json.dumps(log_limits.close(),indent=2)+'\n')

if log_limits.error:
    raise SystemExit('Diagnostic log maintenance failed; original sharingd restoration was attempted')
