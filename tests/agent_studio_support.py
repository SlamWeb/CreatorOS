"""Real loopback server lifecycle shared by isolated Agent/Studio checks."""
from contextlib import contextmanager
import json
import socket
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4

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


class ForbiddenAction:
    """Keep external side effects blocked in existing isolated live probes."""

    def submit(self, *args, **kwargs):
        raise ValueError("评测禁止启动生产、安装或重新调研。")


def write_report(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def send_turn(client, doc, text):
    response = client.post(f"/api/agent/sessions/{doc['id']}/turns", json={
        "request_id": str(uuid4()), "expected_version": doc["version"], "text": text})
    response.raise_for_status()
    deadline = monotonic() + 180
    while monotonic() < deadline:
        doc = client.get(f"/api/agent/sessions/{doc['id']}").json()
        if doc["status"] != "running":
            return doc
        sleep(0.5)
    raise TimeoutError("Agent turn exceeded 180 seconds")
