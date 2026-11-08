"""Observe broker health without treating an unanswered query as process loss."""
import json
import socket
import struct
import time


def health(path, timeout=2):
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        message = b'{"operation":"health"}'
        connection.sendall(struct.pack('>I', len(message)) + message)

        def readn(count):
            data = bytearray()
            while len(data) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Broker health observation expired')
                connection.settimeout(remaining)
                chunk = connection.recv(count - len(data))
                if not chunk:
                    raise ConnectionError('Broker closed health connection')
                data.extend(chunk)
            return data

        size = struct.unpack('>I', readn(4))[0]
        if not 0 < size <= 262144:
            raise ValueError('Invalid broker reply length')
        response = json.loads(readn(size))
        if not isinstance(response, dict):
            raise ValueError('Invalid broker health reply')
        return response


def wait_for_backend_loss(process, path, stop, timeout, interval=5):
    """Return a confirmed failure reason, or None when the caller stops watching.

    A terminal Popen result establishes process loss. Two explicit unhealthy
    replies establish broker failure. A missing/malformed/timed-out observation
    establishes neither; poll the same process and socket again.
    """
    failures = 0
    while not stop.wait(interval):
        if process.poll() is not None:
            return 'broker_exited'
        try:
            response = health(path, timeout)
        except (OSError, ValueError):
            continue
        if response.get('ready') is True:
            failures = 0
        elif response.get('ready') is False:
            failures += 1
            if failures >= 2 and not stop.is_set():
                return 'broker_reported_unhealthy'
    return None
