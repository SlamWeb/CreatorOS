"""Isolated HTTP/worker fault tests, not a claim of real model quality."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

from fastapi.testclient import TestClient

from creatoros.integrations.content_discussion import ContentDiscussionService, DiscussionProgress
from creatoros.runs import ContentRunRepository
from creatoros.storage import ContentRun, ContentRunStatus
from tests.studio_review_fixtures import make_fixture


class Reviewer:
    def __init__(self):
        self.calls = []
        self.release = Event()
        self.release.set()
        self.fail = False

    def review(self, directory, prompt, images, thread_id, source, cancel, emit, bind):
        assert len(images) == 5 and all(p.is_file() for p in images)
        assert all(p.is_relative_to(directory) for p in images)
        assert '"revision_id"' in prompt and '"cards"' in prompt
        self.calls.append((thread_id, source))
        bind(thread_id or "test-discussion-thread", "continued_discussion" if thread_id else "forked_production")
        emit("delta", "图片已查看，")
        self.release.wait(3)
        if self.fail:
            raise RuntimeError("injected failure")
        return "图片已查看，这只是讨论，没有修改。", {"input_tokens": 1}


def wait(client, base, identifier):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        records = client.get(base + "/discussion").json()["items"]
        item = next(r for r in records if r["id"] == identifier)
        if item["status"] not in {"queued", "running"}:
            return item
        sleep(.02)
    raise AssertionError("worker did not finish")


with TemporaryDirectory() as directory:
    root = Path(directory)
    progress = DiscussionProgress(root, lambda *args: None)
    usage = progress.finish_usage(SimpleNamespace(last={"input_tokens": 42}, total={"input_tokens": 999999}))
    assert usage["last_request"]["input_tokens"] == 42
    assert usage["thread_cumulative"]["input_tokens"] == 999999
    assert usage["scope"] == "last_model_request"
    assert progress.finish_usage(None)["scope"] == "unavailable"
    for kind in ("commandExecution", "imageGeneration", "mcpToolCall", "unknownFutureTool"):
        try:
            progress.public(SimpleNamespace(method="item/started", payload={"item": {"type": kind}}))
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"unexpected tool was not rejected: {kind}")
    progress.public(SimpleNamespace(method="item/started", payload={"item": {"type": "reasoning"}}))
    db, runs, producer, app = make_fixture(root)
    run = runs.create("review-1")
    runs.execute(run.id)
    reviewer = Reviewer()
    discussions = app.state.content_discussions
    discussions.reviewer = reviewer
    repository = ContentRunRepository(db)
    with TestClient(app) as client:
        base = "/api/runs/" + run.id
        original = client.get(base).json()
        revision = original["revisions"][0]
        body = {"request_id": "discussion-test-1", "revision_id": revision["id"],
                "artifact_digest": revision["review_digest"], "message": "检查第二张，不修改"}
        stale = client.post(base + "/discussion", json={**body, "artifact_digest": "0" * 64})
        assert stale.status_code == 409, stale.text
        assert not reviewer.calls
        reviewer.release.clear()
        result = client.post(base + "/discussion", json=body)
        assert result.status_code == 202, result.text
        entry = result.json()
        duplicate = client.post(base + "/discussion", json=body)
        assert duplicate.status_code == 202 and duplicate.json()["id"] == entry["id"]
        conflict = client.post(base + "/discussion", json={**body, "message": "another"})
        assert conflict.status_code == 409
        busy = client.post(base + "/discussion", json={**body, "request_id": "discussion-test-2"})
        assert busy.status_code == 409
        reviewer.release.set()
        item = wait(client, base, entry["id"])
        assert item["status"] == "completed" and len(reviewer.calls) == 1
        assert item["context"]["image_count"] == 5
        assert str(root) not in json.dumps(item)
        second = client.post(base + "/discussion", json={**body, "request_id": "discussion-test-2", "message": "为什么？"})
        assert wait(client, base, second.json()["id"])["status"] == "completed"
        assert reviewer.calls[1][0] == "test-discussion-thread"
        reviewer.fail = True
        failed = client.post(base + "/discussion", json={**body, "request_id": "discussion-test-3"})
        assert wait(client, base, failed.json()["id"])["status"] == "failed"
        current = client.get(base).json()
        assert current["version"] == original["version"] and current["status"] == "awaiting_approval"
        assert current["revisions"][0]["review_digest"] == revision["review_digest"]
        assert len(current["revisions"]) == 1 and producer.calls == 1
        with db.session() as session:
            session.get(ContentRun, run.id).status = ContentRunStatus.PRODUCING
        blocked = client.post(base + "/discussion", json={**body, "request_id": "discussion-test-4"})
        assert blocked.status_code == 409
        with db.session() as session:
            session.get(ContentRun, run.id).status = ContentRunStatus.AWAITING_APPROVAL
        assert client.get(base + "/discussion").status_code == 200
    records = discussions.get(run.id)["items"]
    interrupted = {**records[-1], "id": "restart-test", "status": "running"}
    discussions._save(interrupted)
    restored = ContentDiscussionService(db, discussions.artifacts, discussions.root, reviewer)
    restored.start()
    assert any(r["id"] == "restart-test" and r["status"] == "interrupted" for r in restored.get(run.id)["items"])
    assert len(reviewer.calls) == 3
    db.close()

print("content_discussion_smoke=passed HTTP=idempotency=conflict=resume=restart original_run_unchanged=true")
