"""Bulk, read-only cover projection for the unified topic library."""
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from sqlalchemy import event

from creatoros.runs import ContentRunRepository
from creatoros.storage import ContentRevision
from tests.smoke_topic_research import seed_batch
from tests.studio_review_fixtures import make_fixture


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    database, runs, producer, app = make_fixture(root)
    topic_library = "/api/series/agent-notes/topic-library"
    query_statements = []

    def observe_sql(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.lower()
        if "join content_revisions" in normalized and "content_attempts" in normalized:
            query_statements.append(statement)

    event.listen(database.engine, "before_cursor_execute", observe_sql)
    seed_batch(app.state.topic_research, "agent-notes")
    run = runs.create("review-1")

    with TestClient(app) as client:
        initial = client.get(topic_library).json()
        pending = next(item for item in initial["items"] if item["selection_state"] == "pending")
        unproduced = next(item for item in initial["items"] if item["id"] == "review-1")
        assert pending["cover_url"] is None and pending["card_count"] is None
        assert unproduced["existing_run_status"] == "queued"
        assert unproduced["cover_url"] is None and unproduced["card_count"] is None

        runs.execute(run.id)
        before = runs.get(run.id)
        event_count = len(ContentRunRepository(database).list_events(run.id))
        calls = producer.calls
        query_statements.clear()
        produced = client.get(topic_library).json()
        card = next(item for item in produced["items"] if item["id"] == "review-1")
        detail = client.get(f"/api/runs/{run.id}").json()
        active = next(item for item in detail["revisions"] if item["revision_number"] == run.active_revision_number)
        expected_cover = active["cards"][0]["url"]
        assert card["cover_url"] == expected_cover
        assert card["card_count"] == len(active["cards"]) == 5
        assert len(query_statements) == 1, "cover metadata must be loaded in one bulk query per library page"
        assert client.get(card["cover_url"]).status_code == 200
        after = runs.get(run.id)
        assert after.version == before.version
        assert len(ContentRunRepository(database).list_events(run.id)) == event_count
        assert producer.calls == calls

        # An active Revision without final validation metadata cannot borrow a
        # preview image or its previous projection.
        active_revision_id = active["id"]
        with database.session() as session:
            stored_revision = session.get(ContentRevision, active_revision_id)
            saved_validation = stored_revision.validation_json
            stored_revision.validation_json = None
        unvalidated = next(item for item in client.get(topic_library).json()["items"] if item["id"] == "review-1")
        assert unvalidated["cover_url"] is None and unvalidated["card_count"] is None
        with database.session() as session:
            session.get(ContentRevision, active_revision_id).validation_json = saved_validation

        # A prior revision's image must not leak after the active revision changes.
        revised = client.post(
            f"/api/runs/{run.id}/revisions",
            json={"instruction": "隔离测试返工", "expected_version": after.version},
        )
        assert revised.status_code == 201, revised.text
        queued = next(item for item in client.get(topic_library).json()["items"] if item["id"] == "review-1")
        assert queued["existing_run_status"] == "queued"
        assert queued["cover_url"] is None and queued["card_count"] is None

        runs.execute(run.id)
        current = client.get(f"/api/runs/{run.id}").json()
        active_revision = current["revisions"][-1]
        directory = Path(ContentRunRepository(database).get_revision(active_revision["id"]).artifact_directory)
        (directory / "images/01.png").unlink()
        missing = next(item for item in client.get(topic_library).json()["items"] if item["id"] == "review-1")
        assert missing["cover_url"] is None and missing["card_count"] is None

    event.remove(database.engine, "before_cursor_execute", observe_sql)
    database.close()

print("topic_library_cover_smoke=passed bulk=1 pending=unproduced=unvalidated=null active_revision=only missing=unlinked read_only=passed")
