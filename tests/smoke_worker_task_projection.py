from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creatoros.storage import (
    ContentRepository, ContentRun, ContentRunStatus, CreatorPlatform, Database, Series, TopicSource,
    upgrade_database,
)
from creatoros.web.worker_tasks import worker_task_routes
from creatoros.web.agent_scope import AgentScopeGuard
from starlette.requests import Request


class Research:
    def list(self, series_id):
        return [{"id": "batch-1", "created_at": "2026-10-08T01:00:00+00:00",
                 "status": "failed", "note": "research failed"}]

    def get(self, batch_id):
        return {"instructions": "recent topic scan", "progress": {"last_activity_at": None}}


class Discussions:
    def __init__(self, run_id):
        self.run_id = run_id

    def list_for_creator(self, creator_id, series_id=None):
        if creator_id != "creator-a" or (series_id and series_id != "series-a"):
            return []
        return [{"id": "discussion-1", "creator_id": creator_id, "series_id": "series-a",
                 "run_id": self.run_id, "message": "看起来如何？", "status": "completed",
                 "created_at": "2026-10-08T02:00:00+00:00"}]


class Chat:
    def get(self, session_id):
        if session_id != "account-a":
            raise ValueError("unknown session")
        return {"scope_kind": "creator", "creator_id": "creator-a"}


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    url = f"sqlite:///{(root / 'tasks.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)
    repository = ContentRepository(db)
    repository.create_creator(creator_id="creator-a", display_name="A", platform=CreatorPlatform.XIAOHONGSHU)
    repository.create_creator(creator_id="creator-b", display_name="B", platform=CreatorPlatform.XIAOHONGSHU)
    repository.create_series(series_id="series-a", creator_id="creator-a", name="Series A",
                             description="desc", audience="audience", skill_name="knowledge-to-carousel")
    repository.add_topic(topic_id="topic-a", series_id="series-a", title="Topic A", source=TopicSource.MANUAL)
    run = ContentRun(id="run-a", topic_id="topic-a", idempotency_key="key-a",
                     input_snapshot_json={"creator_id": "creator-a"})
    with db.session() as session:
        session.add(run)
        session.flush()

    app = FastAPI()
    research, discussions = Research(), Discussions(run.id)
    app.include_router(worker_task_routes(db, research, discussions))
    guard = AgentScopeGuard(db, Chat(), research, discussions)

    def scoped_request(method, path, query=b""):
        return Request({"type": "http", "method": method, "path": path,
                        "query_string": query,
                        "headers": [(b"x-creatoros-agent-session", b"account-a")]})

    with TestClient(app) as client:
        result = client.get("/api/creators/creator-a/tasks")
        assert result.status_code == 200, result.text
        body = result.json()
        assert {item["kind"] for item in body["items"]} == {"production", "research", "discussion"}
        assert body["summary"] == {"active": 0, "awaiting_approval": 0, "failed": 1}
        production = next(item for item in body["items"] if item["kind"] == "production")
        assert production["id"] == run.id and production["title"] == "Topic A"
        assert body["as_of"]
        with db.session() as session:
            session.get(ContentRun, run.id).status = ContentRunStatus.PRODUCING
        assert client.get("/api/creators/creator-a/tasks").json()["summary"]["active"] == 1
        assert client.get("/api/creators/creator-b/tasks?series_id=series-a").status_code == 404
        assert asyncio.run(guard.check(scoped_request("GET", "/api/creators/creator-a/tasks"))) is None
        assert asyncio.run(guard.check(scoped_request("GET", "/api/runs/run-a/discussion"))) is None
        assert asyncio.run(guard.check(scoped_request("POST", "/api/runs/run-a/discussion"))) is None
        assert asyncio.run(guard.check(scoped_request("POST", "/api/runs/run-a/revisions"))) is None
        assert asyncio.run(guard.check(scoped_request("GET", "/api/runs/run-b/discussion"))).status_code == 403

        with db.session() as session:
            session.get(Series, "series-a").creator_id = "creator-b"
        moved = client.get("/api/creators/creator-b/tasks?series_id=series-a")
        assert moved.status_code == 403
        former_owner = client.get("/api/creators/creator-a/tasks")
        assert former_owner.status_code == 200 and former_owner.json()["items"] == []

    db.close()

print("worker_task_projection_smoke=passed projection=existing_records transfer=scope_checked")
