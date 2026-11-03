"""Queue receipt recovery on an isolated SQLite database; no real producer."""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from pathlib import Path
import traceback
from unittest import TestCase

from sqlalchemy import event, text
from alembic import command
from tests.test_topic_edit_migration import config
from creatoros.runs import ContentRunService
from creatoros.runs.queue import ProductionQueue
from creatoros.storage import Database
from creatoros.tools.model_projection import _run
from creatoros.storage.models import ContentRevision, ContentRun
from tests import test_production_queue as fixtures


def _spawn_enqueue(url, output_root, run_id, version, ready, start, results):
    """Windows spawn entrypoint: independent database, no executor or producer."""
    database = Database(url)
    try:
        service = ContentRunService(database, producer_factory=lambda: None,
                                    output_root=Path(output_root), production_protocol="legacy")
        ready.put(True)
        if not start.wait(20):
            raise TimeoutError("spawn barrier timed out")
        item = ProductionQueue(service).enqueue(run_id, version)
        results.put(("ok", item.id, item.status))
    except BaseException:
        results.put(("error", traceback.format_exc()))
    finally:
        database.close()


class QueueHistoryTests(TestCase):
    setUp = fixtures.ProductionQueueTests.setUp
    tearDown = fixtures.ProductionQueueTests.tearDown
    service = fixtures.ProductionQueueTests.service
    executor = fixtures.ProductionQueueTests.executor

    def failed_receipt(self):
        producer = fixtures.Producer()
        service = self.service(producer)
        run = service.create("topic-0")
        queue = ProductionQueue(service)
        item = queue.enqueue(run.id, run.version)
        queue.transition(item.id, "running")
        queue.transition(item.id, "needs_attention", "original dispatch failure")
        return producer, service, run, queue

    def assert_history(self, queue, run):
        snapshot = queue.snapshot(run.id)
        self.assertEqual(len(snapshot["history"]), 1)
        history = snapshot["history"][0]
        self.assertEqual(history["status"], "needs_attention")
        self.assertEqual(history["last_error"], "original dispatch failure")
        self.assertTrue(history["submitted_at"] and history["started_at"] and history["completed_at"])
        return snapshot

    def test_bad_revision_and_missing_frozen_skill_keep_original_receipt(self):
        for corrupt in ("revision", "skill"):
            with self.subTest(corrupt=corrupt):
                if corrupt == "skill":
                    # Different topic avoids reusing the prior run.
                    producer = fixtures.Producer()
                    service = self.service(producer)
                    run = service.create("topic-1")
                    queue = ProductionQueue(service)
                    item = queue.enqueue(run.id, run.version)
                    queue.transition(item.id, "running")
                    queue.transition(item.id, "needs_attention", "original dispatch failure")
                else:
                    producer, service, run, queue = self.failed_receipt()
                revision = service.get_active_revision(run.id)
                with self.database.session() as session:
                    row = session.get(ContentRevision, revision.id)
                    data = dict(row.production_input_json)
                    if corrupt == "revision":
                        data.pop("creator_id")
                    else:
                        data["skill_snapshot_frozen"] = True
                    row.production_input_json = data
                if corrupt == "skill":
                    frozen = self.root / "outputs" / "creator" / "series" / run.id / "skill-snapshot"
                    if frozen.exists():
                        frozen.rename(frozen.with_name("saved-skill-snapshot"))
                executor = self.executor(service)
                executor.submit(run.id, expected_version=run.version)
                fixtures.wait(lambda: executor.queue_snapshot(run.id)["status"] not in {"pending", "running"}, timeout=15)
                snapshot = self.assert_history(queue, run)
                self.assertEqual(snapshot["status"], "needs_attention")
                self.assertIn("creator_id" if corrupt == "revision" else "冻结文件缺失", snapshot["last_error"])
                self.assertTrue(all("history" not in row for row in queue.snapshot()))
                self.assertEqual(producer.calls, [])
                self.assertFalse(service.repository.list_attempts(revision.id))
                executor.shutdown()

    def test_successful_and_duplicate_reauthorization(self):
        producer, service, run, queue = self.failed_receipt()
        with ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(lambda _: ProductionQueue(self.service(producer)).enqueue(run.id, run.version), range(8)))
        self.assertEqual(len({item.id for item in items}), 1)
        self.assertEqual(self.assert_history(queue, run)["status"], "pending")
        executor = self.executor(service)
        executor.start()
        fixtures.wait(lambda: not executor.is_submitted(run.id))
        self.assertEqual(producer.calls, ["topic-0"])
        self.assertEqual(self.assert_history(queue, run)["status"], "succeeded")

    def test_windows_spawn_concurrent_reauthorization(self):
        producer, service, run, queue = self.failed_receipt()
        ctx = multiprocessing.get_context("spawn")
        ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
        children = [ctx.Process(target=_spawn_enqueue,
                    args=(self.url, str(self.root / "outputs"), run.id, run.version, ready, start, results))
                    for _ in range(4)]
        try:
            for child in children:
                child.start()
            for _ in children:
                self.assertTrue(ready.get(timeout=30))
            start.set()
            received = [results.get(timeout=30) for _ in children]
            for child in children:
                child.join(timeout=30)
                self.assertEqual(child.exitcode, 0)
            self.assertTrue(all(row[0] == "ok" and row[2] == "pending" for row in received), received)
            self.assertEqual(len({row[1] for row in received}), 1)
            self.assertEqual(self.assert_history(queue, run)["status"], "pending")
            with self.database.engine.connect() as conn:
                self.assertEqual(conn.scalar(text("SELECT count(*) FROM production_queue_items")), 1)
                self.assertEqual(conn.scalar(text("SELECT count(*) FROM production_queue_history")), 1)
                self.assertEqual(conn.scalar(text("SELECT count(*) FROM content_attempts")), 0)
            self.assertEqual(producer.calls, [])
        finally:
            start.set()
            for child in children:
                if child.is_alive():
                    child.terminate()
                if child.pid is not None:
                    child.join(timeout=5)
            ready.close()
            results.close()

    def test_bound_session_outer_failure_rolls_back_history_and_queue(self):
        producer, service, run, queue = self.failed_receipt()
        before = queue.snapshot(run.id)
        with self.assertRaisesRegex(RuntimeError, "outer failure"):
            with self.database.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                queue.enqueue(run.id, run.version, bound_session=session)
                self.assertEqual(session.scalar(text("SELECT count(*) FROM production_queue_history")), 1)
                self.assertEqual(session.scalar(text("SELECT status FROM production_queue_items")), "pending")
                raise RuntimeError("outer failure")
        self.assertEqual(queue.snapshot(run.id), before)
        self.assertEqual(producer.calls, [])

    def test_is_submitted_does_not_read_history(self):
        producer, service, run, queue = self.failed_receipt()
        queue.enqueue(run.id, run.version)
        executor = self.executor(service)
        statements = []
        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.lower())
        event.listen(self.database.engine, "before_cursor_execute", capture)
        try:
            self.assertTrue(executor.is_submitted(run.id))
        finally:
            event.remove(self.database.engine, "before_cursor_execute", capture)
        self.assertFalse(any("production_queue_history" in sql for sql in statements), statements)
        self.assertEqual(self.assert_history(queue, run)["status"], "pending")
        self.assertEqual(producer.calls, [])

    def test_incomplete_run_snapshot_rejects_without_changing_receipt(self):
        producer, service, run, queue = self.failed_receipt()
        before = queue.snapshot(run.id)
        with self.database.session() as session:
            row = session.get(ContentRun, run.id)
            data = dict(row.input_snapshot_json)
            data.pop("creator_id")
            row.input_snapshot_json = data
        current = service.get(run.id)
        with self.assertRaisesRegex(Exception, "历史任务输入不完整"):
            queue.enqueue(run.id, current.version)
        self.assertEqual(queue.snapshot(run.id), before)
        self.assertEqual(producer.calls, [])

    def test_repeated_failed_dispatches_each_keep_their_own_receipt(self):
        producer, service, run, queue = self.failed_receipt()
        item = queue.enqueue(run.id, run.version)
        queue.transition(item.id, "failed", "second dispatch failure")
        queue.enqueue(run.id, run.version)
        history = queue.snapshot(run.id)["history"]
        self.assertEqual([(row["status"], row["last_error"]) for row in history],
                         [("needs_attention", "original dispatch failure"), ("failed", "second dispatch failure")])
        self.assertEqual(producer.calls, [])

    def test_migration_refuses_to_discard_saved_history(self):
        command.downgrade(config(self.url), "20261103_0026")
        _, service, run, queue = self.failed_receipt()
        with self.database.engine.connect() as conn:
            old_rows = conn.execute(text("SELECT * FROM production_queue_items ORDER BY id")).all()
        command.upgrade(config(self.url), "head")
        with self.database.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT * FROM production_queue_items ORDER BY id")).all(), old_rows)
        queue.enqueue(run.id, run.version)
        with self.assertRaisesRegex(RuntimeError, "队列历史证据"):
            command.downgrade(config(self.url), "20261103_0026")
        self.assertEqual(self.assert_history(queue, run)["status"], "pending")

    def test_model_projection_omits_unbounded_history(self):
        raw = {"id": "run", "production_queue": {"status": "needs_attention",
               "history": [{"last_error": "private old error"}]}}
        projected = _run(raw)
        self.assertEqual(projected["production_queue"], {"status": "needs_attention", "history_count": 1})
        self.assertEqual(raw["production_queue"]["history"][0]["last_error"], "private old error")

    def test_history_insert_failure_rolls_back_queue_reset(self):
        producer, service, run, queue = self.failed_receipt()
        before = queue.snapshot(run.id)
        with self.database.engine.begin() as conn:
            conn.execute(text("CREATE TRIGGER fail_queue_history BEFORE INSERT ON production_queue_history BEGIN SELECT RAISE(ABORT, 'injected history failure'); END"))
        with self.assertRaises(Exception):
            queue.enqueue(run.id, run.version)
        self.assertEqual(queue.snapshot(run.id), before)
        self.assertEqual(producer.calls, [])
