"""Real loopback server lifecycle shared by isolated Agent/Studio checks."""
from contextlib import contextmanager
import socket
from threading import Thread
from time import monotonic, sleep

import uvicorn

from creatoros.web.server import StudioServer


@contextmanager
def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = StudioServer(uvicorn.Config(app, log_level="error", timeout_graceful_shutdown=3))
    thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 10
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(0.02)
        assert server.started, "isolated Studio did not start"
        yield base
    finally:
        server.should_exit = True
        thread.join(20)
        sock.close()
        assert not thread.is_alive(), "Studio did not shut down"
