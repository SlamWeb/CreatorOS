"""Topic removal regression over isolated SQLite and real loopback HTTP."""
from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from unittest.mock import patch

import httpx
from sqlalchemy import select

from creatoros.runs import ContentRunRepository, ContentRunService
from creatoros.storage import (
    ContentRepository,
    ContentRun,
    ContentRunStatus,
    CreatorPlatform,
    Database,
    Series,
    Topic,
    TopicRemoval,
    TopicSource,
    upgrade_database,
)
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests.smoke_topic_research import seed_batch
from tests.studio_review_fixtures import make_fixture
from creatoros.web.observation import encode


def _remove(client, topic_id: str, request_id: str | None = None, **extra):
    return client.post(
        f"/api/topics/{topic_id}/remove",
        json={"request_id": request_id or uuid4().hex, **extra},
    )


def _response_snapshot(detail: dict) -> tuple:
    return (
        detail["id"], detail["status"], detail["version"],
        detail["active_revision_number"], detail.get("artifact_digest"),
        tuple((revision["id"], revision["revision_number"], revision.get("artifact_digest"),
               revision.get("validation"), tuple((card["order"], card.get("checksum"), card.get("url"))
                                                  for card in revision.get("cards", [])))
              for revision in detail.get("revisions", [])),
    )


