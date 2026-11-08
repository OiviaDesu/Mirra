#!/usr/bin/env python3
"""Carry native sharingd AKS requests to an isolated keybag on the configured backend."""
import argparse
import ctypes
import json
import os
import secrets
import signal
import socket
import socketserver
import struct
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from transport import Endpoint
from configuration import load_config
from keystore import AksBroker
from lifetime import watch_parent_pipe
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--socket", type=Path, required=True)
parser.add_argument("--state-file", type=Path, required=True)
parser.add_argument("--config")
parser.add_argument('--parent-lifeline',action='store_true',help=argparse.SUPPRESS)
args = parser.parse_args()
configuration = load_config(args.config)
if len(os.fsencode(args.socket)) >= 104 or args.socket.exists():
    raise SystemExit("Socket path is too long or already exists")
parent = args.socket.parent.stat()
if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
    raise SystemExit("Socket parent must be owned by this user with mode 0700")
certificate_bytes = configuration.certificate_file.read_bytes()
if not certificate_bytes:
    raise SystemExit("Backend device certificate is empty")
endpoint = Endpoint(configuration)
libc = ctypes.CDLL(None, use_errno=True)
libc.getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
libc.getpeereid.restype = ctypes.c_int
lock = threading.Lock()
auth_token = secrets.token_hex(32)
broker = AksBroker(endpoint)


def readn(sock, count):
    data = bytearray()
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise ConnectionError("client closed")
        data.extend(part)
    return bytes(data)


class Request(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            is_unix = self.request.family == socket.AF_UNIX
            if is_unix:
                uid, gid = ctypes.c_uint(), ctypes.c_uint()
                if libc.getpeereid(self.request.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0 or uid.value != os.getuid():
                    return
            self.request.settimeout(15)
            size = struct.unpack(">I", readn(self.request, 4))[0]
            if size > 262144:
                return
            request = json.loads(readn(self.request, size))
            if not isinstance(request, dict):
                return
            if not is_unix and not secrets.compare_digest(str(request.pop("_auth", "")), auth_token):
                return
            with lock:
                response = broker.execute(request)
            data = json.dumps(response).encode()
            self.request.sendall(struct.pack(">I", len(data)) + data)
            print(json.dumps({"operation": request.get("operation"), "status": response.get("status_hex", response.get("status")),
                              "bytes": response.get("bytes"), "error": response.get("error")}), flush=True)
        except Exception as error:
            print(json.dumps({"transport_error": f"{type(error).__name__}: {error}"}), flush=True)


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


try:
    if not endpoint.ready.get("dcrt_bytes"):
        raise RuntimeError("Backend endpoint has no device certificate")
    generated = broker.execute({"operation": "generate", "key_class": 9})
    if generated.get("status") != 0:
        raise RuntimeError("Backend cannot create an attested signing key: " + str(generated.get("status_hex", generated.get("error"))))
    validated = broker.execute({"operation": "validate", "local_ltk": generated["data"]})
    if validated.get("status") != 0:
        raise RuntimeError("Backend could not validate its fresh attested key")
except BaseException:
    endpoint.close()
    raise
server = Server(str(args.socket), Request)
args.socket.chmod(0o600)
tcp_server = TCPServer(("127.0.0.1", 0), Request)
certificate = args.socket.parent / "certificate.der"
certificate.write_bytes(certificate_bytes)
certificate.chmod(0o600)
if args.state_file:
    descriptor = os.open(args.state_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as state:
        json.dump({"pid": os.getpid(), "socket": str(args.socket), "directory": str(args.socket.parent),
                   "tcp_port": tcp_server.server_address[1], "auth_token": auth_token,
                   "certificate": str(certificate)}, state)
        state.write("\n")
    args.state_file.chmod(0o600)
threading.Thread(target=tcp_server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()


def stop(_signal, _frame):
    threading.Thread(target=server.shutdown, daemon=True).start()
    threading.Thread(target=tcp_server.shutdown, daemon=True).start()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
if args.parent_lifeline:
    watch_parent_pipe(0,lambda:stop(None,None))
print(json.dumps({"ready": True, "socket": str(args.socket), "tcp_port": tcp_server.server_address[1],
                  "dcrt_bytes": endpoint.ready.get("dcrt_bytes"), "attested_key_validated": True,
                  "certificate_binding_verified": endpoint.certificate_binding_verified}), flush=True)
try:
    server.serve_forever(poll_interval=0.2)
finally:
    tcp_server.shutdown()
    tcp_server.server_close()
    server.server_close()
    endpoint.close()
    args.socket.unlink(missing_ok=True)
