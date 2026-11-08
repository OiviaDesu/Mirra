"""Drain subprocess diagnostics into a fixed-size memory tail, never a disk file."""
import os
import select
import threading


class BoundedStderr:
    def __init__(self, stream, limit=65536, on_line=None):
        if limit < 1:
            raise ValueError('Diagnostic tail limit must be positive')
        self.stream = stream
        self.limit = limit
        self.tail = bytearray()
        self.total_bytes = 0
        self.on_line = on_line
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._drain, name='endpoint-stderr', daemon=True)
        self.thread.start()

    def _drain(self):
        descriptor = self.stream.fileno()
        pending = b''
        try:
            while not self.stop.is_set():
                if not select.select([descriptor], [], [], .2)[0]:
                    continue
                chunk = os.read(descriptor, 16384)
                if not chunk:
                    return
                with self.lock:
                    self.total_bytes += len(chunk)
                    self.tail.extend(chunk)
                    if len(self.tail) > self.limit:
                        del self.tail[:-self.limit]
                if self.on_line:
                    pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        try:self.on_line(line)
                        except (OSError, ValueError):pass
                    if len(pending) > self.limit:
                        pending = b''
        finally:
            if pending and self.on_line:
                try:self.on_line(pending)
                except (OSError, ValueError):pass
            self.stream.close()

    def snapshot(self):
        # Do not automatically publish stderr: SSH or endpoint diagnostics can
        # contain device-specific details. Tests/callers can inspect it locally.
        with self.lock:
            return bytes(self.tail), self.total_bytes

    def close(self):
        # An inherited stderr pipe must not make shutdown wait for a grandchild.
        self.stop.set()
        self.thread.join(1)
        if self.thread.is_alive():
            raise RuntimeError('Endpoint diagnostic reader did not stop')
