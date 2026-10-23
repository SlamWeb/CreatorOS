"""Parallel scheduling/crash isolation using real SQLite and local fault injection.

No model/network calls: Events hold local producers at controlled boundaries so
overlap, cancellation, persistence, and artifact ownership can be asserted.
"""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from time import monotonic, sleep
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack
from creatoros.integrations.codex import CodexUsage, ProducedPack, ProductionSession
from creatoros.runs import ContentRunService, ManagedRunExecutor
from creatoros.runs.ownership import ExecutionOwnershipError
from creatoros.runs.service import ContentRunError
from creatoros.runs.ownership import ExecutionOwnershipError, LocalExecutionGuard
from creatoros.storage import (ContentRepository, ContentRun, ContentRunStatus, ContentRevision,
    CreatorPlatform, Database, TopicSource, upgrade_database)


def wait(predicate, timeout=8):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(.02)
    raise AssertionError("parallel production condition timed out")


class BlockingProducer:
    def __init__(self, *, blocking=True, fail_topics=()):
        self.blocking, self.fail_topics = blocking, set(fail_topics)
        self.lock = Lock()
        self.calls, self.active, self.cancelled = [], set(), set()
        self.max_active, self.active_by_topic, self.max_by_topic = 0, {}, {}
        self.gates = {f"topic-{index}": Event() for index in range(31)}

    def snapshot(self):
        with self.lock:
            return list(self.calls), set(self.active), set(self.cancelled)

    def release_all(self):
        for gate in self.gates.values():
            gate.set()

    def produce_to(self, **request):
        topic_id = request["topic_id"]
        directory = Path(request["directory"])
        thread_id = f"parallel-thread-{topic_id}"
        with self.lock:
            self.calls.append({"topic_id": topic_id, "directory": directory,
                               "thread_id": thread_id, "input_thread": request["thread_id"]})
            self.active.add(topic_id)
            self.active_by_topic[topic_id] = self.active_by_topic.get(topic_id, 0) + 1
            self.max_by_topic[topic_id] = max(self.max_by_topic.get(topic_id, 0), self.active_by_topic[topic_id])
            self.max_active = max(self.max_active, sum(self.active_by_topic.values()))
        try:
            request["on_thread_started"](thread_id)
            if self.blocking:
                while not self.gates[topic_id].wait(.01):
                    if request["cancel_event"].is_set():
                        with self.lock:
                            self.cancelled.add(topic_id)
                        raise RuntimeError("isolated producer observed shutdown cancellation")
            if topic_id in self.fail_topics:
                raise RuntimeError(f"isolated failure for {topic_id}")
            directory.mkdir(parents=True)
            (directory / "images").mkdir()
            index = int(topic_id.rsplit("-", 1)[1])
            Image.new("RGB", (32, 48), ((index * 25) % 256, 100, 200)).save(directory / "images/01.png")
            pack = SocialContentPack(pack_id=request["pack_id"], creator_id=request["creator_id"],
                series_id=request["series_id"], topic_id=topic_id, topic_title=request["topic_title"],
                skill_name="knowledge-to-carousel", generated_at="2026-10-19T00:00:00+00:00",
                content_summary=f"Local artifact owned by {topic_id}",
                cards=[CarouselCard(order=1, kind="cover", headline=topic_id, image_path="images/01.png")],
                publish_copy=PublicationCopy(title=topic_id, body=f"Fixture {topic_id}"))
            session = ProductionSession(thread_id=thread_id, pack_id=request["pack_id"],
                created_at="2026-10-19T00:00:00+00:00", usage=CodexUsage(input_tokens=0, output_tokens=0))
            (directory / "social_content_pack.json").write_text(pack.model_dump_json(), encoding="utf-8")
            (directory / "production_session.json").write_text(session.model_dump_json(), encoding="utf-8")
            return ProducedPack(directory=directory, pack=pack, session=session)
        finally:
            with self.lock:
                self.active_by_topic[topic_id] -= 1
                self.active.discard(topic_id)


