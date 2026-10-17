"""Durability/crash fault injection: isolated SQLite, local artifacts, no paid models."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack
from creatoros.integrations.codex import CodexUsage, ProducedPack, ProductionSession
from creatoros.runs import ContentRunService, ManagedRunExecutor
from creatoros.runs.ownership import ExecutionOwnershipError
from creatoros.runs.queue import ProductionQueue
from creatoros.storage import ContentRepository, CreatorPlatform, Database, TopicSource, upgrade_database
from creatoros.storage.models import Base


def wait(predicate, timeout=5):
    end = monotonic() + timeout
    while monotonic() < end:
        if predicate():
            return
        sleep(.02)
    raise AssertionError("queue condition timed out")


class Producer:
    def __init__(self, *, block=False, fail_first=False):
        self.calls = []
        self.block, self.fail_first = block, fail_first
        self.started, self.release = Event(), Event()

    def produce_to(self, **request):
        self.calls.append(request["topic_id"])
        request["on_thread_started"](f"thread-{len(self.calls)}")
        self.started.set()
        if self.block and len(self.calls) == 1:
            while not self.release.wait(.02):
                if request["cancel_event"].is_set():
                    break
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("isolated injected producer failure")
        directory = Path(request["directory"])
        directory.mkdir(parents=True)
        (directory / "images").mkdir()
        Image.new("RGB", (32, 48), "white").save(directory / "images/01.png")
        pack = SocialContentPack(pack_id=request["pack_id"], creator_id=request["creator_id"],
            series_id=request["series_id"], topic_id=request["topic_id"], topic_title=request["topic_title"],
            skill_name="knowledge-to-carousel", generated_at="2026-10-17T00:00:00+00:00",
            content_summary="Local queue fault fixture", cards=[CarouselCard(order=1, kind="cover",
                headline="Local", image_path="images/01.png")],
            publish_copy=PublicationCopy(title="Local", body="Fixture"))
        session = ProductionSession(thread_id=f"thread-{len(self.calls)}", pack_id=request["pack_id"],
            created_at="2026-10-17T00:00:00+00:00", usage=CodexUsage(input_tokens=0, output_tokens=0))
        (directory / "social_content_pack.json").write_text(pack.model_dump_json(), encoding="utf-8")
        (directory / "production_session.json").write_text(session.model_dump_json(), encoding="utf-8")
        return ProducedPack(directory=directory, pack=pack, session=session)


class ProductionQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.url = f"sqlite:///{(self.root / 'test.db').as_posix()}"
        upgrade_database(self.url)
        self.database = Database(self.url)
        repo = ContentRepository(self.database)
        repo.create_creator(creator_id="creator", display_name="Queue fixture", platform=CreatorPlatform.XIAOHONGSHU)
        repo.create_series(series_id="series", creator_id="creator", name="Queue", description="Local",
                           audience="test", skill_name="knowledge-to-carousel")
        for index in range(4):
            repo.add_topic(topic_id=f"topic-{index}", series_id="series", title=f"Topic {index}", source=TopicSource.MANUAL)
        self.executors = []

    def tearDown(self):
        for executor in reversed(self.executors):
            executor.shutdown()
        self.database.close()
        self.tmp.cleanup()

    def service(self, producer):
        return ContentRunService(self.database, producer_factory=lambda: producer,
                                 output_root=self.root / "outputs", production_protocol="legacy")

    def executor(self, service):
        executor = ManagedRunExecutor(service)
        self.executors.append(executor)
        return executor

    def test_migration_matches_metadata_and_never_enqueues_legacy_runs(self):
        with self.database.engine.connect() as connection:
            self.assertEqual(compare_metadata(MigrationContext.configure(connection), Base.metadata), [])
        producer = Producer()
        service = self.service(producer)
        run = service.create("topic-0")
        executor = self.executor(service)
        executor.start()
        self.assertIsNone(executor.queue_snapshot(run.id))
        self.assertEqual(producer.calls, [])
        self.assertEqual(service.get(run.id).status.value, "queued")

    def test_serial_failure_isolation_and_response_loss_idempotency(self):
        producer = Producer(block=True, fail_first=True)
        service, runs = self.service(producer), []
        executor = self.executor(service)
        for index in range(3):
            runs.append(service.create(f"topic-{index}"))
            executor.submit(runs[-1].id, expected_version=runs[-1].version)
        self.assertTrue(producer.started.wait(2))
        with ThreadPoolExecutor(max_workers=4) as clients:
            list(clients.map(lambda _: executor.submit(runs[0].id, expected_version=runs[0].version), range(8)))
        self.assertEqual(service.get(runs[1].id).status.value, "queued")
        self.assertIsNone(service.get(runs[1].id).lease_owner)
        self.assertEqual(executor.queue_snapshot(runs[1].id)["position"], 1)
        producer.release.set()
        wait(lambda: all(not executor.is_submitted(run.id) for run in runs))
        self.assertEqual(producer.calls, ["topic-0", "topic-1", "topic-2"])
        self.assertEqual(executor.queue_snapshot(runs[0].id)["status"], "failed")
        self.assertEqual([service.get(run.id).status.value for run in runs[1:]], ["awaiting_approval"] * 2)
        self.assertEqual(len(service.repository.list_attempts(service.get_active_revision(runs[0].id).id)), 1)

    def test_pending_survives_shutdown_and_inflight_never_auto_retries(self):
        producer = Producer(block=True)
        service = self.service(producer)
        first, second, untouched = [service.create(f"topic-{index}") for index in range(3)]
        executor = self.executor(service)
        executor.submit(first.id, expected_version=first.version)
        self.assertTrue(producer.started.wait(2))
        executor.submit(second.id, expected_version=second.version)
        executor.shutdown()
        self.assertEqual(service.get(first.id).status.value, "interrupted")
        self.assertEqual(executor.queue_snapshot(second.id)["status"], "pending")
        self.database.close()
        self.database = Database(self.url)
        restarted_service = self.service(producer)
        restarted = self.executor(restarted_service)
        restarted.start()
        wait(lambda: not restarted.is_submitted(second.id))
        self.assertEqual(producer.calls, ["topic-0", "topic-1"])
        self.assertEqual(restarted_service.get(untouched.id).status.value, "queued")
        self.assertEqual(restarted.queue_snapshot(first.id)["status"], "needs_attention")

    def test_crash_running_marker_is_reconciled_not_reexecuted(self):
        producer, service = Producer(), self.service(Producer())
        run = service.create("topic-0")
        queue = ProductionQueue(service)
        item = queue.enqueue(run.id, run.version)
        queue.transition(item.id, "running")  # Crash boundary before claim; no model ran.
        restarted = self.executor(self.service(producer))
        restarted.start()
        self.assertEqual(restarted.queue_snapshot(run.id)["status"], "needs_attention")
        self.assertEqual(producer.calls, [])

    def test_pending_cancel_is_not_produced(self):
        producer = Producer(block=True)
        service = self.service(producer)
        first, second = [service.create(f"topic-{index}") for index in range(2)]
        executor = self.executor(service)
        executor.submit(first.id, expected_version=first.version)
        self.assertTrue(producer.started.wait(2))
        executor.submit(second.id, expected_version=second.version)
        executor.cancel(second.id, expected_version=second.version)
        producer.release.set()
        wait(lambda: not executor.is_submitted(first.id))
        self.assertEqual(executor.queue_snapshot(second.id)["status"], "cancelled")
        self.assertEqual(producer.calls, ["topic-0"])

    def test_claimed_crash_requires_explicit_recovery(self):
        producer = Producer()
        service = self.service(producer)
        run = service.create("topic-0")
        queue = ProductionQueue(service)
        item = queue.enqueue(run.id, run.version)
        queue.transition(item.id, "running")
        with service.guard:
            service.claim(run.id, owner_id="dead-fixture", expected_version=run.version)
            # Test crash: no external process exists; simulate verified process cleanup.
            service.guard.finish("dead-fixture")
        restarted = self.executor(service)
        restarted.start()
        self.assertEqual(service.get(run.id).status.value, "interrupted")
        self.assertEqual(restarted.queue_snapshot(run.id)["status"], "needs_attention")
        self.assertEqual(producer.calls, [])
        restarted.submit(run.id, expected_version=service.get(run.id).version)
        wait(lambda: not restarted.is_submitted(run.id))
        self.assertEqual(producer.calls, ["topic-0"])

    def test_completed_artifact_crash_revalidates_without_producer(self):
        producer = Producer()
        service = self.service(producer)
        run = service.create("topic-0")
        queue = ProductionQueue(service)
        item = queue.enqueue(run.id, run.version)
        queue.transition(item.id, "running")
        with service.guard:
            prepared = service.claim(run.id, owner_id="dead-fixture", expected_version=run.version)
            produced = producer.produce_to(directory=prepared["directory"], pack_id="local",
                creator_id="creator", series_id="series", topic_id="topic-0", topic_title="Local",
                on_thread_started=lambda _: None)
            service._mark_produced(run.id, prepared["attempt_id"], produced, 0, owner_id="dead-fixture")
            service.guard.finish("dead-fixture")
        restarted = self.executor(service)
        restarted.start()
        self.assertEqual(service.get(run.id).status.value, "awaiting_approval")
        self.assertEqual(restarted.queue_snapshot(run.id)["status"], "succeeded")
        self.assertEqual(producer.calls, ["topic-0"])

    def test_dirty_journal_blocks_all_dispatch(self):
        producer = Producer()
        service = self.service(producer)
        run = service.create("topic-0")
        ProductionQueue(service).enqueue(run.id, run.version)
        with service.guard:
            service.guard.begin(owner_id="unverified", run_id=run.id, attempt_id="test")
        executor = self.executor(service)
        with self.assertRaises(ExecutionOwnershipError):
            executor.start()
        self.assertEqual(producer.calls, [])
        self.assertEqual(executor.queue_snapshot(run.id)["status"], "pending")
        self.assertIsNotNone(executor.queue_snapshot(run.id)["blocked_reason"])
        with self.assertRaises(ExecutionOwnershipError):
            executor.submit(run.id, expected_version=run.version)
        with service.guard:
            service.guard.finish("unverified")  # Fixture spawned no external process.

    def test_ownership_pause_keeps_pending_visible_and_rejects_new_submissions(self):
        producer = Producer(block=True)
        service = self.service(producer)
        first, second, third = [service.create(f"topic-{index}") for index in range(3)]
        executor = self.executor(service)
        executor.submit(first.id, expected_version=first.version)
        self.assertTrue(producer.started.wait(2))
        executor.submit(second.id, expected_version=second.version)
        original = service.guard.assert_clean
        def pause_after_first():
            if producer.calls and not service.guard.journal.exists():
                raise ExecutionOwnershipError("injected unverified execution")
            original()
        with patch.object(service.guard, "assert_clean", pause_after_first):
            producer.release.set()
            wait(lambda: executor.queue_snapshot(second.id)["blocked_reason"] is not None)
            self.assertEqual(executor.queue_snapshot(second.id)["status"], "pending")
            self.assertIn("unverified", ProductionQueue(service).snapshot(second.id)["blocked_reason"])
            with self.assertRaises(ExecutionOwnershipError):
                executor.submit(third.id, expected_version=third.version)
            self.assertIsNone(executor.queue_snapshot(third.id))
        self.assertEqual(producer.calls, ["topic-0"])
        executor.shutdown()
        restarted = self.executor(service)
        restarted.start()
        wait(lambda: not restarted.is_submitted(second.id))
        self.assertEqual(producer.calls, ["topic-0", "topic-1"])
        self.assertIsNone(restarted.queue_snapshot(second.id)["blocked_reason"])

    def test_failed_error_persistence_pauses_instead_of_silent_dead_consumer(self):
        producer = Producer(block=True)
        service = self.service(producer)
        first, second = [service.create(f"topic-{index}") for index in range(2)]
        executor = self.executor(service)
        executor.submit(first.id, expected_version=first.version)
        self.assertTrue(producer.started.wait(2))
        executor.submit(second.id, expected_version=second.version)
        # Failure injection specifically targets outcome persistence, not model execution.
        with patch.object(executor.queue, "finish", side_effect=OSError("injected disk error")), \
             patch.object(executor.queue, "transition", side_effect=OSError("injected disk error")):
            producer.release.set()
            wait(lambda: executor.queue_snapshot(second.id)["blocked_reason"] is not None)
            with self.assertRaises(ExecutionOwnershipError):
                executor.submit(second.id, expected_version=second.version)
        self.assertEqual(producer.calls, ["topic-0"])
        self.assertEqual(executor.queue_snapshot(second.id)["status"], "pending")


if __name__ == "__main__":
    unittest.main()
