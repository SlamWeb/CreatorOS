"""Series deletion/archive contract using isolated SQLite and real local HTTP."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from unittest.mock import patch

import httpx
from creatoros.runs import ContentRunService
from creatoros.storage import (
    ContentRepository,
    ContentRunStatus,
    Creator,
    CreatorPlatform,
    Database,
    Series,
    TopicSource,
    upgrade_database,
)
from creatoros.web.app import create_app
from tests.agent_studio_support import serve


def _payload(revision: int = 1, request_id: str | None = None) -> dict:
    return {"expected_revision": revision, "request_id": request_id or uuid4().hex}


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    database_url = f"sqlite:///{(root / 'remove.db').as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    repository = ContentRepository(database)
    repository.create_creator(creator_id="creator-a", display_name="A", platform=CreatorPlatform.XIAOHONGSHU)
    repository.create_creator(creator_id="creator-b", display_name="B", platform=CreatorPlatform.XIAOHONGSHU)
    runs = ContentRunService(database, output_root=root / "outputs")
    app = create_app(database=database, run_service=runs, chat_root=root / "sessions")

    with serve(app) as base_url, httpx.Client(base_url=base_url, timeout=5, trust_env=False) as client:
        account_a = app.state.chat.create("creator-a")["id"]
        account_b = app.state.chat.create("creator-b")["id"]
        scoped_a = {"x-creatoros-agent-session": account_a}
        scoped_b = {"x-creatoros-agent-session": account_b}

        def make_series(name: str) -> str:
            result = client.post("/api/series", json={
                "name": name, "creator_id": "creator-a", "skill_name": "knowledge-to-carousel",
                "request_id": uuid4().hex,
            })
            assert result.status_code == 201, result.text
            return result.json()["series"]["id"]

        # Empty series is physically deleted, and its receipt permits a same
        # account retry while preventing another account from replaying it.
        empty_id = make_series("空栏目")
        rid = uuid4().hex
        deleted = client.request("DELETE", f"/api/series/{empty_id}", headers=scoped_a, json=_payload(request_id=rid))
        assert deleted.status_code == 200 and deleted.json() == {
            "id": empty_id, "status": "deleted", "deduplicated": False,
        }, deleted.text
        replay = client.request("DELETE", f"/api/series/{empty_id}", headers=scoped_a, json=_payload(request_id=rid))
        assert replay.status_code == 200 and replay.json()["deduplicated"] is True, replay.text
        denied_replay = client.request("DELETE", f"/api/series/{empty_id}", headers=scoped_b, json=_payload(request_id=rid))
        assert denied_replay.status_code == 403, denied_replay.text
        with database.session() as session:
            assert session.get(Series, empty_id) is None

        # Expected revision is a CAS token and a stale request leaves the row intact.
        stale_id = make_series("旧版本")
        stale = client.request("DELETE", f"/api/series/{stale_id}", json=_payload(revision=9))
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"
        with database.session() as session:
            assert session.get(Series, stale_id).is_active

        # A topic and its old Run require archival; direct Run history remains readable.
        historical_id = make_series("有Run历史")
        repository.add_topic(topic_id="topic-history", series_id=historical_id,
                             title="历史选题", source=TopicSource.MANUAL)
        history_run = runs.create("topic-history")
        archived = client.request("DELETE", f"/api/series/{historical_id}", json=_payload())
        assert archived.status_code == 200 and archived.json()["status"] == "archived", archived.text
        assert historical_id not in {item["id"] for item in client.get("/api/series").json()}
        assert client.get(f"/api/runs/{history_run.id}").status_code == 200
        assert client.get(f"/api/series/{historical_id}").json()["is_active"] is False
        with database.session() as session:
            archived_row = session.get(Series, historical_id)
            assert archived_row is not None and archived_row.revision == 2

        # Existing research files are history and are retained after archival.
        research_id = make_series("有调研历史")
        batch_id = uuid4().hex
        research_path = app.state.topic_research._path(batch_id)
        research_path.parent.mkdir(parents=True, exist_ok=True)
        research_path.write_text(json.dumps({"id": batch_id, "series_id": research_id,
                                             "status": "ready", "created_at": "2026-10-05T00:00:00Z"}),
                                 encoding="utf-8")
        research_archived = client.request("DELETE", f"/api/series/{research_id}", json=_payload())
        assert research_archived.status_code == 200 and research_archived.json()["status"] == "archived"
        assert research_path.is_file()

        # Running production and running research block deletion without cancellation.
        active_id = make_series("生产中")
        repository.add_topic(topic_id="topic-active", series_id=active_id,
                             title="活跃选题", source=TopicSource.MANUAL)
        active_run = runs.create("topic-active")
        with database.session() as session:
            session.get(type(active_run), active_run.id).status = ContentRunStatus.PRODUCING
        blocked_run = client.request("DELETE", f"/api/series/{active_id}", json=_payload())
        assert blocked_run.status_code == 409 and blocked_run.json()["error"]["code"] == "series_active"
        with database.session() as session:
            session.get(type(active_run), active_run.id).status = ContentRunStatus.QUEUED
            assert session.get(Series, active_id).is_active

        research_active_id = make_series("调研中")
        with patch.object(app.state.topic_research, "has_active_for_series", return_value=True):
            blocked_research = client.request("DELETE", f"/api/series/{research_active_id}", json=_payload())
        assert blocked_research.status_code == 409 and blocked_research.json()["error"]["code"] == "series_active"
        with database.session() as session:
            assert session.get(Series, research_active_id).is_active

        # Same account can remove its owned active empty series; another account
        # cannot target it by guessing the identifier.
        own_id = make_series("账号范围")
        assert client.request("DELETE", f"/api/series/{own_id}", headers=scoped_a, json=_payload()).status_code == 200
        other_id = make_series("跨账号范围")
        cross_account = client.request("DELETE", f"/api/series/{other_id}", headers=scoped_b, json=_payload())
        assert cross_account.status_code == 403

        visible_ids = {item["id"] for item in client.get("/api/series").json()}
        assert historical_id not in visible_ids and research_id not in visible_ids

    database.close()

print("series_remove_smoke=passed empty_delete=passed history_archive=passed active_guard=passed revision=passed scope=passed")
