"""Isolated SQLite and controlled blocking/failure injection; never calls a model."""
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from creatoros.integrations.codex import CodexUsage
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.integrations.topic_research import TopicResearchService, ResearchReceipt, _write
from creatoros.storage import ContentRepository, Database, upgrade_database
from creatoros.storage.models import Base, TopicResearchQueueItem
from creatoros.tools.research_wait import observe_research
from creatoros.web import create_app
from tests.agent_studio_support import serve


def result(note="isolated queue fixture"):
    return SimpleNamespace(receipt=ResearchReceipt(candidates=[], note=note),
                           thread_id="local-injected-thread", usage=CodexUsage())


class ResearchQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.url = "sqlite:///" + (Path(self.tmp.name) / "queue.db").as_posix()
        upgrade_database(self.url)
        self.db = Database(self.url)
        self.addCleanup(self.db.close)
        self.repo = ContentRepository(self.db)
        self.repo.create_creator(creator_id="a", display_name="isolated")
        self.repo.create_series(series_id="s", creator_id="a", name="queue test",
                                description="", audience="", skill_name="knowledge-to-carousel")
        self.calls = []
        calls = self.calls

        class Researcher:
            def research(self, snapshot, count, instructions, workspace, cancel):
                calls.append(instructions)
                return result()

        self.service = TopicResearchService(
            self.db, ProducerSkillCatalog(skills_root_for(self.db)), researcher=Researcher())
        self.addCleanup(self.service.shutdown)

    def join(self, service=None):
        service = service or self.service
        worker = service.worker
        self.assertIsNotNone(worker)
        worker.join(10)
        self.assertFalse(worker.is_alive())

    def rows(self):
        with self.db.session() as session:
            return session.scalars(select(TopicResearchQueueItem).order_by(TopicResearchQueueItem.id)).all()

    def test_migration_matches_model_and_does_not_mutate_existing_objects(self):
        self.assertEqual(self.repo.get_creator("a").display_name, "isolated")
        with self.db.engine.connect() as connection:
            self.assertEqual(compare_metadata(MigrationContext.configure(connection), Base.metadata), [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.repo.list_topics("s"), ())

    def test_fifo_distinct_requests_accepted_dedup_and_failure_does_not_block(self):
        started, release = Event(), Event()
        self.addCleanup(release.set)
        calls = self.calls

        class Controlled:
            def research(self, snapshot, count, instructions, workspace, cancel):
                calls.append(instructions)
                if instructions == "first":
                    started.set()
                    if not release.wait(8):
                        raise TimeoutError("test release")
                    raise RuntimeError("injected model failure")
                return result()

        self.service.researcher = Controlled()
        first = self.service.submit("s", 1, "first")
        self.assertTrue(started.wait(5))
        second = self.service.submit("s", 2, "Ａ  TOPIC")
        third = self.service.submit("s", 3, "third")
        self.assertEqual(second["status"], "queued")
        self.assertEqual(third["status"], "queued")
        self.assertEqual(self.service.submit("s", 2, "a\ntopic")["id"], second["id"])
        self.assertTrue(self.service.has_active_for_series("s"))
        self.assertEqual([r.status for r in self.rows()], ["researching", "queued", "queued"])
        release.set()
        self.join()
        self.assertEqual(calls, ["first", "Ａ  TOPIC", "third"])
        self.assertEqual([self.service.get(x["id"])["status"] for x in [first, second, third]],
                         ["failed", "ready", "ready"])
        self.assertEqual([r.status for r in self.rows()], ["failed", "ready", "ready"])
        self.assertFalse(self.service.has_active_for_series("s"))
        self.assertEqual(self.repo.list_topics("s"), ())

    def test_restart_consumes_only_never_started_and_reconciles_finished_receipt(self):
        # Pause dispatch to exercise an actual persisted queue, not a Python queue.
        with patch.object(self.service, "_kick"):
            queued = self.service.submit("s", 1, "pending")
            interrupted = self.service.submit("s", 1, "started-before-crash")
            finished = self.service.submit("s", 1, "receipt-before-crash")
        with self.db.session() as session:
            for row in session.scalars(select(TopicResearchQueueItem)):
                if row.batch_id != queued["id"]:
                    row.status = "researching"
        finished_record = self.service._load(finished["id"])
        finished_record["status"] = "ready"
        finished_record["note"] = "already persisted receipt"
        _write(self.service._path(finished["id"]), finished_record)
        preserved = self.service._path(finished["id"]).read_bytes()
        self.service.shutdown()
        other_db = Database(self.url)
        self.addCleanup(other_db.close)
        restarted = TopicResearchService(other_db, self.service.catalog, self.service.researcher)
        self.addCleanup(restarted.shutdown)
        restarted.start()
        self.join(restarted)
        self.assertEqual(self.calls, ["pending"])
        self.assertEqual(restarted.get(interrupted["id"])["status"], "interrupted")
        self.assertEqual(restarted.get(queued["id"])["status"], "ready")
        self.assertEqual(restarted.get(finished["id"])["status"], "ready")
        self.assertEqual(restarted._path(finished["id"]).read_bytes(), preserved)

    def test_recovery_json_write_failure_still_updates_sql_and_runs_next_batch(self):
        with patch.object(self.service, "_kick"):
            first = self.service.submit("s", 1, "old-running")
            second = self.service.submit("s", 1, "pending")
        old = self.service._load(first["id"])
        old["status"] = "researching"
        _write(self.service._path(first["id"]), old)
        original_bytes = self.service._path(first["id"]).read_bytes()
        with self.db.session() as session:
            row = session.scalar(select(TopicResearchQueueItem).where(
                TopicResearchQueueItem.batch_id == first["id"]))
            row.status = "researching"
        self.service.shutdown()
        restarted = TopicResearchService(self.db, self.service.catalog, self.service.researcher)
        self.addCleanup(restarted.shutdown)

        def deny_one_recovery(path, value):
            if Path(path) == restarted._path(first["id"]) and value.get("status") == "interrupted":
                raise PermissionError("injected recovery JSON failure")
            return _write(path, value)

        with self.assertLogs("creatoros.integrations.topic_research", level="ERROR"), \
             patch("creatoros.integrations.topic_research._write", side_effect=deny_one_recovery):
            restarted.start()
            self.join(restarted)
        self.assertEqual(self.calls, ["pending"])
        self.assertEqual([r.status for r in self.rows()], ["interrupted", "ready"])
        self.assertEqual(restarted.get(first["id"])["status"], "interrupted")
        self.assertEqual(restarted.get(second["id"])["status"], "ready")
        self.assertEqual(restarted._path(first["id"]).read_bytes(), original_bytes)

    def test_shutdown_keeps_pending_and_does_not_restart_cancelled_paid_call(self):
        started = Event()
        calls = self.calls

        class Controlled:
            def research(self, snapshot, count, instructions, workspace, cancel):
                calls.append(instructions)
                if instructions == "first":
                    started.set()
                    if not cancel.wait(8):
                        raise TimeoutError("test stop")
                    raise RuntimeError("stopped")
                return result()

        self.service.researcher = Controlled()
        first = self.service.submit("s", 1, "first")
        self.assertTrue(started.wait(5))
        second = self.service.submit("s", 1, "pending")
        self.service.shutdown()
        self.assertEqual(self.service.get(first["id"])["status"], "interrupted")
        self.assertEqual(self.service.get(second["id"])["status"], "queued")
        restarted = TopicResearchService(self.db, self.service.catalog, self.service.researcher)
        self.addCleanup(restarted.shutdown)
        restarted.start()
        self.join(restarted)
        self.assertEqual(calls, ["first", "pending"])

    def test_unreadable_one_batch_is_failed_and_later_batch_runs(self):
        with patch.object(self.service, "_kick"):
            broken = self.service.submit("s", 1, "broken")
            later = self.service.submit("s", 1, "later")
        self.service._path(broken["id"]).unlink()
        self.service.start()
        self.join()
        self.assertEqual([r.status for r in self.rows()], ["failed", "ready"])
        self.assertEqual(self.service.get(later["id"])["status"], "ready")
        self.assertEqual(self.calls, ["later"])

    def test_corrupt_history_does_not_block_submit_list_or_start(self):
        with patch.object(self.service, "_kick"):
            broken = self.service.submit("s", 1, "broken")
            later = self.service.submit("s", 1, "later")
        self.service._path(broken["id"]).write_text("{broken", encoding="utf-8")
        (self.service.root / "batches" / "invalid-shape.json").write_text(
            '{"id":"incomplete","series_id":"s","status":"ready"}', encoding="utf-8")
        self.assertEqual([x["id"] for x in self.service.list("s")], [later["id"]])
        self.assertEqual(self.service.snapshot("s")["existing_candidates"], [])
        self.service.start()
        self.join()
        self.assertEqual([r.status for r in self.rows()], ["failed", "ready"])
        third = self.service.submit("s", 1, "third")
        self.join()
        self.assertEqual(self.service.get(third["id"])["status"], "ready")
        self.assertEqual(self.calls, ["later", "third"])

    def test_queued_item_reads_newer_candidate_context_at_dispatch(self):
        with patch.object(self.service, "_kick"):
            first = self.service.submit("s", 1, "first")
            second = self.service.submit("s", 1, "second")
        snapshots = []

        class Controlled:
            def research(self, snapshot, count, instructions, workspace, cancel):
                snapshots.append(snapshot)
                return SimpleNamespace(receipt=ResearchReceipt(candidates=[{
                    "title": instructions, "angle": "test angle", "rationale": "test reason",
                    "sources": [{"title": "Python", "url": "https://docs.python.org/3/"}],
                }], note="fixture"), thread_id="local-injected", usage=CodexUsage())

        self.service.researcher = Controlled()
        self.service.start()
        self.join()
        self.assertEqual(snapshots[0]["existing_candidates"], [])
        self.assertEqual(snapshots[1]["existing_candidates"], [{"title": "first", "angle": "test angle"}])
        self.assertEqual(self.service.get(second["id"])["status"], "ready")

    def test_observer_does_not_mistake_queued_for_complete(self):
        views, reads = [], []
        batch = {"id": "a" * 32, "status": "queued", "url": "/series/s"}
        pending = iter([{**batch, "status": "researching"}, {**batch, "status": "ready", "candidates": []}])

        def read(method, path, **kwargs):
            reads.append((method, path))
            return next(pending)

        stopping = SimpleNamespace(is_set=lambda: False, wait=lambda _: False)
        context = SimpleNamespace(research_progress=views.append, stopping=stopping,
                                  research_wait_timeout_seconds=5)
        observed = observe_research(SimpleNamespace(request=read), batch, context)
        self.assertEqual(json.loads(observed.content)["status"], "ready")
        self.assertEqual([v["status"] for v in views], ["queued", "researching", "ready"])
        self.assertEqual(len(reads), 2)
        self.assertTrue(all(method == "GET" for method, _ in reads))

    def test_thread_launch_failure_keeps_accepted_queue_recoverable(self):
        with patch("creatoros.integrations.topic_research.Thread.start", side_effect=RuntimeError("no thread")):
            accepted = self.service.submit("s", 1, "pending")
        self.assertEqual(accepted["status"], "queued")
        self.assertFalse(self.service._draining)
        self.assertEqual(self.rows()[0].status, "queued")
        self.assertEqual(self.calls, [])
        self.service.start()
        self.join()
        self.assertEqual(self.calls, ["pending"])
        self.assertEqual(self.service.get(accepted["id"])["status"], "ready")

    def test_failed_queue_commit_is_not_left_as_accepted_or_started(self):
        original = Session.commit

        def fail_new_queue(session):
            if any(isinstance(row, TopicResearchQueueItem) for row in session.new):
                raise RuntimeError("injected queue commit failure")
            return original(session)

        with patch.object(Session, "commit", fail_new_queue):
            failed = self.service.submit("s", 1, "not accepted")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_type"], "research_queue_write_failed")
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.service.has_active_for_series("s"))

    def test_commit_response_failure_reuses_actual_authorization_without_duplicate(self):
        original = Session.commit

        def commit_then_raise(session):
            is_new = any(isinstance(row, TopicResearchQueueItem) for row in session.new)
            original(session)
            if is_new:
                raise RuntimeError("injected response after commit")

        with patch.object(Session, "commit", commit_then_raise):
            accepted = self.service.submit("s", 1, "accepted once")
        self.join()
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.calls, ["accepted once"])
        self.assertEqual(self.service.get(accepted["id"])["status"], "ready")

    def test_real_http_accepts_distinct_request_while_first_is_blocked(self):
        started, release = Event(), Event()
        calls = self.calls

        class Controlled:
            def research(self, snapshot, count, instructions, workspace, cancel):
                calls.append(instructions)
                if instructions == "first":
                    started.set()
                    while not release.wait(.02):
                        if cancel.is_set():
                            raise RuntimeError("stopped")
                return result()

        self.service.researcher = Controlled()
        app = create_app(database=self.db, topic_research_service=self.service,
                         chat_root=Path(self.tmp.name) / "chats")
        with serve(app) as base, httpx.Client(base_url=base, timeout=5, trust_env=False) as client:
            try:
                endpoint = "/api/series/s/topic-research"
                first = client.post(endpoint, json={"count": 1, "instructions": "first"})
                self.assertEqual(first.status_code, 202, first.text)
                self.assertTrue(started.wait(5))
                second = client.post(endpoint, json={"count": 1, "instructions": "second"})
                self.assertEqual(second.status_code, 202, second.text)
                self.assertEqual(second.json()["status"], "queued")
                self.assertEqual(calls, ["first"])
                self.assertEqual(client.post(endpoint, json={
                    "count": 1, "instructions": "second"
                }).json()["id"], second.json()["id"])
                release.set()
                self.join()
                loaded = client.get("/api/topic-research/" + second.json()["id"])
                self.assertEqual(loaded.json()["status"], "ready")
                self.assertEqual(calls, ["first", "second"])
                self.assertEqual(self.repo.list_topics("s"), ())
            finally:
                release.set()


if __name__ == "__main__":
    unittest.main()
