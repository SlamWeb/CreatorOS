"""Initial submission contracts on isolated SQLite with a held dispatcher.

No model, production server or shared database is used.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
import unittest
from unittest.mock import patch

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from creatoros.runs.production_tasks import ProductionTaskService
from creatoros.storage import ContentAttempt, ContentRepository, ContentRun, ProductionTask, WriteReceipt
from creatoros.storage.models import ProductionQueueItem
from tests import test_production_tasks as tasks_fixture
from tests import test_reference_production_service as reference_fixture


@contextmanager
def reject_receipt_commit(database, request_id):
    """Cause a real SQLite commit failure after the requested rows are flushed."""
    factory = database._session_factory
    seen = []

    def session_factory():
        session = factory()
        commit = session.commit

        def failing_commit():
            if session.get(WriteReceipt, request_id) is not None:
                session.flush()
                seen.append((session.scalar(select(func.count()).select_from(ContentRun)),
                             session.scalar(select(func.count()).select_from(ProductionQueueItem))))
                # Defer an invalid FK until the actual DBAPI COMMIT. It must
                # roll back the entire transaction, including valid records.
                run_id = session.scalar(select(ContentRun.id).limit(1))
                session.execute(text("PRAGMA defer_foreign_keys = ON"))
                session.add(ProductionQueueItem(run_id=run_id, revision_id="missing-revision-for-test",
                                                expected_version=1, status="pending"))
                session.flush()
            return commit()

        session.commit = failing_commit
        return session

    with patch.object(database, "_session_factory", side_effect=session_factory):
        yield seen


class ReferenceSubmissionContractTests(unittest.TestCase):
    setUp = reference_fixture.ReferenceProductionServiceTests.setUp
    tearDown = reference_fixture.ReferenceProductionServiceTests.tearDown
    service = reference_fixture.ReferenceProductionServiceTests.service
    counts = reference_fixture.ReferenceProductionServiceTests.counts
    request = reference_fixture.ReferenceProductionServiceTests.request
    recorded_rows = reference_fixture.ReferenceProductionServiceTests.recorded_rows

    def test_commit_failure_rolls_back_and_retry_wakes_after_visibility(self):
        body = self.request("reference-commit-failure")
        executor = self.app.state.executor
        before = self.recorded_rows()
        with patch.object(executor, "_consume", side_effect=lambda: executor._stop.wait()):
            executor.start()
            with patch.object(executor._wake, "set", wraps=executor._wake.set) as wake:
                with reject_receipt_commit(self.database, body.request_id) as flushed:
                    with self.assertRaisesRegex(IntegrityError, "FOREIGN KEY constraint failed"):
                        self.direct.submit("series", body, creator_id="creator")
                wake.assert_not_called()
            self.assertEqual(len(flushed), 1)
            self.assertGreaterEqual(flushed[0][0], 1)
            self.assertGreaterEqual(flushed[0][1], 1)
            self.assertEqual(self.recorded_rows(), before)
            self.assertFalse(self.producer.started.is_set())

            original_wake = executor._wake.set
            observed = []
            def on_wake():
                with self.database.session() as session:
                    receipt = session.get(WriteReceipt, body.request_id)
                    run_id = receipt.response_json["reference_production"]["run_id"]
                    queue = session.scalar(select(ProductionQueueItem).where(ProductionQueueItem.run_id == run_id))
                    observed.append((run_id, queue.status))
                original_wake()
            with patch.object(executor._wake, "set", side_effect=on_wake):
                run = self.direct.submit("series", body, creator_id="creator")
            self.assertEqual(observed, [(run.id, "pending")])

    def test_old_receipt_replays_pending_running_and_attention_without_reset(self):
        self.library.add("creator", "knowledge-to-carousel", "old-queue-default")
        self.library.set_series("series", self.pool["version"], ["knowledge-to-carousel"],
            {"image": [{"skill_id": "knowledge-to-carousel", "role": "primary"}], "video": []})
        body = self.request("reference-old-queue-states").model_copy(update={"skill_ids": None})
        executor = self.app.state.executor
        with patch.object(executor, "_consume", side_effect=lambda: executor._stop.wait()):
            run = self.direct.submit("series", body, creator_id="creator")
            with self.database.session() as session:
                receipt = session.get(WriteReceipt, body.request_id)
                old_value = dict(receipt.response_json)
                old_value.pop("reference_production")
                receipt.response_json = old_value
            queue_id = executor.queue_snapshot(run.id)["id"]
            for state in ("pending", "running", "needs_attention"):
                if state != "pending":
                    executor.queue.transition(queue_id, state, "unknown outcome" if state == "needs_attention" else None)
                before = self.recorded_rows()
                self.assertEqual(self.direct.submit("series", body, creator_id="creator").id, run.id)
                self.assertEqual(self.recorded_rows(), before)
                self.assertEqual(executor.queue_snapshot(run.id)["status"], state)
            with self.database.session() as session:
                self.assertEqual(list(session.scalars(select(ContentAttempt))), [])
            self.assertFalse(self.producer.started.is_set())

    def test_concurrent_reference_and_task_submissions_share_the_transaction_boundary(self):
        body = self.request("mixed-concurrent-reference")
        repo = ContentRepository(self.database)
        repo.create_series(series_id="task-series", creator_id="creator", name="Task", description="", audience="",
                           skill_name="knowledge-to-carousel")
        repo.add_topic(topic_id="task-topic", series_id="task-series", title="Task", source="manual")
        executor = self.app.state.executor
        tasks = ProductionTaskService(self.database, self.runs, executor)
        barrier = Barrier(4)
        def submit(index):
            barrier.wait(timeout=10)
            if index % 2:
                return ("reference", self.direct.submit("series", body, creator_id="creator").id)
            result = tasks.create("task-topic", request_id="mixed-concurrent-tasks", tasks=[dict(title="One")])
            return ("task", result["items"][0]["run_id"])
        with patch.object(executor, "_consume", side_effect=lambda: executor._stop.wait()):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(submit, range(4)))
            for kind in ("reference", "task"):
                self.assertEqual(len({identifier for name, identifier in results if name == kind}), 1)
            with self.database.session() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(ContentRun)), 2)
                self.assertEqual(session.scalar(select(func.count()).select_from(ProductionQueueItem)), 2)
                self.assertEqual(session.scalar(select(func.count()).select_from(ProductionTask)), 1)
                self.assertEqual(session.scalar(select(func.count()).select_from(WriteReceipt).where(
                    WriteReceipt.request_id.in_([body.request_id, "mixed-concurrent-tasks"]))), 2)
            self.assertFalse(self.producer.started.is_set())


class TaskSubmissionContractTests(unittest.TestCase):
    setUp = tasks_fixture.ProductionTaskTests.setUp
    skill = tasks_fixture.ProductionTaskTests.skill
    make = tasks_fixture.ProductionTaskTests.make
    counts = tasks_fixture.ProductionTaskTests.counts

    def test_commit_failure_rolls_back_all_rows_and_retry_wakes_after_visibility(self):
        producer, runs, executor, tasks = self.make(concurrency=1)
        before = self.counts()
        request_id = "task-commit-failure"
        with patch.object(executor, "_consume", side_effect=lambda: executor._stop.wait()):
            executor.start()
            with patch.object(executor._wake, "set", wraps=executor._wake.set) as wake:
                with reject_receipt_commit(self.db, request_id) as flushed:
                    with self.assertRaisesRegex(IntegrityError, "FOREIGN KEY constraint failed"):
                        tasks.create("topic", request_id=request_id, tasks=[dict(title="One"), dict(title="Two")])
                wake.assert_not_called()
            self.assertEqual(flushed, [(2, 2)])
            self.assertEqual(self.counts(), before)
            self.assertEqual(producer.requests, [])

            original_wake = executor._wake.set
            observed = []
            def on_wake():
                with self.db.session() as session:
                    receipt = session.get(WriteReceipt, request_id)
                    task_ids = receipt.response_json["task_ids"]
                    runs = list(session.scalars(select(ContentRun).where(ContentRun.task_id.in_(task_ids))))
                    queues = list(session.scalars(select(ProductionQueueItem).where(
                        ProductionQueueItem.run_id.in_([run.id for run in runs]))))
                    observed.append((len(task_ids), len(runs), [item.status for item in queues]))
                original_wake()
            with patch.object(executor._wake, "set", side_effect=on_wake):
                result = tasks.create("topic", request_id=request_id, tasks=[dict(title="One"), dict(title="Two")])
            self.assertEqual(len(result["items"]), 2)
            self.assertEqual(observed, [(2, 2, ["pending", "pending"])])

    def test_task_replay_does_not_reauthorize_needs_attention(self):
        producer, runs, executor, tasks = self.make(concurrency=1)
        payload = [dict(title="One")]
        with patch.object(executor, "_consume", side_effect=lambda: executor._stop.wait()):
            result = tasks.create("topic", request_id="task-replay-attention", tasks=payload)
            run_id = result["items"][0]["run_id"]
            item_id = executor.queue_snapshot(run_id)["id"]
            executor.queue.transition(item_id, "running")
            executor.queue.recover()
            original = executor.queue_snapshot(run_id)
            self.assertEqual(original["status"], "needs_attention")
            before = self.counts()
            replay = tasks.create("topic", request_id="task-replay-attention", tasks=payload)
            self.assertEqual(replay["items"][0]["run_id"], run_id)
            self.assertEqual(executor.queue_snapshot(run_id), original)
            self.assertEqual(self.counts(), before)
            self.assertEqual(producer.requests, [])
