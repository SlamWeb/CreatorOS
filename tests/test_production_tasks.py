"""Real isolated SQLite/queue/files with controlled local production faults.

These tests do not call a model or assert generated-image quality.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
import unittest

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text

from creatoros.integrations.producer_skills import _digest
from creatoros.runs import ContentRunService, ManagedRunExecutor, ContentRunError
from creatoros.runs.production_tasks import ProductionTaskService
from creatoros.storage import (Base, ContentRun, ContentRunStatus, MediaCollection, ProductionTask,
                              Series, Topic, WriteReceipt, upgrade_database)
from creatoros.storage.models import ProductionQueueItem
from tests.test_production_queue import Producer, wait
from tests import test_selected_skill_run as selected_fixture


class TaskProducer(Producer):
    """Hold task workers by their independent titles and record input ownership."""
    def __init__(self, fail_titles=()):
        super().__init__()
        self.lock, self.release = Lock(), Event()
        self.active, self.requests, self.fail_titles = set(), [], set(fail_titles)
        self.maximum = 0

    def produce_to(self, **request):
        title = request["topic_title"]
        with self.lock:
            self.requests.append(request)
            self.active.add(title)
            self.maximum = max(self.maximum, len(self.active))
        try:
            while not self.release.wait(.01):
                if request["cancel_event"].is_set():
                    raise RuntimeError("controlled task shutdown")
            if title in self.fail_titles:
                raise RuntimeError("controlled task failure")
            return super().produce_to(**request)
        finally:
            with self.lock:
                self.active.discard(title)

    def count_active(self):
        with self.lock:
            return len(self.active)


class CleanupTaskProducer(Producer):
    def __init__(self):
        super().__init__()
        self.old_started, self.cleanup_started, self.cleanup_release = Event(), Event(), Event()
        self.old_cancel = None
        self.first = True

    def produce_to(self, **request):
        if self.first:
            self.first = False
            request["on_thread_started"]("old-task-cleanup")
            self.old_cancel = request["cancel_event"]
            self.old_started.set()
            self.old_cancel.wait(10)
            self.cleanup_started.set()
            self.cleanup_release.wait(10)
            raise RuntimeError("controlled interrupted task cleanup")
        return super().produce_to(**request)


class ProductionTaskTests(unittest.TestCase):
    skill = selected_fixture.SelectedSkillRunTests.skill
    setUp = selected_fixture.SelectedSkillRunTests.setUp

    def make(self, *, concurrency=2, fail_titles=()):
        producer = TaskProducer(fail_titles)
        runs = ContentRunService(self.db, producer_factory=lambda: producer,
            output_root=self.root / "tasks-output", production_protocol="legacy")
        executor = ManagedRunExecutor(runs, max_concurrency=concurrency, shutdown_timeout=3)
        self.addCleanup(executor.shutdown)
        self.addCleanup(producer.release.set)
        return producer, runs, executor, ProductionTaskService(self.db, runs, executor)

    def selection(self, skill):
        return dict(skill_id=skill["id"], expected_digests={skill["id"]: skill["digest"]})

    def counts(self):
        with self.db.session() as session:
            return {model.__tablename__: session.scalar(select(func.count()).select_from(model))
                    for model in (Topic, ProductionTask, ContentRun, ProductionQueueItem, MediaCollection, WriteReceipt)}

    def topic_row(self):
        with self.db.engine.connect() as connection:
            return dict(connection.execute(text("SELECT * FROM topics WHERE id='topic'")).mappings().one())

    def finish(self, producer, executor, result):
        producer.release.set()
        wait(lambda: all(not executor.is_submitted(item["run_id"]) for item in result["items"]), timeout=12)

    def test_same_topic_parallel_failure_isolation_legacy_and_parent_preserved(self):
        producer, runs, executor, tasks = self.make(fail_titles={"Failure"})
        legacy = runs.create("topic")
        before = self.topic_row()
        result = tasks.create("topic", request_id="parallel", tasks=[
            dict(title="Failure"), dict(title="Success"), dict(title="Later")])
        wait(lambda: producer.count_active() == 2)
        self.assertEqual(producer.maximum, 2)
        self.assertEqual(len(producer.requests), 2)
        self.assertEqual(runs.create("topic").id, legacy.id)
        self.assertEqual(self.counts()["topics"], 3)
        self.finish(producer, executor, result)
        values = tasks.list("topic")
        self.assertEqual([item["status"] for item in values["items"]], ["failed", "awaiting_approval", "awaiting_approval"])
        self.assertEqual(self.topic_row(), before)
        self.assertEqual(runs.get(legacy.id).status, ContentRunStatus.QUEUED)
        self.assertEqual(len({request["directory"] for request in producer.requests}), 3)
        self.assertTrue(all(request["thread_id"] is None for request in producer.requests))
        self.assertEqual(values["creator_id"], "creator")
        self.assertIsNotNone(values["collection_id"])
        with self.db.session() as session:
            rows = list(session.scalars(select(ProductionTask)))
            self.assertEqual({row.collection_id for row in rows}, {values["collection_id"]})
        self.assertEqual(executor.queue_snapshot(result["items"][0]["run_id"])["status"], "failed")

    def test_default_and_selected_freeze_complete_skill_before_queue(self):
        producer, runs, executor, tasks = self.make(concurrency=1)
        other = self.skill("other", "legacy_end_to_end")
        result = tasks.create("topic", request_id="frozen", tasks=[dict(title="Default"),
            dict(title="Selected", skill_overrides=self.selection(other))])
        wait(lambda: producer.count_active() == 1)
        originals = []
        for item, selected in zip(result["items"], (self.base, other)):
            run = runs.get(item["run_id"])
            frozen = runs.output_root / "creator" / "single" / run.id / "skill-snapshot" / "skills" / "single"
            self.assertEqual(_digest(frozen), selected["digest"])
            self.assertEqual((frozen / "scripts/method.py").read_text(), "print('original')\n")
            self.assertEqual(run.input_snapshot_json["skill_digest"], selected["digest"])
            self.assertIn(" v1", item["skill_summary"])
            self.assertIn("不代表本任务要完成全部内容", run.input_snapshot_json["topic_brief"])
            self.assertIn("不要补做父选题中的其他场景", run.input_snapshot_json["topic_brief"])
            originals.append((frozen, _digest(frozen)))
        (self.catalog.locate(other["id"]) / "scripts/method.py").write_text("changed", encoding="utf-8")
        self.finish(producer, executor, result)
        self.assertEqual([item["status"] for item in tasks.list("topic")["items"]], ["awaiting_approval"] * 2)
        for frozen, digest in originals:
            self.assertEqual(_digest(frozen), digest)

    def test_replay_concurrent_and_changed_payload_conflict_no_duplicate_authorization(self):
        producer, runs, executor, tasks = self.make(concurrency=1)
        payload = [dict(title="One"), dict(title="Two")]
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(lambda _: tasks.create("topic", request_id="replay", tasks=payload), range(3)))
        identities = [[item["id"] for item in result["items"]] for result in responses]
        self.assertEqual(identities, [identities[0]] * 3)
        with self.assertRaises(ContentRunError) as raised:
            tasks.create("topic", request_id="replay", tasks=[dict(title="Changed")])
        self.assertEqual(raised.exception.code, "idempotency_conflict")
        self.assertEqual(self.counts()["production_queue_items"], 2)
        self.assertEqual(self.counts()["production_tasks"], 2)
        self.finish(producer, executor, responses[0])
        self.assertEqual(len(producer.requests), 2)

    def test_invalid_sibling_rolls_back_tasks_runs_queue_collection_and_receipt(self):
        producer, runs, executor, tasks = self.make()
        before = self.counts()
        with self.assertRaises(ContentRunError):
            tasks.create("topic", request_id="rollback", tasks=[dict(title="Valid"), dict(title="Bad",
                skill_overrides=dict(skill_id="missing--0123456789abcdef", expected_digests={"missing--0123456789abcdef": "a" * 64}))])
        self.assertEqual(self.counts(), before)
        self.assertEqual(producer.requests, [])

    def test_fresh_stays_in_task_and_keeps_original_skill_despite_series_rebind(self):
        producer, runs, executor, tasks = self.make()
        result = tasks.create("topic", request_id="fresh", tasks=[dict(title="One"), dict(title="Two")])
        self.finish(producer, executor, result)
        first, sibling = result["items"]
        source = runs.get(first["run_id"])
        with self.db.session() as session:
            session.get(Series, "single").skill_name = "knowledge-to-carousel"
        fresh = runs.restart_fresh(source.id, expected_version=source.version, request_id="fresh-one")
        self.assertEqual(fresh.task_id, first["id"])
        self.assertEqual(fresh.input_snapshot_json["skill_name"], self.base["id"])
        self.assertEqual(fresh.input_snapshot_json["skill_digest"], self.base["digest"])
        self.assertEqual(tasks.list("topic")["items"][0]["run_id"], fresh.id)
        sibling_run = runs.get(sibling["run_id"])
        runs.request_revision(sibling_run.id, "Sibling still current", expected_version=sibling_run.version)
        with self.assertRaises(ContentRunError) as raised:
            runs.cancel(source.id, expected_version=source.version)
        self.assertEqual(raised.exception.code, "run_superseded")
        replay = runs.restart_fresh(source.id, expected_version=source.version, request_id="fresh-one")
        self.assertEqual(replay.id, fresh.id)

    def test_task_fresh_rejects_mutated_candidate_instead_of_silent_substitution(self):
        producer, runs, executor, tasks = self.make()
        result = tasks.create("topic", request_id="fresh-digest", tasks=[dict(title="One")])
        self.finish(producer, executor, result)
        source = runs.get(result["items"][0]["run_id"])
        (self.catalog.locate(self.base["id"]) / "scripts/method.py").write_text("changed", encoding="utf-8")
        with self.assertRaises(ContentRunError) as raised:
            runs.restart_fresh(source.id, expected_version=source.version, request_id="reject-drift")
        self.assertEqual(raised.exception.code, "invalid_skill")
        self.assertEqual(tasks.list("topic")["items"][0]["run_id"], source.id)

    def test_same_task_fresh_waits_for_worker_exit_while_sibling_can_run(self):
        producer = CleanupTaskProducer()
        runs = ContentRunService(self.db, producer_factory=lambda: producer,
            output_root=self.root / "cleanup-output", production_protocol="legacy")
        executor = ManagedRunExecutor(runs, max_concurrency=2, shutdown_timeout=3)
        self.addCleanup(executor.shutdown)
        self.addCleanup(producer.cleanup_release.set)
        tasks = ProductionTaskService(self.db, runs, executor)
        result = tasks.create("topic", request_id="cleanup-old", tasks=[dict(title="Old")])
        self.assertTrue(producer.old_started.wait(5))
        source = runs.get(result["items"][0]["run_id"])
        runs.interrupt_owner(source.id, owner_id=source.lease_owner)
        producer.old_cancel.set()
        self.assertTrue(producer.cleanup_started.wait(5))
        interrupted = runs.get(source.id)
        fresh = runs.restart_fresh(source.id, expected_version=interrupted.version, request_id="cleanup-fresh")
        executor.submit(fresh.id, expected_version=fresh.version)
        sibling = tasks.create("topic", request_id="cleanup-sibling", tasks=[dict(title="Sibling")])
        sibling_id = sibling["items"][0]["run_id"]
        wait(lambda: executor.queue_snapshot(sibling_id)["status"] == "succeeded", timeout=10)
        self.assertEqual(executor.queue_snapshot(fresh.id)["status"], "pending")
        self.assertEqual(runs.repository.list_attempts(runs.get_active_revision(fresh.id).id), ())
        producer.cleanup_release.set()
        wait(lambda: executor.queue_snapshot(fresh.id)["status"] == "succeeded", timeout=10)
        self.assertEqual(runs.get(source.id).status.value, "interrupted")
        self.assertEqual(runs.get(fresh.id).task_id, source.task_id)

    def test_default_pair_frozen_before_claim_and_source_edits_do_not_change_it(self):
        import json
        from uuid import uuid4
        runs = ContentRunService(self.db, output_root=self.root / "pair-output", production_protocol="native-v1")
        with runs._write_lock, self.db.session() as session:
            task = ProductionTask(id=str(uuid4()), topic_id="pair-topic", title="Pair", brief=None,
                position=0, create_request_id="pair-freeze")
            session.add(task)
            session.flush()
            run = runs._create("pair-topic", task_id=task.id, bound_session=session)
        frozen = runs.output_root / "creator" / "pair" / run.id / "skill-snapshot"
        recorded = json.loads((frozen / "pair.json").read_text(encoding="utf-8"))
        self.assertEqual(_digest(frozen / "skills/mind"), self.mind["digest"])
        self.assertEqual(_digest(frozen / "skills/production"), self.visual["digest"])
        (self.catalog.locate(self.mind["id"]) / "scripts/method.py").write_text("changed", encoding="utf-8")
        prepared = runs._begin_attempt(run.id, owner_id="pair-test")
        self.assertEqual(prepared["input"].composition.model_dump(mode="json"), recorded)
        self.assertEqual(prepared["input"].composition.mind.digest, self.mind["digest"])
        with self.db.session() as session:
            self.assertEqual(session.get(Topic, "pair-topic").status.value, "queued")

    def test_account_transfer_rejects_old_task_projection_and_collection_reuse(self):
        from creatoros.storage import CreatorPlatform
        producer, runs, executor, tasks = self.make()
        result = tasks.create("topic", request_id="before-transfer", tasks=[dict(title="One")])
        self.finish(producer, executor, result)
        self.repo.create_creator(creator_id="new-creator", display_name="Other", platform=CreatorPlatform.XIAOHONGSHU)
        with self.db.session() as session:
            session.get(Series, "single").creator_id = "new-creator"
        before = self.counts()
        with self.assertRaises(ContentRunError) as raised:
            tasks.list("topic")
        self.assertEqual(raised.exception.status_code, 403)
        with self.assertRaises(ContentRunError) as raised:
            tasks.create("topic", request_id="after-transfer", tasks=[dict(title="Two")])
        self.assertEqual(raised.exception.status_code, 403)
        source = runs.get(result["items"][0]["run_id"])
        with self.assertRaises(ContentRunError) as raised:
            runs.restart_fresh(source.id, expected_version=source.version, request_id="transfer-fresh")
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(self.counts(), before)

    def test_upgrade_downgrade_upgrade_preserves_legacy_rows_and_null_scope(self):
        legacy = self.runs.create("topic")
        config = Config("alembic.ini")
        config.attributes["database_url"] = self.url
        command.downgrade(config, "20261024_0019")
        with self.db.engine.connect() as connection:
            before = dict(connection.execute(text("SELECT * FROM content_runs WHERE id=:id"), {"id": legacy.id}).mappings().one())
            topic_before = dict(connection.execute(text("SELECT * FROM topics WHERE id='topic'")).mappings().one())
        upgrade_database(self.url)
        with self.db.engine.connect() as connection:
            after = dict(connection.execute(text("SELECT * FROM content_runs WHERE id=:id"), {"id": legacy.id}).mappings().one())
            self.assertIsNone(after.pop("task_id"))
            self.assertEqual(after, before)
            self.assertEqual(dict(connection.execute(text("SELECT * FROM topics WHERE id='topic'")).mappings().one()), topic_before)
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
            self.assertEqual(compare_metadata(MigrationContext.configure(connection), Base.metadata), [])

    def test_migration_metadata_and_task_collection_foreign_keys(self):
        with self.db.engine.connect() as connection:
            self.assertEqual(compare_metadata(MigrationContext.configure(connection), Base.metadata), [])
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
        producer, runs, executor, tasks = self.make()
        result = tasks.create("topic", request_id="foreign-keys", tasks=[dict(title="One")])
        self.finish(producer, executor, result)
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError), self.db.session() as session:
            session.delete(session.get(MediaCollection, result["collection_id"]))
        config = Config("alembic.ini")
        config.attributes["database_url"] = self.url
        with self.assertRaisesRegex(RuntimeError, "Production tasks exist"):
            command.downgrade(config, "20261024_0019")


if __name__ == "__main__":
    unittest.main()
