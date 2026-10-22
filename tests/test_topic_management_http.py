"""Real isolated HTTP/SQLite contracts; no formal data or model requests.

The local ReviewProducer creates fixture image files solely to verify historical
evidence preservation. Synthetic running states and lost responses are faults,
not claims about real model quality. Browser/DeepSeek acceptance is separate.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import select

from creatoros.storage import (ContentAttempt, ContentRevision, ContentRun, ContentRunEvent,
                              ContentRunStatus, ContentRepository, CreatorPlatform,
                              Series, Topic, TopicSource, TopicRemoval, WriteReceipt)
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture
from creatoros.web.agent_scope import AgentScopeGuard


def rows(database, models):
    with database.session() as session:
        return {model.__tablename__: [{column.name: deepcopy(getattr(item, column.name))
                for column in model.__table__.columns} for item in session.scalars(select(model))]
                for model in models}


class TopicManagementHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db, self.runs, self.producer, self.app = make_fixture(self.root)
        self.repo = ContentRepository(self.db)
        self.repo.create_creator(creator_id="other", display_name="Other", platform=CreatorPlatform.XIAOHONGSHU)
        self.repo.create_series(series_id="other-series", creator_id="other", name="Other series",
            description="isolated", audience="isolated", skill_name="knowledge-to-carousel")
        self.repo.add_topic(topic_id="foreign-topic", series_id="other-series", title="FOREIGN_PRIVATE",
                            source=TopicSource.MANUAL)
        self.history = self.runs.create("review-1")
        self.runs.execute(self.history.id)
        self.host = serve(self.app)
        self.base = self.host.__enter__()
        self.client = httpx.Client(base_url=self.base, timeout=15, trust_env=False)
        self.sid = self.app.state.chat.create("review-lab")["id"]
        self.other_sid = self.app.state.chat.create("other")["id"]
        self.scoped = {"x-creatoros-agent-session": self.sid}
        self.foreign_scoped = {"x-creatoros-agent-session": self.other_sid}

    def tearDown(self):
        try:
            self.client.close()
            self.host.__exit__(None, None, None)
        finally:
            self.db.close()
            self.temporary.cleanup()

    def get(self, topic="review-2", **kwargs):
        return self.client.get(f"/api/topics/{topic}", **kwargs)

    def edit(self, topic="review-2", *, digest=None, request_id=None, **changes):
        digest = digest or self.get(topic).json()["edit_digest"]
        return self.client.patch(f"/api/topics/{topic}", headers=self.scoped,
            json={"expected_digest": digest, "request_id": request_id or uuid4().hex, **changes})

    def test_direct_edit_preserves_identity_and_list_matches_readback(self):
        before = rows(self.db, [Topic, ContentRun])
        original = self.get(headers=self.scoped)
        self.assertEqual(original.status_code, 200, original.text)
        self.assertEqual(original.headers["cache-control"], "no-store")
        self.assertEqual(original.json()["management_actions"], ["get", "update", "remove"])
        changed = self.edit(brief="上格安慰朋友，下格深夜自责。")
        self.assertEqual(changed.status_code, 200, changed.text)
        body = changed.json()
        self.assertEqual(body["topic"]["id"], "review-2")
        self.assertEqual(body["topic"]["brief"], "上格安慰朋友，下格深夜自责。")
        self.assertEqual(body["changed_fields"], ["brief"])
        self.assertEqual(body["effect"], "existing_runs_unchanged")
        after = rows(self.db, [Topic, ContentRun])
        self.assertEqual(len(after["topics"]), len(before["topics"]))
        self.assertEqual(after["content_runs"], before["content_runs"])
        self.assertNotEqual(body["topic"]["edit_digest"], original.json()["edit_digest"])
        listed = self.client.get("/api/series/agent-notes/topics").json()["items"]
        item = next(row for row in listed if row["id"] == "review-2")
        self.assertEqual(item, self.get().json())
        self.assertEqual(item["edit_digest"], body["topic"]["edit_digest"])
        # No Run starts as a side effect of a metadata-only edit.
        self.assertEqual(self.producer.calls, 1)

    def test_cas_replay_and_strong_request_binding(self):
        original = self.get().json()
        key = uuid4().hex
        request = {"expected_digest": original["edit_digest"], "request_id": key, "brief": "版本一"}
        response = self.client.patch("/api/topics/review-2", headers=self.scoped, json=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.edit(brief="版本二").status_code, 200)
        current = self.get().json()
        # Exact replay returns frozen first receipt and never overwrites version two.
        replay = self.client.patch("/api/topics/review-2", headers=self.scoped, json=request)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["deduplicated"])
        self.assertEqual(replay.json()["topic"]["brief"], "版本一")
        self.assertEqual(self.get().json(), current)
        stale = self.edit(digest=original["edit_digest"], brief="旧编辑器覆盖")
        self.assertEqual(stale.status_code, 409, stale.text)
        collision = self.client.patch("/api/topics/review-2", headers=self.scoped,
            json={**request, "brief": "同键异文"})
        self.assertEqual(collision.status_code, 409, collision.text)
        collision = self.client.patch("/api/topics/review-1", headers=self.scoped, json=request)
        self.assertEqual(collision.status_code, 403, collision.text)
        self.assertEqual(self.get().json(), current)

    def test_receipt_read_after_lost_response_and_restart(self):
        key = uuid4().hex
        digest = self.get().json()["edit_digest"]
        response = self.edit(digest=digest, request_id=key, brief="已保存，响应丢失")
        self.assertEqual(response.status_code, 200, response.text)
        # The caller deliberately discards the real successful response. Recovery
        # uses only GET, including after reopening a SQLite connection.
        path = f"/api/topics/review-2/edit-receipts/{key}"
        receipt = self.client.get(path, headers=self.scoped)
        self.assertEqual(receipt.status_code, 200, receipt.text)
        self.assertTrue(receipt.json()["deduplicated"])
        self.assertEqual(receipt.json()["topic"]["brief"], "已保存，响应丢失")
        self.assertEqual(receipt.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get(f"/api/topics/review-2/edit-receipts/{uuid4().hex}",
            headers=self.scoped).json()["status"], "unknown")
        with self.db.session() as session:
            saved = session.get(WriteReceipt, key)
            self.assertEqual(saved.operation, "edit_topic")
            self.assertEqual(saved.response_json["creator_id"], "review-lab")
        self.db.engine.dispose()
        reopened = self.client.get(path, headers=self.scoped)
        self.assertEqual(reopened.json(), receipt.json())

    def test_account_guard_rejects_cross_account_and_unversioned_agent_edits(self):
        before = rows(self.db, [Topic, ContentRun, WriteReceipt])
        foreign = self.get("foreign-topic", headers=self.scoped)
        self.assertEqual(foreign.status_code, 403, foreign.text)
        foreign_digest = self.get("foreign-topic").json()["edit_digest"]
        denied = self.client.patch("/api/topics/foreign-topic", headers=self.scoped,
            json={"brief": "越权", "expected_digest": foreign_digest, "request_id": uuid4().hex})
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(self.client.patch("/api/topics/review-2", headers=self.scoped,
            json={"brief": "缺CAS"}).status_code, 403)
        overview = {"x-creatoros-agent-session": self.app.state.chat.create()["id"]}
        self.assertEqual(self.client.patch("/api/topics/review-2", headers=overview,
            json={"brief": "总览也缺CAS"}).status_code, 403)
        self.assertEqual(rows(self.db, [Topic, ContentRun, WriteReceipt]), before)
        # Header-free old Web editing remains supported; one identity half is rejected.
        self.assertEqual(self.client.patch("/api/topics/review-2", json={"brief": "旧网页"}).status_code, 200)
        self.assertEqual(self.client.patch("/api/topics/review-2",
            json={"brief": "缺半边", "request_id": uuid4().hex}).status_code, 422)

    def test_edit_and_archive_preserve_entire_historical_production(self):
        models = [ContentRun, ContentRevision, ContentAttempt, ContentRunEvent]
        before = rows(self.db, models)
        images = {path.relative_to(self.runs.output_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in self.runs.output_root.rglob("*") if path.is_file()}
        edited = self.edit("review-1", title="新的选题标题", brief="后续重做才使用的新内容")
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(rows(self.db, models), before)
        removed = self.client.post("/api/topics/review-1/remove", headers=self.scoped,
            json={"request_id": uuid4().hex})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["status"], "archived")
        self.assertEqual(removed.json()["series_id"], "agent-notes")
        self.assertEqual(rows(self.db, models), before)
        self.assertEqual({path.relative_to(self.runs.output_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in self.runs.output_root.rglob("*") if path.is_file()}, images)
        self.assertEqual(self.get("review-1").status_code, 404)
        blocked = self.client.patch("/api/topics/review-1", json={"brief": "复活"})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self.client.get(f"/api/runs/{self.history.id}").status_code, 200)

    def test_active_runs_queue_and_discussions_block_edit_and_remove(self):
        active = self.runs.create("review-2")
        for status in (ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING):
            with self.db.session() as session:
                session.get(ContentRun, active.id).status = status
            try:
                self.assertEqual(self.get().json()["management_actions"], ["get"])
                self.assertEqual(self.edit(brief="禁止").status_code, 409)
                self.assertEqual(self.client.post("/api/topics/review-2/remove", json={
                    "request_id": uuid4().hex}).status_code, 409)
                with self.db.session() as session:
                    self.assertEqual(session.get(ContentRun, active.id).status, status)
            finally:
                with self.db.session() as session:
                    session.get(ContentRun, active.id).status = ContentRunStatus.QUEUED
        # Hold only this isolated dispatcher's lock so a fixture authorization
        # cannot be consumed while the actual HTTP guard is inspected.
        executor = self.app.state.executor
        with executor._lock:
            with self.db.session() as session:
                current_version = session.get(ContentRun, active.id).version
            queued = executor.queue.enqueue(active.id, expected_version=current_version)
            try:
                for state in ("pending", "running"):
                    executor.queue.transition(queued.id, state)
                    self.assertEqual(self.get().json()["management_actions"], ["get"])
                    self.assertEqual(self.edit(brief="禁止队列改写").status_code, 409)
                    self.assertEqual(self.client.post("/api/topics/review-2/remove", json={
                        "request_id": uuid4().hex}).status_code, 409)
            finally:
                executor.queue.transition(queued.id, "cancelled")
        with patch.object(self.app.state.content_discussions, "_records", return_value=[{
                "run_id": active.id, "status": "running"}]):
            self.assertEqual(self.get().json()["management_actions"], ["get"])
            self.assertEqual(self.edit(brief="禁止讨论改写").status_code, 409)
            self.assertEqual(self.client.post("/api/topics/review-2/remove", json={
                "request_id": uuid4().hex}).status_code, 409)
        self.assertEqual(self.producer.calls, 1)

    def test_receipt_and_topic_scope_close_after_series_transfer(self):
        key = uuid4().hex
        edited = self.edit(request_id=key, brief="原账号写入")
        self.assertEqual(edited.status_code, 200, edited.text)
        path = f"/api/topics/review-2/edit-receipts/{key}"
        self.assertEqual(self.client.get(path, headers=self.foreign_scoped).status_code, 403)
        with self.db.session() as session:
            # Move an independent no-history Topic to a separately transferred
            # series, without exposing the original series' frozen history.
            series = Series(id="moved", creator_id="review-lab", name="Moved", description="", audience="",
                            skill_name="knowledge-to-carousel")
            session.add(series)
            session.flush()
            topic = session.get(Topic, "review-2")
            topic.series_id = "moved"
        moved_key = uuid4().hex
        self.assertEqual(self.edit(request_id=moved_key, brief="转移前写入").status_code, 200)
        with self.db.session() as session:
            session.get(Series, "moved").creator_id = "other"
        for target, headers in ((key, self.scoped), (moved_key, self.scoped),
                                (moved_key, self.foreign_scoped)):
            denied = self.client.get(f"/api/topics/review-2/edit-receipts/{target}", headers=headers)
            self.assertEqual(denied.status_code, 403, denied.text)

    def test_edit_waits_for_production_lock_and_rechecks_new_queue_authorization(self):
        active = self.runs.create("review-2")
        digest = self.get().json()["edit_digest"]
        before_topic = rows(self.db, [Topic])
        checked = Event()
        responses = {}
        original_check = AgentScopeGuard.check

        async def record_check(guard, request):
            result = await original_check(guard, request)
            if request.method == "PATCH" and request.url.path == "/api/topics/review-2":
                checked.set()
            return result

        def write():
            responses["edit"] = self.client.patch("/api/topics/review-2", headers=self.scoped,
                json={"brief": "不能跨过刚入队的任务", "expected_digest": digest, "request_id": uuid4().hex})

        executor = self.app.state.executor
        with executor._lock, patch.object(AgentScopeGuard, "check", record_check):
            worker = Thread(target=write)
            queued = None
            try:
                with self.runs._write_lock:
                    worker.start()
                    self.assertTrue(checked.wait(5), "request did not reach actual HTTP scope guard")
                    self.assertNotIn("edit", responses, "edit bypassed the production write lock")
                    queued = executor.queue.enqueue(active.id, expected_version=active.version)
                worker.join(10)
                self.assertFalse(worker.is_alive())
                self.assertEqual(responses["edit"].status_code, 409, responses["edit"].text)
                self.assertEqual(rows(self.db, [Topic]), before_topic)
            finally:
                if queued is not None:
                    executor.queue.transition(queued.id, "cancelled")
                worker.join(15)
        self.assertEqual(self.producer.calls, 1)

    def test_inactive_series_is_read_only_and_delete_is_recoverably_idempotent(self):
        original = self.get().json()
        with self.db.session() as session:
            session.get(Series, "agent-notes").is_active = False
        self.assertEqual(self.get().json()["management_actions"], ["get"])
        self.assertEqual(self.edit(digest=original["edit_digest"], brief="已停用").status_code, 409)
        self.assertEqual(self.client.post("/api/topics/review-2/remove", json={
            "request_id": uuid4().hex}).status_code, 409)
        with self.db.session() as session:
            session.get(Series, "agent-notes").is_active = True
        key = uuid4().hex
        removed = self.client.post("/api/topics/review-2/remove", headers=self.scoped,
            json={"request_id": key})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["status"], "deleted")
        self.assertEqual(removed.json()["series_id"], "agent-notes")
        replay = self.client.post("/api/topics/review-2/remove", headers=self.scoped,
            json={"request_id": key})
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["deduplicated"])
        self.assertEqual(replay.json()["series_id"], "agent-notes")
        with self.db.session() as session:
            self.assertIsNone(session.get(Topic, "review-2"))
            self.assertEqual(session.get(TopicRemoval, "review-2").status, "deleted")


if __name__ == "__main__":
    unittest.main()
