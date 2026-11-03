"""Durability/crash fault injection: isolated SQLite, local artifacts, no paid models."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import multiprocessing
import os
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
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


def _restart_after_unknown_unlink(url, output_root, run_id, result):
    database = Database(url)
    producer = Producer()
    executor = None
    try:
        service = ContentRunService(database, producer_factory=lambda: producer,
                                    output_root=Path(output_root), production_protocol="legacy")
        executor = ManagedRunExecutor(service, max_concurrency=1)
        scanned = Event()
        next_pending = executor.queue.next_pending
        def observe_scan(**kwargs):
            item = next_pending(**kwargs)
            if item is None:
                scanned.set()
            return item
        executor.queue.next_pending = observe_scan
        executor.start()
        if not scanned.wait(20):
            raise TimeoutError("dispatcher did not complete an empty scan")
        executor.shutdown()  # Join workers before taking the immutable evidence snapshot.
        run = service.get(run_id)
        revision = service.get_active_revision(run_id)
        attempts = service.repository.list_attempts(revision.id)
        observed = (run.status.value, executor.queue_snapshot(run_id)["status"],
                    [item.status.value for item in attempts], tuple(producer.calls))
        result.put(observed)
    except BaseException:
        result.put(("error", traceback.format_exc()))
    finally:
        if executor is not None:
            executor.shutdown()
        database.close()


def _crash_inflight_with_evidence(url, output_root, run_id, version, evidence):
    database = Database(url)
    service = ContentRunService(database, output_root=Path(output_root), production_protocol="legacy")
    with service.guard:
        prepared = service.claim(run_id, owner_id="crashed-fixture", expected_version=version)
        directory = Path(prepared["directory"])
        directory.mkdir(parents=True)
        (directory / "historical.bin").write_bytes(b"old evidence")
        if evidence == "image":
            (directory / "artifact-submission-intent.json").write_text("partial", encoding="utf-8")
        elif evidence == "previous_image":
            (directory / "artifact-submission-intent.json").write_text("partial", encoding="utf-8")
            service.interrupt_owner(run_id, owner_id="crashed-fixture")
            service.guard.finish("crashed-fixture")
            # A newer authorized Attempt crashed before its directory existed.
            prepared = service.claim(run_id, owner_id="crashed-fixture", expected_version=service.get(run_id).version)
        elif evidence == "image_missing":
            from creatoros.delivery.image import CHECKPOINT, Checkpoint
            checkpoint = Checkpoint(input_digest="a" * 64, thread_id="fixture", skill_digests={},
                                    handoff_protocol="tool-v1", submission_sha256="b" * 64)
            (directory / CHECKPOINT).write_text(checkpoint.model_dump_json(), encoding="utf-8")
        elif evidence in {"video_missing", "video_valid", "video_failed"}:
            import hashlib
            from creatoros.delivery.video import VIDEO_CHECKPOINT, VideoCheckpoint, VideoDelivery
            if evidence == "video_failed":
                response = VideoDelivery(title="fixture", text="", video_path=None, complete=False,
                                         error="original terminal failure").model_dump_json()
                value = VideoCheckpoint(input_digest="a" * 64, thread_id="fixture",
                    handoff_protocol="submit-tool-v1", response=response,
                    failure_sha256=hashlib.sha256(response.encode()).hexdigest())
            else:
                response = VideoDelivery(title="fixture", text="", video_path="clip.mp4", complete=True).model_dump_json()
                contents = b"known video bytes"
                value = VideoCheckpoint(input_digest="a" * 64, thread_id="fixture",
                    handoff_protocol="submit-tool-v1", response=response,
                    accepted_sha256=hashlib.sha256(contents).hexdigest(),
                    submission_digest=hashlib.sha256(response.encode()).hexdigest())
                if evidence == "video_valid":
                    (directory / "received-video.mp4").write_bytes(contents)
            (directory / VIDEO_CHECKPOINT).write_text(value.model_dump_json(), encoding="utf-8")
        elif evidence in {"video", "video_conflict"}:
            from creatoros.delivery.video import VIDEO_CHECKPOINT, VideoCheckpoint
            if evidence == "video":
                value = VideoCheckpoint(input_digest="a" * 64, thread_id="fixture",
                                        handoff_protocol="submit-tool-v1", submission_guard="armed")
                (directory / VIDEO_CHECKPOINT).write_text(value.model_dump_json(), encoding="utf-8")
            else:
                (directory / VIDEO_CHECKPOINT).write_text("{broken", encoding="utf-8")
        # Crash after the owner record has disappeared: only the DB and the
        # original Attempt's files remain for the independent restart.
        service.guard.finish("crashed-fixture")
        os._exit(0)


def _restart_inflight_snapshot(url, output_root, run_id, result):
    database = Database(url)
    producer = Producer()
    service = ContentRunService(database, producer_factory=lambda: producer,
                                output_root=Path(output_root), production_protocol="legacy")
    executor = ManagedRunExecutor(service, max_concurrency=1)
    try:
        executor.start()
        executor.shutdown()
        state = service.get(run_id)
        attempts = service.repository.list_attempts(service.get_active_revision(run_id).id)
        result.put((state.status.value, state.error_type, state.retryable,
                    executor.queue_snapshot(run_id)["status"], len(attempts),
                    attempts[-1].error_type, attempts[-1].retryable, tuple(producer.calls)))
    except BaseException:
        result.put(("error", traceback.format_exc()))
    finally:
        database.close()


def _restart_after_unknown_replace(url, output_root, run_id, result):
    database = Database(url)
    producer = Producer()
    executor = None
    try:
        service = ContentRunService(database, producer_factory=lambda: producer,
                                    output_root=Path(output_root), production_protocol="legacy")
        executor = ManagedRunExecutor(service, max_concurrency=1)
        try:
            executor.start()
        except ExecutionOwnershipError:
            blocked = True
        else:
            blocked = False
        executor.shutdown()
        run = service.get(run_id)
        revision = service.get_active_revision(run_id)
        attempts = service.repository.list_attempts(revision.id)
        result.put((blocked, run.status.value, executor.queue_snapshot(run_id)["status"],
                    [item.status.value for item in attempts], tuple(producer.calls)))
    except BaseException:
        result.put(("error", traceback.format_exc()))
    finally:
        if executor is not None:
            executor.shutdown()
        database.close()


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
        executor = ManagedRunExecutor(service, max_concurrency=1)
        self.executors.append(executor)
        return executor

    def test_shutdown_waits_for_inflight_unknown_result_before_recording_status(self):
        from creatoros.integrations.codex import CodexProducerError
        from creatoros.runs import executor as executor_module
        started, cancelled, release, reached_wait = Event(), Event(), Event(), Event()
        class DelayedProducer:
            def produce_to(self, **request):
                from types import SimpleNamespace
                from creatoros.delivery.image import SUBMISSION_INTENT
                from creatoros.integrations.artifact_submission import ImageSubmission
                self.directory = Path(request["directory"])
                self.directory.mkdir(parents=True)
                (self.directory / "historical.bin").write_bytes(b"old evidence")
                started.set()
                if not request["cancel_event"].wait(10):
                    raise AssertionError("shutdown did not cancel")
                cancelled.set()
                if not release.wait(10):
                    raise AssertionError("producer was not released")
                marker = self.directory / SUBMISSION_INTENT
                real_open = Path.open
                def deny_guard(path, *args, **kwargs):
                    if path == marker:
                        raise OSError("injected guard write failure")
                    return real_open(path, *args, **kwargs)
                handler = ImageSubmission(self.directory, SimpleNamespace(), self.directory, {})
                with patch.object(Path, "open", autospec=True, side_effect=deny_guard):
                    handler.mark_unknown()
                self.guard_absent = not marker.exists()
                raise CodexProducerError("unknown write", error_type="image_delivery_unconfirmed")
        producer = DelayedProducer()
        service = self.service(producer)
        run = service.create("topic-0")
        executor = self.executor(service)
        executor.submit(run.id, expected_version=run.version)
        self.assertTrue(started.wait(10))
        real_wait = executor_module.wait
        def ordered_wait(futures, timeout):
            marker = getattr(executor, "_shutdown_marker", None)
            if marker is not None:
                marker.join(5)
            reached_wait.set()
            return real_wait(futures, timeout=timeout)
        errors = []
        def close():
            try:
                executor.shutdown()
            except Exception as error:
                errors.append(error)
        try:
            with patch("creatoros.runs.executor.wait", side_effect=ordered_wait):
                thread = Thread(target=close)
                thread.start()
                self.assertTrue(cancelled.wait(10))
                self.assertTrue(reached_wait.wait(10))
                release.set()
                thread.join(20)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            state = service.get(run.id)
            attempt = service.repository.list_attempts(service.get_active_revision(run.id).id)[0]
            self.assertEqual((state.error_type, state.retryable), ("image_delivery_unconfirmed", False))
            self.assertEqual((attempt.error_type, attempt.retryable), ("image_delivery_unconfirmed", False))
            self.assertTrue(producer.guard_absent)
            self.assertEqual((producer.directory / "historical.bin").read_bytes(), b"old evidence")
        finally:
            release.set()

    def test_unknown_result_survives_sqlite_writer_lock_longer_than_busy_timeout(self):
        import sqlite3
        from creatoros.integrations.codex import CodexProducerError
        started, release, first_failed = Event(), Event(), Event()
        class DelayedProducer:
            def produce_to(self, **request):
                started.set()
                if not release.wait(10):
                    raise AssertionError("fixture producer was not released")
                raise CodexProducerError("unknown submission", error_type="image_delivery_unconfirmed")
        service = self.service(DelayedProducer())
        run = service.create("topic-0")
        executor = self.executor(service)
        original = service._mark_failed
        def observe_failed_write(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except Exception:
                first_failed.set()
                raise
        locker = None
        try:
            with patch.object(service, "_mark_failed", side_effect=observe_failed_write):
                executor.submit(run.id, expected_version=run.version)
                self.assertTrue(started.wait(10))
                locker = sqlite3.connect(self.root / "test.db", timeout=1)
                locker.execute("BEGIN IMMEDIATE")
                release.set()
                self.assertTrue(first_failed.wait(12), "first write must exceed SQLite busy_timeout")
                locker.commit()
                locker.close()
                locker = None
                wait(lambda: not executor.is_submitted(run.id), timeout=15)
            state = service.get(run.id)
            attempt = service.repository.list_attempts(service.get_active_revision(run.id).id)[0]
            self.assertEqual((state.error_type, state.retryable), ("image_delivery_unconfirmed", False))
            self.assertEqual((attempt.error_type, attempt.retryable), ("image_delivery_unconfirmed", False))
        finally:
            release.set()
            if locker is not None:
                locker.rollback()
                locker.close()

    def test_unpersistable_unknown_keeps_guard_and_does_not_finish_queue(self):
        from creatoros.integrations.codex import CodexProducerError
        started, release = Event(), Event()
        class DelayedProducer:
            calls = 0
            def produce_to(self, **request):
                self.calls += 1
                started.set()
                if not release.wait(10):
                    raise AssertionError("fixture producer was not released")
                raise CodexProducerError("unknown submission", error_type="image_delivery_unconfirmed")
        producer = DelayedProducer()
        service = self.service(producer)
        run = service.create("topic-0")
        executor = self.executor(service)
        with patch.object(service, "_mark_failed", side_effect=OSError("database unavailable")):
            executor.submit(run.id, expected_version=run.version)
            self.assertTrue(started.wait(10))
            pending = service.create("topic-1")
            executor.submit(pending.id, expected_version=pending.version)
            wait(lambda: run.id in executor._active, timeout=10)
            task = executor._active[run.id]
            release.set()
            wait(lambda: task.future.done(), timeout=10)
            self.assertIsNotNone(executor._blocked_error)
            self.assertEqual(service.get(run.id).status.value, "producing")
            self.assertEqual(executor.queue_snapshot(run.id)["status"], "running")
            self.assertEqual(executor.queue_snapshot(pending.id)["status"], "pending")
            self.assertEqual(producer.calls, 1)
            self.assertTrue(service.guard.lock.is_locked)
            with self.assertRaises(ExecutionOwnershipError):
                executor.submit(run.id, expected_version=service.get(run.id).version)
            ctx = multiprocessing.get_context("spawn")
            result = ctx.Queue()
            child = ctx.Process(target=_restart_inflight_snapshot,
                                args=(self.url, str(self.root / "outputs"), run.id, result))
            child.start()
            observed = result.get(timeout=30)
            child.join(20)
            self.assertEqual(child.exitcode, 0)
            self.assertEqual(observed[0], "error")
            result.close()
            self.assertEqual(service.get(run.id).status.value, "producing")
            self.assertEqual(executor.queue_snapshot(run.id)["status"], "running")
            with self.assertRaises(ExecutionOwnershipError):
                executor.shutdown()
            self.assertTrue(service.guard.lock.is_locked)
            if executor._dispatcher is not None:
                executor._dispatcher.join(10)
                self.assertFalse(executor._dispatcher.is_alive())
        # This isolated fake producer has exited and has no child process.
        service.guard.finish(task.owner_id)
        service.guard.__exit__()

    def test_unknown_image_result_wins_over_concurrent_cancellation(self):
        from creatoros.integrations.codex import CodexProducerError
        started, release, cancellation = Event(), Event(), []
        class UnknownProducer:
            def __init__(self):
                self.calls = 0
            def produce_to(self, **request):
                self.calls += 1
                cancellation.append(request["cancel_event"])
                started.set()
                if not release.wait(10):
                    raise AssertionError("test did not release producer")
                raise CodexProducerError("submission outcome unknown", error_type="image_delivery_unconfirmed")
        producer = UnknownProducer()
        service = self.service(producer)
        run = service.create("topic-0")
        executor = self.executor(service)
        executor.submit(run.id, expected_version=run.version)
        self.assertTrue(started.wait(10))
        cancellation[0].set()  # Concurrent caller cancels while producer is in flight.
        release.set()
        wait(lambda: not executor.is_submitted(run.id), timeout=10)
        actual = service.get(run.id)
        attempt = service.repository.list_attempts(service.get_active_revision(run.id).id)[0]
        self.assertEqual(actual.error_type, "image_delivery_unconfirmed")
        self.assertFalse(actual.retryable)
        self.assertEqual(actual.status.value, "failed")
        self.assertEqual(attempt.error_type, "image_delivery_unconfirmed")
        self.assertFalse(attempt.retryable)
        self.assertEqual(producer.calls, 1)

    def test_unknown_video_results_win_over_concurrent_cancellation(self):
        from creatoros.integrations.codex import CodexProducerError
        for index, error_type in enumerate(("video_delivery_unconfirmed", "video_delivery_evidence_conflict", "video_generation_failed")):
            with self.subTest(error_type=error_type):
                started, release, cancellation = Event(), Event(), []
                class ProducerWithUnknownVideo:
                    def produce_to(self, **request):
                        if error_type == "video_generation_failed":
                            import hashlib
                            from creatoros.delivery.video import VIDEO_CHECKPOINT, VideoCheckpoint, VideoDelivery
                            directory = Path(request["directory"])
                            directory.mkdir(parents=True)
                            response = VideoDelivery(title="fixture", text="", video_path=None,
                                                     complete=False, error="original video failure").model_dump_json()
                            checkpoint = VideoCheckpoint(input_digest="a" * 64, thread_id="fixture",
                                handoff_protocol="submit-tool-v1", response=response,
                                failure_sha256=hashlib.sha256(response.encode()).hexdigest())
                            (directory / VIDEO_CHECKPOINT).write_text(checkpoint.model_dump_json(), encoding="utf-8")
                        cancellation.append(request["cancel_event"])
                        started.set()
                        if not release.wait(10):
                            raise AssertionError("producer was not released")
                        raise CodexProducerError("original video failure" if error_type == "video_generation_failed"
                                                 else "video evidence requires inspection", error_type=error_type)
                service = self.service(ProducerWithUnknownVideo())
                run = service.create(f"topic-{index}")
                executor = self.executor(service)
                executor.submit(run.id, expected_version=run.version)
                self.assertTrue(started.wait(10))
                cancellation[0].set()
                release.set()
                wait(lambda: not executor.is_submitted(run.id), timeout=10)
                state = service.get(run.id)
                attempt = service.repository.list_attempts(service.get_active_revision(run.id).id)[0]
                self.assertEqual((state.error_type, state.retryable), (error_type, False))
                self.assertEqual((attempt.error_type, attempt.retryable), (error_type, False))
                if error_type == "video_generation_failed":
                    self.assertEqual(state.error_message, "original video failure")
                executor.shutdown()

    def test_old_completion_cannot_finish_reauthorized_queue_version(self):
        from creatoros.storage.models import ProductionQueueItem
        service = self.service(Producer())
        run = service.create("topic-0")
        queue = ProductionQueue(service)
        item = queue.enqueue(run.id, run.version)
        queue.transition(item.id, "running")
        with service.guard:
            prepared = service.claim(run.id, owner_id="old-owner", expected_version=run.version)
            service.interrupt_owner(run.id, owner_id="old-owner")
            service.guard.finish("old-owner")
            with self.database.session() as session:
                row = session.get(ProductionQueueItem, item.id)
                row.expected_version = run.version + 1
                row.status = "running"
                row.last_error = "new authorization"
            queue.finish(item.id, run.id, expected_attempt_id=prepared["attempt_id"],
                         expected_queue_version=run.version)
            saved = queue.snapshot(run.id)
            self.assertEqual((saved["status"], saved["last_error"]), ("running", "new authorization"))

    def test_shutdown_keeps_unknown_video_failures_nonretryable(self):
        from creatoros.integrations.codex import CodexProducerError
        for index, error_type in enumerate(("video_delivery_unconfirmed", "video_delivery_evidence_conflict", "video_generation_failed")):
            with self.subTest(error_type=error_type):
                started, cancelled, release = Event(), Event(), Event()
                class DelayedVideo:
                    def produce_to(self, **request):
                        started.set()
                        if not request["cancel_event"].wait(10):
                            raise AssertionError("shutdown did not cancel")
                        cancelled.set()
                        if not release.wait(10):
                            raise AssertionError("producer was not released")
                        raise CodexProducerError("video needs inspection", error_type=error_type)
                service = self.service(DelayedVideo())
                run = service.create(f"topic-{index}")
                executor = self.executor(service)
                executor.submit(run.id, expected_version=run.version)
                self.assertTrue(started.wait(10))
                errors = []
                def close():
                    try:
                        executor.shutdown()
                    except Exception as error:
                        errors.append(error)
                thread = Thread(target=close)
                try:
                    thread.start()
                    self.assertTrue(cancelled.wait(10))
                    release.set()
                    thread.join(20)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(errors, [])
                    state = service.get(run.id)
                    attempt = service.repository.list_attempts(service.get_active_revision(run.id).id)[0]
                    self.assertEqual((state.error_type, state.retryable), (error_type, False))
                    self.assertEqual((attempt.error_type, attempt.retryable), (error_type, False))
                finally:
                    release.set()

    def test_heartbeat_loss_retains_unknown_without_overwriting_a_new_owner(self):
        from datetime import datetime, timedelta, timezone
        from creatoros.integrations.codex import CodexProducerError
        from creatoros.storage import ContentAttempt, ContentAttemptStatus, ContentRun, ContentRunStatus
        for index, mode in enumerate(("expired", "reassigned", "completed")):
            with self.subTest(mode=mode):
                started, cancelled, release = Event(), Event(), Event()
                class DelayedProducer:
                    def produce_to(self, **request):
                        started.set()
                        if not request["cancel_event"].wait(10):
                            raise AssertionError("heartbeat failure did not cancel")
                        cancelled.set()
                        if not release.wait(10):
                            raise AssertionError("producer was not released")
                        raise CodexProducerError("unknown result", error_type="image_delivery_unconfirmed")
                service = self.service(DelayedProducer())
                service.lease_seconds = 0.5
                run = service.create(f"topic-{index}")
                executor = self.executor(service)
                with patch.object(service, "heartbeat", side_effect=OSError("injected renewal failure")):
                    executor.submit(run.id, expected_version=run.version)
                    self.assertTrue(started.wait(10))
                    wait(lambda: run.id in executor._active, timeout=10)
                    task = executor._active[run.id]
                    self.assertTrue(cancelled.wait(10))
                    running = service.get(run.id)
                    self.assertEqual(running.status.value, "producing")
                    with self.database.session() as session:
                        row = session.get(ContentRun, run.id)
                        if mode == "reassigned":
                            row.lease_owner = "new-owner"
                            row.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
                        elif mode == "completed":
                            attempt = service.repository.list_attempts(service.get_active_revision(run.id).id)[0]
                            session.get(ContentAttempt, attempt.id).status = ContentAttemptStatus.SUCCEEDED
                            row.status = ContentRunStatus.AWAITING_APPROVAL
                        else:
                            row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                    release.set()
                    wait(lambda: task.future.done(), timeout=10)
                state = service.get(run.id)
                if mode == "reassigned":
                    self.assertEqual((state.status.value, state.lease_owner), ("producing", "new-owner"))
                    self.assertEqual(executor.queue_snapshot(run.id)["status"], "running")
                elif mode == "completed":
                    self.assertEqual(state.status.value, "awaiting_approval")
                else:
                    self.assertEqual((state.error_type, state.retryable), ("image_delivery_unconfirmed", False))
                executor.shutdown()

    def test_process_crash_restart_classifies_original_delivery_evidence(self):
        from datetime import datetime, timezone
        from creatoros.storage.models import ProductionQueueHistory
        cases = (("image", "failed", "image_delivery_unconfirmed", False),
                 ("video", "failed", "video_delivery_unconfirmed", False),
                 ("video_conflict", "failed", "video_delivery_evidence_conflict", False),
                 ("legacy", "interrupted", "recovery_interrupted", True),
                 ("previous_image", "failed", "image_delivery_unconfirmed", False),
                 ("image_missing", "failed", "image_delivery_unconfirmed", False),
                 ("video_missing", "failed", "video_delivery_evidence_conflict", False),
                 ("video_valid", "interrupted", "recovery_interrupted", True),
                 ("video_failed", "failed", "video_generation_failed", False))
        repository = ContentRepository(self.database)
        for index in range(4, len(cases)):
            repository.add_topic(topic_id=f"topic-{index}", series_id="series", title=f"Topic {index}", source=TopicSource.MANUAL)
        ctx = multiprocessing.get_context("spawn")
        for index, (evidence, status, error_type, retryable) in enumerate(cases):
            with self.subTest(evidence=evidence):
                service = self.service(Producer())
                run = service.create(f"topic-{index}")
                queue = ProductionQueue(service)
                item = queue.enqueue(run.id, run.version)
                with self.database.session() as session:
                    session.add(ProductionQueueHistory(queue_item_id=item.id, expected_version=run.version,
                        status="failed", submitted_at=datetime.now(timezone.utc), last_error="prior failure evidence"))
                queue.transition(item.id, "running")
                crash = ctx.Process(target=_crash_inflight_with_evidence,
                                    args=(self.url, str(self.root / "outputs"), run.id, run.version, evidence))
                crash.start()
                crash.join(30)
                self.assertEqual(crash.exitcode, 0)
                attempt = service.repository.list_attempts(service.get_active_revision(run.id).id)[0]
                historical = (Path(attempt.output_directory) / "historical.bin").read_bytes()
                result = ctx.Queue()
                child = ctx.Process(target=_restart_inflight_snapshot,
                                    args=(self.url, str(self.root / "outputs"), run.id, result))
                child.start()
                observed = result.get(timeout=40)
                child.join(30)
                self.assertEqual(child.exitcode, 0)
                queue_status = "failed" if status == "failed" else "needs_attention"
                attempt_count = 2 if evidence == "previous_image" else 1
                self.assertEqual(observed, (status, error_type, retryable, queue_status, attempt_count, error_type, retryable, ()))
                if evidence == "video_failed":
                    self.assertEqual(service.get(run.id).error_message, "original terminal failure")
                self.assertEqual((Path(attempt.output_directory) / "historical.bin").read_bytes(), historical)
                self.assertEqual(queue.snapshot(run.id)["history"][0]["last_error"], "prior failure evidence")
                if not retryable:
                    from creatoros.runs.errors import ContentRunError
                    with service.guard:
                        with self.assertRaises(ContentRunError):
                            service.claim(run.id, owner_id="blocked-retry", expected_version=service.get(run.id).version)
                    self.assertEqual(len(service.repository.list_attempts(service.get_active_revision(run.id).id)), attempt_count)
                result.close()

    def test_stale_attempt_non_directory_is_evidence_conflict(self):
        target = self.root / "not-an-attempt-directory"
        target.write_bytes(b"original evidence")
        kind, _ = ContentRunService._stale_delivery_evidence(str(target))
        self.assertEqual(kind, "delivery_evidence_conflict")
        self.assertEqual(target.read_bytes(), b"original evidence")

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

    @unittest.skipUnless(os.name == "nt", "requires Windows error codes")
    def test_restart_after_unknown_successful_unlink_never_replays(self):
        ctx = multiprocessing.get_context("spawn")
        for index, completed in enumerate((False, True)):
            with self.subTest(completed=completed):
                producer = Producer()
                service = self.service(producer)
                run = service.create(f"topic-{index}")
                queue = ProductionQueue(service)
                item = queue.enqueue(run.id, run.version)
                queue.transition(item.id, "running")
                with service.guard:
                    prepared = service.claim(run.id, owner_id=f"fixture-{index}", expected_version=run.version)
                    journal = service.guard.journal
                    real_unlink = Path.unlink
                    def unlink_then_error(path, *args, **kwargs):
                        real_unlink(path, *args, **kwargs)
                        if path == journal:
                            raise ctypes.WinError(5)
                    with patch.object(Path, "unlink", autospec=True, side_effect=unlink_then_error):
                        with self.assertRaises(ExecutionOwnershipError):
                            if completed:
                                service.execute_claimed(prepared, owner_id=f"fixture-{index}")
                            else:
                                service.guard.finish(f"fixture-{index}")
                    self.assertFalse(journal.exists())
                    with self.assertRaises(ExecutionOwnershipError):
                        service.guard.assert_dispatchable()
                result = ctx.Queue()
                child = ctx.Process(target=_restart_after_unknown_unlink,
                    args=(self.url, str(self.root / "outputs"), run.id, result))
                try:
                    child.start()
                    observed = result.get(timeout=40)
                    child.join(timeout=20)
                    self.assertEqual(child.exitcode, 0)
                    expected = ("awaiting_approval", "succeeded", ["succeeded"], ()) if completed else (
                        "interrupted", "needs_attention", ["interrupted"], ())
                    self.assertEqual(observed, expected)
                    self.assertEqual(len(producer.calls), 1 if completed else 0)
                finally:
                    if child.is_alive():
                        child.terminate()
                        child.join(timeout=5)
                    result.close()

    @unittest.skipUnless(os.name == "nt", "requires Windows error codes")
    def test_restart_after_unknown_successful_replace_blocks_dispatch(self):
        producer = Producer()
        service = self.service(producer)
        run = service.create("topic-0")
        queue = ProductionQueue(service)
        item = queue.enqueue(run.id, run.version)
        queue.transition(item.id, "running")
        real_replace = Path.replace
        def replace_then_error(source, destination):
            real_replace(source, destination)
            raise ctypes.WinError(5)
        with service.guard:
            with patch.object(Path, "replace", autospec=True, side_effect=replace_then_error):
                with self.assertRaises(ExecutionOwnershipError):
                    service.claim(run.id, owner_id="fixture-unknown", expected_version=run.version)
            saved_journal = service.guard.journal.read_bytes()
            with self.assertRaises(ExecutionOwnershipError):
                service.guard.assert_dispatchable()
        ctx = multiprocessing.get_context("spawn")
        result = ctx.Queue()
        child = ctx.Process(target=_restart_after_unknown_replace,
            args=(self.url, str(self.root / "outputs"), run.id, result))
        try:
            child.start()
            observed = result.get(timeout=40)
            child.join(timeout=20)
            self.assertEqual(child.exitcode, 0)
            self.assertEqual(observed, (True, "interrupted", "running", ["interrupted"], ()))
            self.assertEqual(service.guard.journal.read_bytes(), saved_journal)
            self.assertEqual(producer.calls, [])
        finally:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
            result.close()

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
        original = service.guard.assert_dispatchable
        def pause_after_first():
            if producer.calls and not service.guard.journal.exists():
                raise ExecutionOwnershipError("injected unverified execution")
            original()
        with patch.object(service.guard, "assert_dispatchable", pause_after_first):
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