class SlowCleanupProducer(BlockingProducer):
    """The first topic-0 worker acknowledges cancellation but has not exited."""

    def __init__(self):
        super().__init__(blocking=False)
        self.first_claimed = False
        self.old_started, self.cleanup_started, self.cleanup_release = Event(), Event(), Event()
        self.old_cancel = None

    def release_all(self):
        if self.old_cancel is not None:
            self.old_cancel.set()
        self.cleanup_release.set()
        super().release_all()

    def produce_to(self, **request):
        topic_id = request["topic_id"]
        with self.lock:
            first = topic_id == "topic-0" and not self.first_claimed
            if first:
                self.first_claimed = True
                self.calls.append({"topic_id": topic_id, "directory": Path(request["directory"]),
                    "thread_id": "slow-cleanup-old-thread", "input_thread": request["thread_id"]})
                self.active.add(topic_id)
                self.active_by_topic[topic_id] = self.active_by_topic.get(topic_id, 0) + 1
                self.max_by_topic[topic_id] = self.active_by_topic[topic_id]
                self.max_active = max(self.max_active, sum(self.active_by_topic.values()))
        if not first:
            return super().produce_to(**request)
        try:
            request["on_thread_started"]("slow-cleanup-old-thread")
            self.old_cancel = request["cancel_event"]
            self.old_started.set()
            self.old_cancel.wait()
            self.cleanup_started.set()
            self.cleanup_release.wait()  # Deliberately delay physical termination.
            raise RuntimeError("isolated interrupted worker finally finished cleanup")
        finally:
            with self.lock:
                self.active_by_topic[topic_id] -= 1
                self.active.discard(topic_id)


class ParallelProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.url = f"sqlite:///{(self.root / 'parallel.db').as_posix()}"
        upgrade_database(self.url)
        self.database = Database(self.url)
        repository = ContentRepository(self.database)
        repository.create_creator(creator_id="creator", display_name="Parallel fixture", platform=CreatorPlatform.XIAOHONGSHU)
        repository.create_series(series_id="series", creator_id="creator", name="Parallel",
            description="Local fault injection", audience="test", skill_name="knowledge-to-carousel")
        for index in range(8):
            repository.add_topic(topic_id=f"topic-{index}", series_id="series", title=f"Topic {index}", source=TopicSource.MANUAL)
        self.executors, self.producers = [], []

    def tearDown(self):
        for producer in self.producers:
            producer.release_all()
        try:
            for executor in reversed(self.executors):
                executor.shutdown()
        finally:
            self.database.close()
            self.tmp.cleanup()

    def make(self, *, max_concurrency=5, **producer_options):
        producer = BlockingProducer(**producer_options)
        self.producers.append(producer)
        service = ContentRunService(self.database, producer_factory=lambda: producer,
            output_root=self.root / "outputs", production_protocol="legacy")
        executor = ManagedRunExecutor(service, max_concurrency=max_concurrency, shutdown_timeout=3)
        self.executors.append(executor)
        return producer, service, executor

    def submit(self, service, executor, count):
        runs = [service.create(f"topic-{index}") for index in range(count)]
        for run in runs:
            executor.submit(run.id, expected_version=run.version)
        return runs

    def test_explicit_five_real_overlap_then_refill_and_distinct_artifacts(self):
        producer, service, executor = self.make()
        runs = self.submit(service, executor, 7)
        wait(lambda: len(producer.snapshot()[1]) == 5)
        self.assertEqual(len(producer.snapshot()[0]), 5)
        self.assertEqual(sum(executor.queue_snapshot(run.id)["status"] == "pending" for run in runs), 2)
        first_topic = producer.snapshot()[0][0]["topic_id"]
        producer.gates[first_topic].set()
        wait(lambda: len(producer.snapshot()[0]) == 6)
        self.assertEqual(len(producer.snapshot()[1]), 5)
        self.assertEqual(producer.max_active, 5)
        producer.release_all()
        wait(lambda: all(not executor.is_submitted(run.id) for run in runs))
        self.assertEqual([service.get(run.id).status.value for run in runs], ["awaiting_approval"] * 7)
        calls = producer.snapshot()[0]
        self.assertEqual(len(calls), 7)
        self.assertEqual(len({call["directory"] for call in calls}), 7)
        self.assertEqual(len({call["thread_id"] for call in calls}), 7)
        self.assertTrue(all(call["input_thread"] is None for call in calls))
        hashes = set()
        for run in runs:
            revision = service.get_active_revision(run.id)
            attempts = service.repository.list_attempts(revision.id)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0].producer_thread_id, f"parallel-thread-{run.topic_id}")
            directory = Path(attempts[0].output_directory)
            self.assertTrue(directory.is_relative_to(self.root / "outputs"))
            pack = SocialContentPack.model_validate_json((directory / "social_content_pack.json").read_text(encoding="utf-8"))
            self.assertEqual((pack.topic_id, pack.cards[0].headline), (run.topic_id, run.topic_id))
            hashes.add(sha256((directory / "images/01.png").read_bytes()).hexdigest())
        self.assertEqual(len(hashes), 7)

    def test_same_run_concurrent_response_replays_create_one_attempt(self):
        producer, service, executor = self.make()
        run = service.create("topic-0")
        with ThreadPoolExecutor(max_workers=8) as clients:
            results = list(clients.map(lambda _: executor.submit(run.id, expected_version=run.version), range(8)))
        self.assertTrue(all(result.accepted for result in results))
        wait(lambda: len(producer.snapshot()[0]) == 1)
        self.assertEqual(len(service.repository.list_attempts(service.get_active_revision(run.id).id)), 1)
        producer.release_all()
        wait(lambda: not executor.is_submitted(run.id))
        self.assertEqual(len(producer.snapshot()[0]), 1)

    def test_shutdown_database_delay_is_bounded_and_keeps_instance_lock(self):
        producer, service, executor = self.make()
        self.submit(service, executor, 2)
        wait(lambda: len(producer.snapshot()[1]) == 2)
        executor.shutdown_timeout = .15
        marker_started, marker_release = Event(), Event()
        original = service.interrupt_owner
        def delayed_marker(*args, **kwargs):
            marker_started.set()
            marker_release.wait(4)
            return original(*args, **kwargs)
        try:
            with patch.object(service, "interrupt_owner", delayed_marker):
                started = monotonic()
                with self.assertRaises(ExecutionOwnershipError):
                    executor.shutdown()
                self.assertLess(monotonic() - started, 1)
                self.assertTrue(marker_started.is_set())
                self.assertTrue(all(task.cancel.is_set() for task in executor._active.values()))
                self.assertTrue(service.guard.lock.is_locked)
                with self.assertRaises(ExecutionOwnershipError):
                    LocalExecutionGuard(self.database).__enter__()
        finally:
            marker_release.set()
            wait(lambda: not executor._shutdown_marker.is_alive()
                 and all(task.future.done() for task in executor._active.values()))
            # Isolated fixture reconciliation after every writer really exited.
            service.guard.assert_clean()
            service.guard.__exit__()

    def test_capacity_is_configurable_without_claiming_waiting_work(self):
        producer, service, unused = self.make()
        executor = ManagedRunExecutor(service, max_concurrency=2, shutdown_timeout=3)
        self.executors.append(executor)
        runs = self.submit(service, executor, 3)
        wait(lambda: len(producer.snapshot()[1]) == 2)
        pending = service.get(runs[2].id)
        self.assertEqual(pending.status.value, "queued")
        self.assertIsNone(pending.lease_owner)
        self.assertEqual(service.repository.list_attempts(service.get_active_revision(pending.id).id), ())
        self.assertEqual(executor.queue_snapshot(pending.id)["status"], "pending")
        producer.release_all()
        wait(lambda: all(not executor.is_submitted(run.id) for run in runs))
        self.assertEqual(producer.max_active, 2)

    def test_failure_does_not_cancel_peers_and_free_slot_is_refilled(self):
        producer, service, executor = self.make(fail_topics={"topic-0"})
        runs = self.submit(service, executor, 7)
        wait(lambda: len(producer.snapshot()[1]) == 5)
        producer.gates["topic-0"].set()
        wait(lambda: len(producer.snapshot()[0]) == 6)
        self.assertEqual(executor.queue_snapshot(runs[0].id)["status"], "failed")
        self.assertEqual(len(producer.snapshot()[1]), 5)
        self.assertEqual(producer.snapshot()[2], set())
        self.assertTrue(all(service.get(run.id).status.value == "producing" for run in runs[1:5]))
        producer.release_all()
        wait(lambda: all(not executor.is_submitted(run.id) for run in runs))
        self.assertEqual([executor.queue_snapshot(run.id)["status"] for run in runs], ["failed"] + ["succeeded"] * 6)
        self.assertEqual(producer.max_active, 5)

    def test_cancel_pending_run_leaves_five_active_peers_untouched(self):
        producer, service, executor = self.make()
        runs = self.submit(service, executor, 7)
        wait(lambda: len(producer.snapshot()[1]) == 5)
        pending = runs[5]
        executor.cancel(pending.id, expected_version=service.get(pending.id).version)
        self.assertEqual(executor.queue_snapshot(pending.id)["status"], "cancelled")
        self.assertEqual(len(producer.snapshot()[1]), 5)
        self.assertEqual(producer.snapshot()[2], set())
        producer.release_all()
        wait(lambda: all(not executor.is_submitted(run.id) for run in runs))
        self.assertNotIn(pending.topic_id, {call["topic_id"] for call in producer.snapshot()[0]})
        self.assertEqual(executor.queue_snapshot(runs[6].id)["status"], "succeeded")

    def test_shutdown_interrupts_all_five_and_restart_only_consumes_pending(self):
        producer, service, executor = self.make()
        runs = self.submit(service, executor, 7)
        wait(lambda: len(producer.snapshot()[1]) == 5)
        active_topics = producer.snapshot()[1]
        executor.shutdown()
        self.assertEqual(producer.snapshot()[2], active_topics)
        self.assertEqual(len(producer.snapshot()[0]), 5)
        self.assertEqual([service.get(run.id).status.value for run in runs[:5]], ["interrupted"] * 5)
        self.assertEqual([executor.queue_snapshot(run.id)["status"] for run in runs[5:]], ["pending"] * 2)
        self.database.close()
        self.database = Database(self.url)
        restarted_producer, restarted_service, restarted = self.make(blocking=False)
        restarted.start()
        wait(lambda: all(not restarted.is_submitted(run.id) for run in runs[5:]))
        self.assertEqual({call["topic_id"] for call in restarted_producer.snapshot()[0]}, {"topic-5", "topic-6"})
        self.assertEqual([restarted.queue_snapshot(run.id)["status"] for run in runs[:5]], ["needs_attention"] * 5)
        self.assertEqual([restarted_service.get(run.id).status.value for run in runs[:5]], ["interrupted"] * 5)
        self.assertEqual([restarted_service.get(run.id).status.value for run in runs[5:]], ["awaiting_approval"] * 2)

    def test_same_topic_different_runs_cannot_produce_concurrently(self):
        producer, service, executor = self.make()
        obsolete = service.create("topic-0", idempotency_key="old-request")
        current = service.create("topic-0", idempotency_key="new-request")
        executor.submit(current.id, expected_version=current.version)
        wait(lambda: len(producer.snapshot()[1]) == 1)
        try:
            executor.submit(obsolete.id, expected_version=obsolete.version)
        except ContentRunError:
            pass  # Rejecting before queue admission is also a safe outcome.
        other = service.create("topic-1")
        executor.submit(other.id, expected_version=other.version)
        wait(lambda: len(producer.snapshot()[1]) == 2)
        self.assertEqual(len([call for call in producer.snapshot()[0] if call["topic_id"] == "topic-0"]), 1)
        self.assertEqual(service.get(current.id).status.value, "producing")
        self.assertNotEqual(service.get(obsolete.id).status.value, "producing")
        producer.release_all()
        wait(lambda: not executor.is_submitted(current.id) and not executor.is_submitted(other.id))
        self.assertEqual(producer.max_by_topic["topic-0"], 1)
        self.assertEqual(service.get(current.id).status.value, "awaiting_approval")

    def test_result_persistence_failure_pauses_dispatch_without_cancelling_peers(self):
        producer, service, executor = self.make()
        runs = self.submit(service, executor, 7)
        wait(lambda: len(producer.snapshot()[1]) == 5)
        original_finish = executor.queue.finish

        def fail_one_receipt(item_id, run_id):
            if run_id == runs[0].id:
                raise OSError("isolated queue outcome persistence failure")
            return original_finish(item_id, run_id)

        with patch.object(executor.queue, "finish", side_effect=fail_one_receipt):
            producer.gates["topic-0"].set()
            wait(lambda: executor.queue_snapshot(runs[5].id)["blocked_reason"] is not None)
            self.assertEqual(len(producer.snapshot()[0]), 5)
            self.assertEqual(producer.snapshot()[2], set())
            self.assertEqual(len(producer.snapshot()[1]), 4)
            self.assertTrue(all(service.get(run.id).status.value == "producing" for run in runs[1:5]))
            for run in runs[5:]:
                self.assertEqual(executor.queue_snapshot(run.id)["status"], "pending")
            with self.assertRaises(ExecutionOwnershipError):
                executor.submit(runs[6].id, expected_version=runs[6].version)
            producer.release_all()
            wait(lambda: not producer.snapshot()[1] and not service.guard.journal.exists())
            wait(lambda: all(service.get(run.id).status.value == "awaiting_approval" for run in runs[:5]))
            executor.shutdown()  # A known database error is not a surviving writer.
        self.assertFalse(service.guard.lock.is_locked)
        self.assertEqual(producer.snapshot()[2], set())
        self.assertEqual([executor.queue_snapshot(run.id)["status"] for run in runs[5:]], ["pending"] * 2)
        restarted_producer, restarted_service, restarted = self.make(blocking=False)
        restarted.start()
        wait(lambda: all(not restarted.is_submitted(run.id) for run in runs))
        self.assertEqual({call["topic_id"] for call in restarted_producer.snapshot()[0]}, {"topic-5", "topic-6"})
        self.assertEqual(restarted.queue_snapshot(runs[0].id)["status"], "succeeded")

    def test_newest_run_cannot_claim_topic_still_owned_by_older_inflight_run(self):
        producer, service, executor = self.make()
        old = service.create("topic-0")
        executor.submit(old.id, expected_version=old.version)
        wait(lambda: len(producer.snapshot()[1]) == 1)
        # Fault fixture: a legacy/external client has inserted a newer Run while
        # the old one is in flight. This deliberately bypasses normal create's
        # status gate, to exercise the separate claim-time same-topic guard.
        newest_id, revision_id = str(uuid4()), str(uuid4())
        with self.database.session() as session:
            session.add(ContentRun(id=newest_id, topic_id=old.topic_id,
                idempotency_key="isolated-newest-inflight", status=ContentRunStatus.QUEUED,
                input_snapshot_json=old.input_snapshot_json,
                created_at=datetime.now(timezone.utc) + timedelta(seconds=1)))
            session.add(ContentRevision(id=revision_id, content_run_id=newest_id,
                revision_number=1, production_input_json=old.input_snapshot_json))
        newest = service.get(newest_id)
        with self.assertRaises(ContentRunError) as caught:
            service.claim(newest.id, owner_id="must-not-start", expected_version=newest.version)
        self.assertEqual(caught.exception.code, "topic_active")
        self.assertEqual(service.get(old.id).status.value, "producing")
        self.assertEqual(service.get(newest.id).status.value, "queued")
        self.assertEqual(service.repository.list_attempts(revision_id), ())
        self.assertEqual(len(producer.snapshot()[0]), 1)
        producer.release_all()
        wait(lambda: not executor.is_submitted(old.id))
        self.assertEqual(service.get(old.id).status.value, "awaiting_approval")
        self.assertEqual(service.get(newest.id).status.value, "queued")
        self.assertEqual(producer.max_by_topic["topic-0"], 1)

    def test_fresh_waits_for_physical_cleanup_but_other_topic_bypasses_it(self):
        producer = SlowCleanupProducer()
        self.producers.append(producer)
        service = ContentRunService(self.database, producer_factory=lambda: producer,
            output_root=self.root / "outputs", production_protocol="legacy")
        executor = ManagedRunExecutor(service, max_concurrency=5, shutdown_timeout=3)
        self.executors.append(executor)
        old = service.create("topic-0")
        executor.submit(old.id, expected_version=old.version)
        self.assertTrue(producer.old_started.wait(3))
        owner = service.get(old.id).lease_owner
        self.assertTrue(service.interrupt_owner(old.id, owner_id=owner,
            message="isolated heartbeat loss before child cleanup"))
        producer.old_cancel.set()
        self.assertTrue(producer.cleanup_started.wait(3))
        interrupted = service.get(old.id)
        fresh = service.restart_fresh(old.id, expected_version=interrupted.version,
            request_id="isolated-cleanup-fresh")
        executor.submit(fresh.id, expected_version=fresh.version)
        other = service.create("topic-1")
        executor.submit(other.id, expected_version=other.version)
        # The later independent topic must finish while the first queue item is
        # deliberately still blocked by the old topic's physical worker.
        wait(lambda: executor.queue_snapshot(other.id)["status"] == "succeeded")
        self.assertEqual(executor.queue_snapshot(fresh.id)["status"], "pending")
        self.assertEqual(service.get(fresh.id).status.value, "queued")
        self.assertEqual(service.repository.list_attempts(service.get_active_revision(fresh.id).id), ())
        self.assertEqual(len([call for call in producer.snapshot()[0] if call["topic_id"] == "topic-0"]), 1)
        self.assertIn("topic-0", producer.snapshot()[1])
        producer.cleanup_release.set()
        wait(lambda: executor.queue_snapshot(fresh.id)["status"] == "succeeded")
        self.assertEqual(service.get(old.id).status.value, "interrupted")
        self.assertEqual(executor.queue_snapshot(old.id)["status"], "needs_attention")
        self.assertEqual(service.get(fresh.id).status.value, "awaiting_approval")
        self.assertEqual(producer.max_by_topic["topic-0"], 1)
        topic_calls = [call for call in producer.snapshot()[0] if call["topic_id"] == "topic-0"]
        self.assertEqual(len(topic_calls), 2)
        self.assertEqual(len({call["directory"] for call in topic_calls}), 2)
        self.assertEqual(len({call["thread_id"] for call in topic_calls}), 2)
        self.assertIsNone(topic_calls[1]["input_thread"])

    def test_default_thirty_real_overlap_then_thirty_first_waits_and_refills(self):
        repository = ContentRepository(self.database)
        for index in range(8, 31):
            repository.add_topic(topic_id=f"topic-{index}", series_id="series",
                title=f"Topic {index}", source=TopicSource.MANUAL)
        with patch.dict(os.environ):
            os.environ.pop("CREATOROS_PRODUCTION_CONCURRENCY", None)
            producer, service, executor = self.make(max_concurrency=None)
        self.assertEqual(executor.max_concurrency, 30)
        runs = self.submit(service, executor, 31)
        wait(lambda: len(producer.snapshot()[1]) == 30, timeout=20)
        self.assertEqual(len(producer.snapshot()[0]), 30)
        self.assertEqual(executor.queue_snapshot(runs[30].id)["status"], "pending")
        self.assertEqual(service.get(runs[30].id).status.value, "queued")
        first_topic = producer.snapshot()[0][0]["topic_id"]
        producer.gates[first_topic].set()
        wait(lambda: len(producer.snapshot()[0]) == 31, timeout=20)
        self.assertEqual(len(producer.snapshot()[1]), 30)
        self.assertEqual(producer.max_active, 30)
        producer.release_all()
        wait(lambda: all(not executor.is_submitted(run.id) for run in runs), timeout=30)
        self.assertEqual([service.get(run.id).status.value for run in runs], ["awaiting_approval"] * 31)
        calls = producer.snapshot()[0]
        self.assertEqual(len({call["directory"] for call in calls}), 31)
        self.assertEqual(len({call["thread_id"] for call in calls}), 31)
        self.assertTrue(all(call["input_thread"] is None for call in calls))
        self.assertEqual(set(producer.max_by_topic.values()), {1})
        for run in runs:
            self.assertEqual(len(service.repository.list_attempts(service.get_active_revision(run.id).id)), 1)

    def test_environment_and_explicit_capacity_bounds(self):
        producer, service, unused = self.make()
        for capacity in (1, 5, 30):
            with self.subTest(capacity=capacity):
                self.assertEqual(ManagedRunExecutor(service, max_concurrency=capacity).max_concurrency, capacity)
            with self.subTest(environment=capacity), patch.dict(os.environ,
                    {"CREATOROS_PRODUCTION_CONCURRENCY": str(capacity)}):
                self.assertEqual(ManagedRunExecutor(service).max_concurrency, capacity)
        with patch.dict(os.environ, {"CREATOROS_PRODUCTION_CONCURRENCY": "invalid"}):
            self.assertEqual(ManagedRunExecutor(service, max_concurrency=30).max_concurrency, 30)
        self.assertEqual(producer.snapshot()[0], [])
        self.assertFalse(service.guard.lock.is_locked)

    def test_invalid_capacity_is_rejected_before_starting_executor(self):
        producer, service, unused = self.make()
        for value in (0, 31, -1, True, False, 2.5, "2"):
            with self.subTest(capacity=value), self.assertRaises(ValueError):
                ManagedRunExecutor(service, max_concurrency=value)
        for value in ("not-a-number", "", "0", "31", "2.5"):
            with self.subTest(environment=value), patch.dict(os.environ, {"CREATOROS_PRODUCTION_CONCURRENCY": value}):
                with self.assertRaises(ValueError):
                    ManagedRunExecutor(service)
        self.assertEqual(producer.snapshot()[0], [])
        self.assertFalse(service.guard.lock.is_locked)


if __name__ == "__main__":
    unittest.main()
