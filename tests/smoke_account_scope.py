from __future__ import annotations

import json
import asyncio
from threading import Event, Thread
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from creatoros.context import RuntimeContext
from creatoros.runs import ContentRunService
from creatoros.storage import (
    ContentRepository,
    ContentRun,
    Creator,
    CreatorPlatform,
    Database,
    Series,
    Topic,
    TopicSource,
    upgrade_database,
)
from creatoros.web.app import create_app
from creatoros.tools.studio import list_creator_series, list_creators
from creatoros.web.agent_scope import AgentScopeGuard
from tests.agent_studio_support import serve


def _counts(database: Database) -> tuple[int, int, int, int]:
    with database.session() as session:
        return tuple(int(session.scalar(select(func.count()).select_from(model)) or 0)
                     for model in (Creator, Series, Topic, ContentRun))


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    database_url = f"sqlite:///{(root / 'scope.db').as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    repository = ContentRepository(database)
    repository.create_creator(creator_id="creator-a", display_name="A", platform=CreatorPlatform.XIAOHONGSHU)
    repository.create_creator(creator_id="creator-b", display_name="B", platform=CreatorPlatform.XIAOHONGSHU)
    repository.create_series(series_id="series-a", creator_id="creator-a", name="A series",
                             description="A", audience="A", skill_name="knowledge-to-carousel")
    repository.create_series(series_id="series-b", creator_id="creator-b", name="B series",
                             description="B", audience="B", skill_name="knowledge-to-carousel")
    repository.add_topic(topic_id="topic-a", series_id="series-a", title="A topic", source=TopicSource.MANUAL)
    repository.add_topic(topic_id="topic-b", series_id="series-b", title="B topic", source=TopicSource.MANUAL)

    runs = ContentRunService(database, output_root=root / "outputs")
    run_a = runs.create("topic-a")
    run_b = runs.create("topic-b")
    app = create_app(database=database, run_service=runs, chat_root=root / "sessions")
    with serve(app) as studio_url, httpx.Client(base_url=studio_url, timeout=5, trust_env=False) as client:
        account_session = app.state.chat.create("creator-a")["id"]
        scoped = {"x-creatoros-agent-session": account_session}

        # Header-free Web/CLI calls retain the existing global projection.
        global_page = client.get("/api/creators")
        assert global_page.status_code == 200 and global_page.json()["page"]["total"] == 2

        creators = client.get("/api/creators?offset=0&limit=20", headers=scoped)
        assert creators.status_code == 200
        assert [item["id"] for item in creators.json()["items"]] == ["creator-a"]
        assert creators.json()["page"]["total"] == 1
        assert client.get("/api/creators/creator-a", headers=scoped).status_code == 200
        assert client.get("/api/creators/creator-b", headers=scoped).status_code == 403
        assert client.get("/api/series/series-a/topics", headers=scoped).status_code == 200
        assert client.get("/api/series/series-b/topic-library", headers=scoped).status_code == 403

        context = RuntimeContext(project_root=root, studio_url=studio_url,
                                 allowed_tools=frozenset({"list_creators", "list_creator_series"}),
                                 creator_id="creator-a", agent_session_id=account_session)
        tool_page = json.loads(list_creators(context=context).content)
        assert [item["id"] for item in tool_page["items"]] == ["creator-a"]
        forged_tool = list_creator_series("creator-b", context=context)
        assert forged_tool.is_error

        # Forged IDs and global mutations are rejected before service calls.
        before = _counts(database)
        composed = client.post("/api/series", headers=scoped, json={
            "name": "forged", "creator_id": "creator-b", "request_id": uuid4().hex,
            "skill_name": "knowledge-to-carousel",
        })
        queued = client.post("/api/series/series-b/queue", headers=scoped, json={
            "request_id": uuid4().hex, "topics": [{"title": "forged", "source": "manual"}],
        })
        research_start = client.post("/api/series/series-b/topic-research", headers=scoped,
                                     json={"count": 1, "instructions": ""})
        install = client.post("/api/producer-skills/install", headers=scoped,
                              json={"github_url": "https://github.com/example/skill"})
        assert [composed.status_code, queued.status_code, research_start.status_code, install.status_code] == [403] * 4
        assert _counts(database) == before

        # A research batch is scoped by its persisted series ID for reads and writes.
        batch_id = "a" * 32
        batch_path = app.state.topic_research._path(batch_id)
        batch_path.parent.mkdir(parents=True, exist_ok=True)
        batch_path.write_text(json.dumps({"id": batch_id, "series_id": "series-b", "status": "ready",
                                          "snapshot": {}, "candidates": []}), encoding="utf-8")
        assert client.get(f"/api/topic-research/{batch_id}", headers=scoped).status_code == 403
        batch_preview = client.post(f"/api/topic-research/{batch_id}/preview", headers=scoped,
                                    json={"selections": [{"candidate_id": "c1"}]})
        assert batch_preview.status_code == 403

        # Topic ownership controls Run creation; Run reads and execution also compare
        # the immutable snapshot with the current Topic -> Series relationship.
        before = _counts(database)
        forged_run = client.post("/api/runs", headers=scoped, json={"topic_id": "topic-b"})
        assert forged_run.status_code == 403 and _counts(database) == before
        assert client.get(f"/api/runs/{run_a.id}", headers=scoped).status_code == 200
        assert client.get(f"/api/runs/{run_b.id}", headers=scoped).status_code == 403
        denied_execute = client.post(f"/api/runs/{run_b.id}/execute", headers=scoped,
                                     json={"expected_version": 1})
        assert denied_execute.status_code == 403
        with database.session() as session:
            assert session.get(ContentRun, run_b.id).status.value == "queued"

        # A moved Series with old Runs cannot expose their frozen foreign snapshots,
        # create a duplicate Run, or project topic rows containing those Run IDs.
        with database.session() as session:
            session.get(Series, "series-b").creator_id = "creator-a"
        moved_topics = client.get("/api/series/series-b/topics", headers=scoped)
        moved_start = client.post("/api/runs", headers=scoped, json={"topic_id": "topic-b"})
        assert moved_topics.status_code == 403 and moved_start.status_code == 403
        assert client.get(f"/api/runs/{run_b.id}", headers=scoped).status_code == 403

        # Research catalog is intentionally shared/read-only. Unknown session IDs and
        # otherwise unlisted account endpoints fail closed.
        assert client.get("/api/producer-skills", headers=scoped).status_code == 200
        unknown = client.get("/api/series", headers=scoped)
        assert unknown.status_code == 403
        missing_session = client.get("/api/creators", headers={
            "x-creatoros-agent-session": str(uuid4())
        })
        assert missing_session.status_code == 403

        # Deactivation immediately closes a previously persisted account session.
        overview_session = app.state.chat.create()["id"]
        assert client.get("/api/series", headers={
            "x-creatoros-agent-session": overview_session
        }).status_code == 200

        # Pause after a successful scope check. An HTTP reassignment must wait
        # until the scoped write finishes; it must not race into the gap.
        checked, release, reassigned = Event(), Event(), Event()
        responses = {}
        original_check = AgentScopeGuard.check
        async def delayed_check(guard, request):
            result = await original_check(guard, request)
            if request.url.path == "/api/series/series-a/queue" and request.method == "POST":
                assert result is None
                checked.set()
                assert await asyncio.to_thread(release.wait, 5)
            return result
        def enqueue():
            responses["queue"] = client.post("/api/series/series-a/queue", headers=scoped,
                json={"request_id": uuid4().hex, "topics": [{"title": "Before transfer", "source": "manual"}]})
        def transfer():
            responses["transfer"] = client.post("/api/series/series-a/assignment",
                json={"request_id": uuid4().hex, "creator_id": "creator-b", "expected_revision": 1})
            reassigned.set()
        with patch.object(AgentScopeGuard, "check", delayed_check):
            writer = Thread(target=enqueue)
            mover = Thread(target=transfer)
            writer.start()
            try:
                assert checked.wait(3)
                mover.start()
                assert not reassigned.wait(.2)
            finally:
                release.set()
                writer.join(5)
                if mover.ident is not None:
                    mover.join(5)
            assert responses["queue"].status_code == 201, responses["queue"].text
            assert responses["transfer"].status_code == 200, responses["transfer"].text
        assert client.get("/api/series/series-a/topics", headers=scoped).status_code == 403

        with database.session() as session:
            creator = session.get(Creator, "creator-a")
            creator.is_active = False
        inactive = client.get("/api/creators", headers=scoped)
        assert inactive.status_code == 403

    database.close()

print("account_scope_smoke=passed list=filtered cross_account=denied batch=owned run=double_checked transfer_race=serialized")
