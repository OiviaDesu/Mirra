"""Exercise the real shim's recovery requests against an isolated local broker."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import threading

ROOT = Path(__file__).resolve().parents[1]


def readn(stream, count):
    data = b''
    while len(data) < count:
        chunk = stream.recv(count - len(data))
        if not chunk:
            raise RuntimeError('probe closed its request')
        data += chunk
    return data


def run_case(name, mode, responses, expected_operations, expected):
    token = secrets.token_hex(32)
    requests = []
    errors = []
    stop = threading.Event()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(.1)

        def serve():
            while not stop.is_set():
                try:
                    stream, _ = listener.accept()
                except socket.timeout:
                    continue
                try:
                    with stream:
                        stream.settimeout(2)
                        size, = struct.unpack('>I', readn(stream, 4))
                        if size > 262144:
                            raise ValueError('oversized probe request')
                        request = json.loads(readn(stream, size))
                        if request.pop('_auth', None) != token:
                            raise ValueError('missing broker authentication')
                        requests.append(request['operation'])
                        index = len(requests) - 1
                        response = responses[index] if index < len(responses) else {'status': -1}
                        data = json.dumps(response).encode()
                        stream.sendall(struct.pack('>I', len(data)) + data)
                except Exception as error:
                    errors.append(str(error))

        thread = threading.Thread(target=serve)
        thread.start()
        env = {k: v for k, v in os.environ.items() if not k.startswith(('MIRRA_KEYSTORE_', 'DYLD_'))}
        env.update(MIRRA_KEYSTORE_BROKER_SOCKET='/tmp/unused-shim-probe.sock',
                   MIRRA_KEYSTORE_BROKER_TCP_PORT=str(listener.getsockname()[1]), MIRRA_KEYSTORE_BROKER_TOKEN=token)
        try:
            reply = subprocess.run([str(ROOT / 'build/keyprobe'), mode], env=env,
                                   capture_output=True, text=True, timeout=5, check=True)
        finally:
            stop.set()
            thread.join(timeout=3)
        if thread.is_alive() or errors:
            raise AssertionError(f'{name}: fixture failure: {errors}')
        actual = json.loads(reply.stdout)
        if actual != expected or requests != expected_operations:
            raise AssertionError(f'{name}: result {actual}; operations {requests}')
        return {'case': name, 'result': actual, 'operations': requests, 'passed': True}


def main():
    results = [
        run_case('one broker cleanup request preserves native reset', 'reset',
                 [{'status': 0}], ['reset_owned_sessions'], {'status': 73, 'native_resets': 1}),
        run_case('backend failure is not hidden by native reset', 'reset',
                 [{'status': -123}], ['reset_owned_sessions'], {'status': -123, 'native_resets': 1}),
        run_case('memory exhaustion cleans owned sessions then retries once', 'session',
                 [{'status': -536870211}, {'status': 0}, {'status': 0, 'session': 0x6F001005}],
                 ['session_signed', 'reset_owned_sessions', 'session_signed'],
                 {'status': 0, 'session': 0x6F001005}),
        run_case('failed cleanup prevents a misleading retry', 'session',
                 [{'status': -536870211}, {'status': -123}],
                 ['session_signed', 'reset_owned_sessions'], {'status': -536870211, 'session': -1}),
    ]
    out = ROOT / 'runtime' / ('aks-shim-checks-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'))
    out.mkdir(parents=True, mode=0o700)
    (out / 'result.json').write_text(json.dumps({'cases': results, 'passed': True}, indent=2) + '\n')
    print(f'{len(results)} shim recovery cases passed; report: {out.relative_to(ROOT)}/result.json')


if __name__ == '__main__':
    main()
