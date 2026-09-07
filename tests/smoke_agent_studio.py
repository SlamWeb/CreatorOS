"""Real HTTP/SQLite; controlled images and network faults are explicitly test fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch

import httpx

from creatoros.ai.types import ToolCall
from creatoros.integrations.studio import StudioClient, StudioClientError
from creatoros.tools import execute_tool_call, tools
from creatoros.storage import ContentRun, ContentRunStatus
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


def tool(name, **args):
    return execute_tool_call(ToolCall("test", name, json.dumps(args)), model_requested=True)


def expect(code, action):
    try:
        action()
    except StudioClientError as error:
        assert error.code == code, error.code
        return error
    raise AssertionError(f"expected {code}")


def main():
    exposed = {item["function"]["name"] for item in tools}
    assert "start_content_run" in exposed and "produce_content_pack" not in exposed
    assert tool("produce_content_pack").error_type == "tool_not_exposed"
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        db, service, producer, app = make_fixture(root)
        release, started = Event(), Event()
        original = producer.produce_to

        def delayed(**kwargs):
            started.set()
            assert release.wait(15)
            return original(**kwargs)

        producer.produce_to = delayed
        try:
            with serve(app) as base, patch.dict(os.environ, {"CREATOROS_STUDIO_URL": base}):
                client = StudioClient(base)
                try:
                    assert client.creators(0, 1)["page"]["total"] == 1
                    assert client.creator_series("review-lab")["items"][0]["id"] == "agent-notes"
                    assert client.topics("agent-notes", 1, 1)["items"][0]["id"] == "review-2"
                    assert not tool("list_creators").is_error
                    assert tool("list_series_topics", series_id="missing").error_type == "not_found"
                    assert tool("list_creators", limit=101).error_type == "invalid_arguments"
                    submitted = json.loads(tool("start_content_run", topic_id="review-1").content)
                    run_id = submitted["run_id"]
                    assert submitted["accepted"] and submitted["status"] == "producing", submitted
                    assert started.wait(2)
                    # Another actual CLI process can exit Agent mode while Studio owns this database.
                    script = (
                        "from pathlib import Path; from creatoros.session import snapshot; "
                        f"snapshot.SESSION_FILE=Path({str(root / 'agent-session.json')!r}); "
                        "from creatoros.cli import main; main()"
                    )
                    env = dict(os.environ, DATABASE_URL=f"sqlite:///{(root / 'review.db').as_posix()}")
                    env.setdefault("DEEPSEEK_API_KEY", "unused-no-model-request")
                    child = subprocess.run([sys.executable, "-c", script, "--agent", "--studio-url", base],
                                           input="/menu\n", text=True, capture_output=True, env=env, timeout=10)
                    assert child.returncode == 0, child.stderr
                    assert (root / "agent-session.json").exists()
                    assert client.get_run(run_id)["status"] == "producing"
                    assert client.start("review-1")["accepted"] is False
                    busy = expect("producer_busy", lambda: client.start("review-2"))
                    assert busy.run_id == run_id
                    release.set()
                    deadline = monotonic() + 5
                    while client.get_run(run_id)["status"] in {"producing", "validating"} and monotonic() < deadline:
                        sleep(0.03)
                    detail = client.get_run(run_id)
                    assert detail["status"] == "awaiting_approval", detail["status"]
                    assert not client.start("review-1")["accepted"] and producer.calls == 1
                    assert len(detail["revisions"][0]["attempts"]) == 1
                    summary = json.loads(tool("get_content_run", run_id=run_id).content)
                    assert summary["status"] == detail["status"] and "revisions" not in summary
                    assert summary["url"] == base + f"/runs/{run_id}"
                    # A new human revision is not an implicit production command.
                    service.request_revision(run_id, "测试返工", expected_version=detail["version"])
                    assert not client.start("review-1")["accepted"] and producer.calls == 1
                    fresh = client.request("POST", "/api/runs", payload={"topic_id": "review-2"})
                    expect("version_conflict", lambda: client.request("POST", f"/api/runs/{fresh['id']}/execute",
                                                              payload={"expected_version": 999}))
                    for status in (ContentRunStatus.INTERRUPTED, ContentRunStatus.FAILED, ContentRunStatus.CANCELLED):
                        # Inject terminal/pause states, verifying start does not turn into resume.
                        with db.session() as session:
                            session.get(ContentRun, fresh["id"]).status = status
                        observed = client.start("review-2")
                        assert not observed["accepted"] and observed["status"] == status.value
                    assert producer.calls == 1
                finally:
                    release.set()
                    client.close()
        finally:
            db.close()

        # Only failure injection uses MockTransport. A lost execute response must retain the Run ID.
        requests = []
        def timeout(request):
            requests.append(request)
            if request.url.path == "/api/runs":
                return httpx.Response(200, json=fresh)
            raise httpx.ReadTimeout("injected", request=request)
        fault = StudioClient("http://127.0.0.1:8765", client=httpx.Client(transport=httpx.MockTransport(timeout)))
        try:
            error = expect("studio_outcome_unknown", lambda: fault.start("review-2"))
            assert error.run_id == fresh["id"] and len(requests) == 2
        finally:
            fault.close()
        def offline(request):
            raise httpx.ConnectError("injected", request=request)
        fault = StudioClient("http://127.0.0.1:8765", client=httpx.Client(transport=httpx.MockTransport(offline)))
        try:
            expect("studio_unavailable", lambda: fault.start("review-2"))
        finally:
            fault.close()
    print("agent_studio_smoke=passed directory=passed same_run=passed duplicate=passed busy=passed cli_lock=passed outcome_unknown=passed")


if __name__ == "__main__":
    main()