def _event_snapshot(events) -> tuple:
    return tuple((event.id, event.content_run_id, event.revision_id, event.attempt_id,
                  event.event_type, event.from_status, event.to_status, event.created_at)
                 for event in events)


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        database, runs, producer, app = make_fixture(root)
        repo = ContentRepository(database)
        series_id = "agent-notes"
        library_url = f"/api/series/{series_id}/topic-library"

        with serve(app) as base_url, httpx.Client(
            base_url=base_url, timeout=20, trust_env=False
        ) as client:
            # The test-only second owner is real persisted scope, but the target
            # series and every fixture belong to review-lab.
            repo.create_creator(creator_id="other-account", display_name="Other",
                                platform=CreatorPlatform.XIAOHONGSHU)
            session_a = app.state.chat.create("review-lab")["id"]
            session_b = app.state.chat.create("other-account")["id"]
            scoped_a = {"x-creatoros-agent-session": session_a}
            scoped_b = {"x-creatoros-agent-session": session_b}

            # A no-run Topic is physically deleted, and a retry replays the
            # original receipt. Reusing the request ID for another target conflicts.
            deleted_id = "review-2"
            request_id = uuid4().hex
            deleted = _remove(client, deleted_id, request_id)
            assert deleted.status_code == 200, deleted.text
            assert deleted.json() == {"id": deleted_id, "status": "deleted", "deduplicated": False}
            replay = _remove(client, deleted_id, request_id)
            assert replay.status_code == 200 and replay.json()["deduplicated"] is True, replay.text
            assert _remove(client, deleted_id).json()["deduplicated"] is True
            assert _remove(client, "review-1", request_id).status_code == 409
            with database.session() as session:
                assert session.get(Topic, deleted_id) is None

            # Existing Run history is archived from workspaces without changing
            # the Topic, Run, events, revision metadata, or checked image bytes.
            history_run = runs.create("review-1")
            runs.execute(history_run.id)
            before_detail = client.get(f"/api/runs/{history_run.id}").json()
            assert before_detail["status"] == ContentRunStatus.AWAITING_APPROVAL.value
            before_version = before_detail["version"]
            with database.session() as session:
                before_topic_status = session.get(Topic, "review-1").status
            before_events = _event_snapshot(ContentRunRepository(database).list_events(history_run.id))
            image_urls = [card["url"] for revision in before_detail["revisions"]
                          for card in revision.get("cards", [])]
            assert image_urls
            before_images = {url: hashlib.sha256(client.get(url).content).hexdigest()
                             for url in image_urls}
            archived_request_id = uuid4().hex
            archived = _remove(client, "review-1", archived_request_id)
            assert archived.status_code == 200, archived.text
            assert archived.json()["status"] == "archived"
            strict_old_delete = client.post("/api/topics/review-1/delete")
            assert strict_old_delete.status_code >= 400, strict_old_delete.text
            after_detail = client.get(f"/api/runs/{history_run.id}").json()
            assert _response_snapshot(after_detail) == _response_snapshot(before_detail)
            assert _event_snapshot(ContentRunRepository(database).list_events(history_run.id)) == before_events
            assert {url: hashlib.sha256(client.get(url).content).hexdigest()
                    for url in image_urls} == before_images
            with database.session() as session:
                topic = session.get(Topic, "review-1")
                run = session.get(ContentRun, history_run.id)
                assert topic is not None, f"archived Topic row disappeared: {topic!r}"
                assert topic.status == before_topic_status, (
                    f"Topic status changed during removal: topic={topic!r}, "
                    f"topic.status={topic.status!r}, before_topic_status={before_topic_status!r}"
                )
                assert run.status is ContentRunStatus.AWAITING_APPROVAL
                assert run.version == before_version

            # Removed Topic leaves the default workspaces and counters, while
            # historical Run detail and artifact URLs remain directly readable.
            assert "review-1" not in {item["id"] for item in client.get(f"/api/series/{series_id}/topics").json()["items"]}
            assert "review-1" not in {item["id"] for item in client.get(library_url).json()["items"]}
            assert next(item for item in client.get("/api/series").json() if item["id"] == series_id)["topic_count"] == 0
            history_after_remove = client.get(f"/api/runs/{history_run.id}").json()
            assert history_after_remove["allowed_actions"] == ["view"]
            listed_history = next(item for item in client.get("/api/runs").json()["items"]
                                  if item["id"] == history_run.id)
            assert listed_history["allowed_actions"] == ["view"]
            overview = client.get("/api/overview").json()
            assert history_run.id not in {item["id"] for item in overview["awaiting_approval"]}
            assert history_run.id not in {item["id"] for item in overview["needs_attention"]}

            # Creator task summaries drop removed production work, both across
            # the whole account and for its specific Series, without hiding a
            # saved read-only discussion record for the historical Run.
            discussion_record = {
                "id": "historic-discussion",
                "request_id": "historic-discussion-request",
                "run_id": history_run.id,
                "creator_id": "review-lab",
                "series_id": series_id,
                "revision_id": after_detail["revisions"][-1]["id"],
                "artifact_digest": after_detail["revisions"][-1]["artifact_digest"],
                "message": "历史作品讨论记录",
                "status": "completed",
                "created_at": "2026-10-09T00:00:00+00:00",
            }
            app.state.content_discussions._save(discussion_record)
            account_tasks = client.get("/api/creators/review-lab/tasks").json()
            series_tasks = client.get("/api/creators/review-lab/tasks",
                                      params={"series_id": series_id}).json()
            for task_summary in (account_tasks, series_tasks):
                assert history_run.id not in {item["id"] for item in task_summary["items"]
                                              if item["kind"] == "production"}
                assert task_summary["summary"]["awaiting_approval"] == 0
                assert any(item["id"] == "historic-discussion" and item["kind"] == "discussion"
                           for item in task_summary["items"])
            assert client.get("/api/creators/review-lab/tasks", headers=scoped_b).status_code == 403
            assert client.get(image_urls[0]).status_code == 200
            observation_tree = client.get("/api/observation/tree",
                                          params={"parent": encode("topic", "review-1")})
            assert observation_tree.status_code == 200, observation_tree.text
            assert observation_tree.json()["items"][0]["id"] == encode("run", history_run.id)
            observation_detail = client.get("/api/observation/detail",
                                            params={"node_id": encode("run", history_run.id)})
            assert observation_detail.status_code == 200, observation_detail.text

            repo.add_topic(topic_id="attention-removed", series_id=series_id,
                           title="移除的失败任务", source=TopicSource.MANUAL)
            attention_run = runs.create("attention-removed")
            with database.session() as session:
                session.get(ContentRun, attention_run.id).status = ContentRunStatus.FAILED
            assert _remove(client, "attention-removed").json()["status"] == "archived"
            attention_overview = client.get("/api/overview").json()
            assert attention_run.id not in {item["id"] for item in attention_overview["awaiting_approval"]}
            assert attention_run.id not in {item["id"] for item in attention_overview["needs_attention"]}
            attention_detail = client.get(f"/api/runs/{attention_run.id}").json()
            assert attention_detail["status"] == ContentRunStatus.FAILED.value
            assert attention_detail["allowed_actions"] == ["view"]

            # A queued Run can be archived, but its old execute route and a
            # new revision cannot restart production for a removed Topic.
            repo.add_topic(topic_id="review-3", series_id=series_id, title="队列任务",
                           source=TopicSource.MANUAL)
            queued = runs.create("review-3")
            assert _remove(client, "review-3").json()["status"] == "archived"
            before_calls = producer.calls
            denied_execute = client.post(f"/api/runs/{queued.id}/execute",
                                         json={"expected_version": queued.version})
            assert denied_execute.status_code == 409, denied_execute.text
            denied_revision = client.post(f"/api/runs/{history_run.id}/revisions", json={
                "instruction": "移除后返工",
                "expected_version": after_detail["version"],
            })
            assert denied_revision.status_code == 409, denied_revision.text
            assert producer.calls == before_calls
            # Active production/validation is rejected without cancellation or
            # any producer invocation. Both states are seeded locally in SQLite.
            for topic_id, status in (("active-producing", ContentRunStatus.PRODUCING),
                                     ("active-validating", ContentRunStatus.VALIDATING)):
                repo.add_topic(topic_id=topic_id, series_id=series_id, title=topic_id,
                               source=TopicSource.MANUAL)
                active_run = runs.create(topic_id)
                with database.session() as session:
                    session.get(ContentRun, active_run.id).status = status
                calls = producer.calls
                response = _remove(client, topic_id)
                assert response.status_code == 409, response.text
                with database.session() as session:
                    assert session.get(Topic, topic_id) is not None
                    assert session.get(ContentRun, active_run.id).status is status
                    # Restore the synthetic active state so app restart does
                    # not mistake a fault-injection row for a crashed worker.
                    session.get(ContentRun, active_run.id).status = ContentRunStatus.QUEUED
                assert producer.calls == calls

            # Removal also refuses while a discussion task for the selected
            # Topic is active. The record is a local fixture; no reviewer runs.
            repo.add_topic(topic_id="discussion-guard", series_id=series_id,
                           title="讨论进行中", source=TopicSource.MANUAL)
            discussion_run = runs.create("discussion-guard")
            calls = producer.calls
            with patch.object(app.state.content_discussions, "_records", return_value=[{
                "run_id": discussion_run.id, "status": "running",
            }]):
                blocked_discussion = _remove(client, "discussion-guard")
            assert blocked_discussion.status_code == 409, blocked_discussion.text
            with database.session() as session:
                assert session.get(Topic, "discussion-guard") is not None
                assert session.get(ContentRun, discussion_run.id).status is ContentRunStatus.QUEUED
            assert producer.calls == calls

            # A removal receipt itself is series history. Deleting the now
            # empty visible queue archives the Series so its receipt remains
            # addressable and same-ID retry cannot re-create the Topic.
            removal_history_series = "topic-remove-series-history"
            repo.create_series(series_id=removal_history_series, creator_id="review-lab",
                               name="移除回执历史", description="isolated", audience="isolated",
                               skill_name="knowledge-to-carousel")
            revived_id = "topic-revive-after-removal"
            old_preview_response = client.post("/api/operations/preview", json={
                "request_text": "稍后加入同一选题 ID",
                "series_id": removal_history_series,
                "plan": {"schema_version": 1, "operations": [{
                    "action": "add_topics", "series_id": removal_history_series,
                    "topics": [{"topic_id": revived_id, "title": "待加入选题", "source": "manual"}],
                }]},
            })
            assert old_preview_response.status_code == 201, old_preview_response.text
            old_preview = old_preview_response.json()
            repo.add_topic(topic_id=revived_id, series_id=removal_history_series,
                           title="已加入后移除", source=TopicSource.MANUAL)
            removal_request_id = uuid4().hex
            assert _remove(client, revived_id, removal_request_id).json()["status"] == "deleted"
            duplicate_removal = _remove(client, revived_id, removal_request_id)
            assert duplicate_removal.status_code == 200 and duplicate_removal.json()["deduplicated"]
            assert _remove(client, revived_id).json()["deduplicated"]
            old_preview_confirm = client.post(f"/api/operations/{old_preview['id']}/confirm", json={
                "expected_version": old_preview["version"],
                "expected_revision": old_preview["revision"],
                "confirmation_token": old_preview["confirmation_token"],
            })
            assert old_preview_confirm.status_code == 409, old_preview_confirm.text
            try:
                repo.add_topic(topic_id=revived_id, series_id=removal_history_series,
                               title="不能复活", source=TopicSource.MANUAL)
            except ValueError:
                pass
            else:
                raise AssertionError("a removed topic ID was allowed to re-enter its queue")
            series_archive = client.request("DELETE", f"/api/series/{removal_history_series}", json={
                "expected_revision": 1, "request_id": uuid4().hex,
            })
            assert series_archive.status_code == 200 and series_archive.json()["status"] == "archived", series_archive.text
            with database.session() as session:
                series = session.get(Series, removal_history_series)
                receipt = session.get(TopicRemoval, revived_id)
                assert series is not None and not series.is_active
                assert receipt is not None and receipt.status == "deleted"
                assert session.get(Topic, revived_id) is None
            after_archive_retry = _remove(client, revived_id, removal_request_id)
            assert after_archive_retry.status_code == 200 and after_archive_retry.json()["deduplicated"]

            # Reorder accepts exactly the visible IDs. An archived Topic keeps
            # its position at the tail, so visible renumbering cannot collide.
            reorder_series_id = "topic-remove-reorder"
            repo.create_series(series_id=reorder_series_id, creator_id="review-lab",
                               name="隐藏项调序", description="isolated", audience="isolated",
                               skill_name="knowledge-to-carousel")
            visible_ids = ["visible-one", "visible-two", "visible-three"]
            hidden_id = "archived-tail"
            for topic_id in [*visible_ids, hidden_id]:
                repo.add_topic(topic_id=topic_id, series_id=reorder_series_id,
                               title=topic_id, source=TopicSource.MANUAL)
            hidden_run = runs.create(hidden_id)
            hidden_removal = _remove(client, hidden_id)
            assert hidden_removal.status_code == 200 and hidden_removal.json()["status"] == "archived"
            before_hidden_position = next(item.position for item in repo.list_topics(reorder_series_id)
                                          if item.id == hidden_id)
            assert before_hidden_position == 4
            reordered_ids = [visible_ids[2], visible_ids[0], visible_ids[1]]
            reorder_response = client.post(f"/api/series/{reorder_series_id}/reorder", json={
                "ordered_topic_ids": reordered_ids,
            })
            assert reorder_response.status_code == 200, reorder_response.text
            visible_topics = client.get(f"/api/series/{reorder_series_id}/topics").json()["items"]
            assert [item["id"] for item in visible_topics] == reordered_ids
            assert [item["position"] for item in visible_topics] == [1, 2, 3]
            with database.session() as session:
                all_topics = list(session.scalars(select(Topic).where(Topic.series_id == reorder_series_id)))
                by_id = {item.id: item for item in all_topics}
                assert by_id[hidden_id].position == before_hidden_position
                assert [by_id[item].position for item in reordered_ids] == [1, 2, 3]
                assert len({item.position for item in all_topics}) == len(all_topics)

            # The original owner can replay only the original request after
            # transfer. New requests use current ownership; B cannot replay A's
            # receipt even though it now owns the Series.
            scope_series_id = "topic-remove-scope-transfer"
            repo.create_series(series_id=scope_series_id, creator_id="review-lab",
                               name="移除后转移", description="isolated", audience="isolated",
                               skill_name="knowledge-to-carousel")
            scope_topic_id = "scope-transfer-topic"
            repo.add_topic(topic_id=scope_topic_id, series_id=scope_series_id,
                           title="账号范围移除", source=TopicSource.MANUAL)
            scope_request_id = uuid4().hex
            scope_removed = client.post(f"/api/topics/{scope_topic_id}/remove", headers=scoped_a,
                                        json={"request_id": scope_request_id})
            assert scope_removed.status_code == 200 and scope_removed.json()["status"] == "deleted", scope_removed.text
            transferred = client.post(f"/api/series/{scope_series_id}/assignment", json={
                "creator_id": "other-account", "expected_revision": 1, "request_id": uuid4().hex,
            })
            assert transferred.status_code == 200, transferred.text
            original_owner_replay = client.post(f"/api/topics/{scope_topic_id}/remove", headers=scoped_a,
                                                json={"request_id": scope_request_id})
            assert original_owner_replay.status_code == 200 and original_owner_replay.json()["deduplicated"]
            original_owner_new_request = client.post(f"/api/topics/{scope_topic_id}/remove", headers=scoped_a,
                                                     json={"request_id": uuid4().hex})
            assert original_owner_new_request.status_code == 403, original_owner_new_request.text
            new_owner_replay = client.post(f"/api/topics/{scope_topic_id}/remove", headers=scoped_b,
                                           json={"request_id": uuid4().hex})
            assert new_owner_replay.status_code == 200 and new_owner_replay.json()["deduplicated"], new_owner_replay.text
            other_owner_old_request = client.post(f"/api/topics/{scope_topic_id}/remove", headers=scoped_b,
                                                  json={"request_id": scope_request_id})
            assert other_owner_old_request.status_code == 403, other_owner_old_request.text

            # Pending candidate dismissal is a durable tombstone keyed by the
            # same deterministic topic_id used by research selection.
            batch = seed_batch(app.state.topic_research, series_id, "d" * 32)
            batch_path = app.state.topic_research._path(batch["id"])
            batch_bytes = batch_path.read_bytes()
            old_preview_response = client.post(f"/api/topic-research/{batch['id']}/preview",
                                               json={"selections": [{"candidate_id": "c2"}]})
            assert old_preview_response.status_code == 200, old_preview_response.text
            old_preview = old_preview_response.json()
            candidate_id = "c1"
            pending_id = app.state.topic_research.topic_id(batch["id"], candidate_id)
            body = {"batch_id": batch["id"], "candidate_id": candidate_id}
            dismissed_request_id = uuid4().hex
            dismissed = _remove(client, pending_id, dismissed_request_id, **body)
            assert dismissed.status_code == 200, dismissed.text
            assert dismissed.json()["status"] == "dismissed"
            assert batch_path.read_bytes() == batch_bytes
            assert pending_id not in {item["id"] for item in client.get(library_url).json()["items"]}
            assert _remove(client, pending_id, **body).json()["deduplicated"] is True
            assert batch_path.read_bytes() == batch_bytes
            assert client.post(f"/api/topic-research/{batch['id']}/preview",
                               json={"selections": [{"candidate_id": candidate_id}]}).status_code >= 400

            # A Preview prepared before dismissal cannot race-confirm the same
            # candidate back into the Topic queue.
            dismissed_c2_id = app.state.topic_research.topic_id(batch["id"], "c2")
            dismissed_c2 = _remove(client, dismissed_c2_id,
                                   batch_id=batch["id"], candidate_id="c2")
            assert dismissed_c2.status_code == 200 and dismissed_c2.json()["status"] == "dismissed"
            old_preview_confirm = client.post(f"/api/operations/{old_preview['id']}/confirm", json={
                "expected_version": old_preview["version"],
                "expected_revision": old_preview["revision"],
                "confirmation_token": old_preview["confirmation_token"],
            })
            assert old_preview_confirm.status_code == 409, old_preview_confirm.text
            assert repo.get_topic(dismissed_c2_id) is None
            assert batch_path.read_bytes() == batch_bytes
            malformed_batch = seed_batch(app.state.topic_research, series_id, "f" * 32)
            malformed_pending_id = app.state.topic_research.topic_id(malformed_batch["id"], "c1")
            malformed_batch_path = app.state.topic_research._path(malformed_batch["id"])
            malformed_batch_bytes = malformed_batch_path.read_bytes()
            wrong_batch_replay = client.post(f"/api/topics/{pending_id}/remove", json={
                "request_id": uuid4().hex,
                "batch_id": malformed_batch["id"],
                "candidate_id": "c1",
            })
            assert wrong_batch_replay.status_code == 409, wrong_batch_replay.text
            unknown_batch_id = "a" * 32
            unknown_batch_topic = app.state.topic_research.topic_id(unknown_batch_id, "c1")
            unknown_batch = client.post(f"/api/topics/{unknown_batch_topic}/remove", json={
                "request_id": uuid4().hex, "batch_id": unknown_batch_id, "candidate_id": "c1",
            })
            assert unknown_batch.status_code == 404, unknown_batch.text

            # Body provenance and deterministic identifiers are verified; a
            # malformed scope cannot create a tombstone or touch the source JSON.
            for bad_body in (
                {"batch_id": "e" * 32, "candidate_id": "c1"},
                {"batch_id": malformed_batch["id"], "candidate_id": "not-a-candidate"},
                {"batch_id": malformed_batch["id"], "candidate_id": "c1", "id": "wrong-id"},
            ):
                response = client.post(f"/api/topics/{malformed_pending_id}/remove",
                                       json={"request_id": uuid4().hex, **bad_body})
                assert response.status_code >= 400, response.text
                assert malformed_batch_path.read_bytes() == malformed_batch_bytes

            # Owner checks apply before replay for deleted Topics, dismissed
            # candidates, and still-existing archived Topics.
            for topic_id, extra, replay_id in (
                (deleted_id, {}, request_id),
                (pending_id, body, dismissed_request_id),
                ("review-1", {}, archived_request_id),
            ):
                denied = client.post(f"/api/topics/{topic_id}/remove", headers=scoped_b,
                                     json={"request_id": uuid4().hex, **extra})
                assert denied.status_code == 403, denied.text
                denied_replay = client.post(f"/api/topics/{topic_id}/remove", headers=scoped_b,
                                            json={"request_id": replay_id, **extra})
                assert denied_replay.status_code == 403, denied_replay.text

            # Same request ID from another account cannot replay a successful
            # receipt even when the topic row is gone.
            denied_replay = client.post(f"/api/topics/{deleted_id}/remove", headers=scoped_b,
                                        json={"request_id": request_id})
            assert denied_replay.status_code == 403, denied_replay.text

            # Refresh-like repeated reads and a newly created app/database
            # process observe the durable tombstones; old candidates do not reappear.
            assert pending_id not in {item["id"] for item in client.get(library_url).json()["items"]}
            assert client.get(f"/api/topic-research/{batch['id']}").status_code == 200
            assert batch_path.read_bytes() == batch_bytes

        database.close()

        # Open a fresh ORM/database instance against the same isolated file to
        # prove removal facts survive an actual app/process restart.
        fresh = Database(f"sqlite:///{(root / 'review.db').as_posix()}")
        try:
            with fresh.session() as session:
                assert session.get(Topic, "review-2") is None
                assert session.get(Topic, "review-1") is not None
                assert session.get(ContentRun, history_run.id) is not None
                assert session.get(TopicRemoval, "review-2").status == "deleted"
                assert session.get(TopicRemoval, "review-1").status == "archived"
                assert session.get(TopicRemoval, pending_id).status == "dismissed"
                assert session.get(TopicRemoval, dismissed_c2_id).status == "dismissed"
            restarted_app = create_app(database=fresh,
                                       run_service=ContentRunService(fresh, output_root=root / "outputs"),
                                       chat_root=root / "restarted-sessions")
            with serve(restarted_app) as base_url, httpx.Client(
                base_url=base_url, timeout=20, trust_env=False
            ) as restarted_client:
                restarted_items = restarted_client.get(library_url).json()["items"]
                restarted_ids = {item["id"] for item in restarted_items}
                assert "review-1" not in restarted_ids and "review-2" not in restarted_ids
                assert pending_id not in restarted_ids and dismissed_c2_id not in restarted_ids
                assert restarted_client.get(f"/api/runs/{history_run.id}").status_code == 200
                assert restarted_client.get(image_urls[0]).status_code == 200
        finally:
            fresh.close()

    print("topic_remove_smoke=passed deleted=passed archived_history=passed pending_dismissal=passed scope=passed active_guard=passed")


if __name__ == "__main__":
    main()
