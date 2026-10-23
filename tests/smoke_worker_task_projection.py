"""Seeded task projection over real isolated SQLite and loopback HTTP; no model calls."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio

from fastapi import FastAPI
import httpx
from sqlalchemy import select

from creatoros.storage import (
    ContentRepository, ContentRun, ContentRunStatus, CreatorPlatform, Database, Series,
    SkillIteration, Topic, TopicRemoval, TopicSource, upgrade_database,
)
from creatoros.web.worker_tasks import worker_task_routes
from creatoros.web.agent_scope import AgentScopeGuard
from starlette.requests import Request
from tests.agent_studio_support import serve


class Research:
    def __init__(self):
        self.list_calls = []

    def list(self, series_id):
        self.list_calls.append(series_id)
        return [{"id": f"batch-{series_id}", "created_at": "2026-10-08T01:00:00+00:00",
                 "status": "failed", "note": "research failed"}]

    def get(self, batch_id):
        return {"instructions": "recent topic scan", "progress": {"last_activity_at": None}}


class Discussions:
    def __init__(self, run_id):
        self.records = [{"id": "discussion-1", "creator_id": "creator-a", "series_id": "series-a",
                         "run_id": run_id, "message": "看起来如何？", "status": "completed",
                         "created_at": "2026-10-08T02:00:00+00:00"}]

    def list_for_creator(self, creator_id, series_id=None):
        return [item for item in self.records if item["creator_id"] == creator_id
                and (series_id is None or item["series_id"] == series_id)]


class Iterations:
    def __init__(self):
        self.read_ids = []

    def get(self, identity):
        self.read_ids.append(identity)
        return {"status": "completed", "stage": "completed"}


def snapshot(db):
    with db.session() as session:
        return {model.__tablename__: [
            {column.name: deepcopy(getattr(row, column.name)) for column in model.__table__.columns}
            for row in session.scalars(select(model).order_by(*model.__table__.primary_key.columns))]
            for model in (Series, Topic, ContentRun, SkillIteration, TopicRemoval)}


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

    repository.create_series(series_id="archived-series", creator_id="creator-a", name="归档栏目",
                             description="", audience="", skill_name="knowledge-to-carousel")
    for topic_id, series_id in (("removed-topic", "series-a"), ("archived-topic", "archived-series")):
        repository.add_topic(topic_id=topic_id, series_id=series_id,
                             title=f"保留的历史：{topic_id}", source=TopicSource.MANUAL)
    with db.session() as session:
        session.get(Series, "archived-series").is_active = False
        session.add(TopicRemoval(topic_id="removed-topic", series_id="series-a", creator_id="creator-a",
                                 request_id="removed-history", status="archived"))
        for topic_id in ("removed-topic", "archived-topic"):
            session.add(ContentRun(id=f"run-{topic_id}", topic_id=topic_id,
                idempotency_key=f"key-{topic_id}", input_snapshot_json={"creator_id": "creator-a"}))
        for identity, topic_id, series_id in (("a" * 64, "topic-a", "series-a"),
                ("b" * 64, "removed-topic", "series-a"), ("c" * 64, "archived-topic", "archived-series")):
            session.add(SkillIteration(id=identity, request_id=identity, input_digest="d" * 64,
                creator_id="creator-a", series_id=series_id, skill_id="fixture-skill", topic_id=topic_id,
                status="completed", config_json={"creator_id": "creator-a", "series_id": series_id,
                    "topic_id": topic_id, "topic_title": topic_id}, state_json={}))

    app = FastAPI()
    research, discussions = Research(), Discussions(run.id)
    for topic_id, series_id in (("removed-topic", "series-a"), ("archived-topic", "archived-series")):
        discussions.records.append({"id": f"discussion-{topic_id}", "creator_id": "creator-a",
            "series_id": series_id, "run_id": f"run-{topic_id}", "message": "历史讨论应保留但不默认列出",
            "status": "completed", "created_at": "2026-10-08T02:00:00+00:00"})
    iterations = Iterations()
    app.include_router(worker_task_routes(db, research, discussions, iterations=iterations))
    guard = AgentScopeGuard(db, Chat(), research, discussions)

    def scoped_request(method, path, query=b""):
        return Request({"type": "http", "method": method, "path": path,
                        "query_string": query,
                        "headers": [(b"x-creatoros-agent-session", b"account-a")]})

    before = snapshot(db)
    with serve(app) as base, httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
        result = client.get("/api/creators/creator-a/tasks")
        assert result.status_code == 200, result.text
        body = result.json()
        assert {item["kind"] for item in body["items"]} == {"production", "research", "discussion", "skill_iteration"}
        assert {item["id"] for item in body["items"]} == {"run-a", "batch-series-a", "discussion-1", "a" * 64}
        assert research.list_calls == ["series-a"] and iterations.read_ids == ["a" * 64]
        assert body["summary"] == {"active": 0, "awaiting_approval": 0, "failed": 1}
        production = next(item for item in body["items"] if item["kind"] == "production")
        assert production["id"] == run.id and production["title"] == "Topic A"
        assert body["as_of"]
        assert client.get("/api/creators/creator-a/tasks?series_id=archived-series").status_code == 404
        assert client.get("/api/creators/creator-b/tasks?series_id=archived-series").status_code == 404
        assert client.get("/api/creators/creator-a/tasks?series_id=unknown-series").status_code == 404
        filtered = client.get("/api/creators/creator-a/tasks", params={"statuses": "failed"}).json()
        assert [item["id"] for item in filtered["items"]] == ["batch-series-a"]
        assert filtered["summary"] == {"active": 0, "awaiting_approval": 0, "failed": 1}
        assert snapshot(db) == before  # Queries do not erase or rewrite retained history.
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

print("worker_task_projection_smoke=passed projection=active_nonremoved history=preserved transfer=scope_checked loopback=real")
