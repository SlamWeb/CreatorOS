"""Real localhost HTTP/SSE transport; delayed producer is only fault injection."""
import json
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep

import httpx
import uvicorn

from creatoros.runs import ContentRunRepository
from tests.studio_review_fixtures import make_fixture
from creatoros.web.server import StudioServer


def frames(response):
    frame = {}
    for line in response.iter_lines():
        if not line:
            if frame:
                yield frame
                frame = {}
        elif ":" in line and not line.startswith(":"):
            key, value = line.split(":", 1)
            frame[key] = value.strip()


with TemporaryDirectory() as temporary:
    db, service, producer, app = make_fixture(Path(temporary))
    started, release = Event(), Event()
    original = producer.produce_to

    def delayed(**request):
        request["on_thread_started"]("isolated-review-thread")
        started.set()
        assert release.wait(10)
        return original(**request)

    producer.produce_to = delayed
    run = service.create("review-1")
    other = service.create("review-2")
    repository = ContentRunRepository(db)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = StudioServer(uvicorn.Config(app, log_level="error", timeout_graceful_shutdown=3))
    thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 5
        while not server.started and monotonic() < deadline:
            sleep(0.02)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
            base = f"/api/runs/{run.id}"
            initial = client.get(base + "/events").json()["items"]
            cursor = initial[-1]["id"]
            assert client.get(base + "/events?after_id=-1").status_code == 422
            assert client.get(base + "/events/stream", headers={"Last-Event-ID": "bad"}).status_code == 422
            assert client.get(base + "/events/stream?after_id=999999").status_code == 409
            assert client.get("/api/runs/unknown/events").status_code == 404
            with client.stream("GET", base + "/events/stream") as response:
                assert response.status_code == 200 and "text/event-stream" in response.headers["content-type"]
                stream = frames(response)
                snapshot = next(stream)
                assert snapshot["event"] == "snapshot" and "id" not in snapshot
                event = next(stream)
                assert int(event["id"]) == cursor
                assert json.loads(event["data"])["run_id"] == run.id
                assert len(repository.list_events(run.id)) == 1 and producer.calls == 0
                assert client.post(base + "/execute", json={"expected_version": run.version}).status_code == 202
                assert started.wait(2)
            # Dropping observation does not cancel production or create a replacement Attempt.
            assert service.get(run.id).status.value == "producing"
            assert app.state.executor.is_submitted(run.id)
            with client.stream("GET", base + "/events/stream?after_id=0", headers={"Last-Event-ID": str(cursor)}) as response:
                stream = frames(response)
                assert next(stream)["event"] == "snapshot"
                resumed = next(stream)
                assert int(resumed["id"]) > cursor  # Header overrides initial query cursor.
                payload = json.loads(resumed["data"])
                assert payload["event_type"] == "started" and "payload" not in payload
                release.set()
                ids = [int(resumed["id"])]
                for event in stream:
                    if event.get("event") != "run_event":
                        continue
                    ids.append(int(event["id"]))
                    if json.loads(event["data"])["event_type"] == "validated":
                        break
                assert ids == sorted(set(ids))
            final = client.get(base).json()
            assert final["status"] == "awaiting_approval" and producer.calls == 1
            assert len(final["revisions"][0]["attempts"]) == 1
            batch = client.get(base + f"/events?after_id={cursor}&limit=1").json()
            assert len(batch["items"]) == 1
            rest = client.get(base + f"/events?after_id={batch['next_after_id']}").json()
            assert all(item["id"] > batch["next_after_id"] for item in rest["items"])
            assert service.get(other.id).status.value == "queued"
            assert client.get(base + f"/events?after_id={ids[-1]}").json()["items"] == []
            with client.stream("GET", base + f"/events/stream?after_id={ids[-1]}") as response:
                stream = frames(response)
                assert next(stream)["event"] == "snapshot"
                shutdown_started = monotonic()
                server.should_exit = True
                list(stream)
                assert monotonic() - shutdown_started < 3
    finally:
        release.set()
        server.should_exit = True
        thread.join(5)
        assert not thread.is_alive()
        sock.close()
        db.close()
print("studio_events_smoke=passed localhost_http=sse snapshot=replay cursor=ordered reconnect=no_duplicates disconnect=no_side_effects")
