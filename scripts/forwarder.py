"""Owned USB forwarding that can wait for a configured backend to reconnect."""
import select
import socket
import socketserver
import threading

from usbmux import connect, list_devices, select_device


class USBForwarder:
    def __init__(self, config):
        if config.transport != 'usb':
            raise ValueError('USB forwarding requires backend.transport=usb')
        self.config = config
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.connections = set()
        self.slots = threading.BoundedSemaphore(8)
        self.server = None
        self.thread = None
        self.failed_connections = 0

    def device_id(self):
        # DeviceID changes after replug. Resolve it for each new stream, then
        # keep that stream attached to exactly one selected device.
        return select_device(list_devices(), product=self.config.product_type,
                             serial=self.config.serial)['DeviceID']

    def forward(self, client):
        remote = None
        if not self.slots.acquire(blocking=False):
            return
        try:
            with self.lock:
                if self.stop.is_set():
                    return
                self.connections.add(client)
            remote = connect(self.device_id(), self.config.device_port)
            with self.lock:
                self.connections.add(remote)
            client.settimeout(8)
            remote.settimeout(8)
            while not self.stop.is_set():
                readable, _, _ = select.select([client, remote], [], [], .5)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (remote if source is client else client).sendall(data)
        except (OSError, ConnectionError, ValueError):
            self.failed_connections += 1
        finally:
            with self.lock:
                self.connections.discard(client)
                if remote is not None:
                    self.connections.discard(remote)
            if remote is not None:
                remote.close()
            self.slots.release()

    def start(self):
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                owner.forward(self.request)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        # bind() proves ownership. An existing listener is never reused or killed.
        self.server = Server(('127.0.0.1', self.config.listen_port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
            kwargs={'poll_interval': .2}, name='backend-usb-forwarder', daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.stop.set()
        if self.server is not None:
            self.server.shutdown()
            with self.lock:
                for connection in self.connections:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(3)
        return {'stopped': self.thread is None or not self.thread.is_alive(),
                'failed_connections': self.failed_connections}
