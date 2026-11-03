"""Real local MP4 acceptance; transport interruption is explicit fault injection."""
import asyncio
from contextlib import asynccontextmanager
import json
import shutil
import threading
import unittest
from unittest.mock import patch

from creatoros.integrations.codex import CodexProducerError
from creatoros.integrations.video_production import (
    VIDEO_CHECKPOINT, VideoCheckpoint, VideoDelivery, VideoSubmission,
    _identity, _thread_has_submission_tool, validate_video_artifact,
)
from creatoros.integrations.visual_production import atomic_json
from creatoros.integrations.worker_protocol import record_thread, record_turn
from openai_codex.generated import v2_all as sdk_types
from openai_codex.models import Notification
from tests import test_task_video_execution as fixtures


class VideoSubmitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TaskVideoExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.directory = self.fixture.root / "delivery"
        (self.directory / "work").mkdir(parents=True)
        shutil.copyfile(self.fixture.clip, self.directory / "work" / "finished.mp4")
        self.checkpoint = VideoCheckpoint(input_digest="input", thread_id="thread", handoff_protocol="submit-tool-v1")
        self.submit = VideoSubmission(self.directory, self.checkpoint)
        self.arguments = VideoDelivery(title="视频", text="正文", video_path="finished.mp4", complete=True).model_dump()

    def test_wrong_path_then_same_instance_accepts_correct_path(self):
        failed = self.submit({**self.arguments, "video_path": "missing.mp4"})
        self.assertFalse(failed["accepted"])
        self.assertTrue(failed["retryable"])
        self.assertFalse(self.checkpoint.accepted_sha256)
        accepted = self.submit(self.arguments)
        self.assertTrue(accepted["accepted"])
        self.assertEqual(self.checkpoint.submission_attempts, 2)
        saved = VideoCheckpoint.model_validate_json((self.directory / VIDEO_CHECKPOINT).read_text())
        self.assertEqual(saved.submission_digest, accepted["receipt_id"])
        self.assertFalse(saved.turn_completed)

    def test_explicit_failure_is_terminal_durable_and_cannot_be_overwritten(self):
        failure = {**self.arguments, "complete": False, "video_path": None,
                   "error": "无法启动本地剪辑命令"}
        original = (self.directory / "work/finished.mp4").read_bytes()
        result = self.submit(failure)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["retryable"])
        self.assertTrue(result["terminal"])
        self.assertIn("无法启动本地剪辑命令", result["error"])
        self.assertIsNone(self.checkpoint.accepted_sha256)
        self.assertFalse((self.directory / "received-video.mp4").exists())
        saved_bytes = (self.directory / VIDEO_CHECKPOINT).read_bytes()
        saved = VideoCheckpoint.model_validate_json(saved_bytes)
        self.assertIn("无法启动本地剪辑命令", saved.response)

        restored = VideoSubmission(self.directory, saved)
        same = restored(failure)
        replacement = restored(self.arguments)
        self.assertTrue(same["terminal"])
        self.assertFalse(same["retryable"])
        self.assertFalse(replacement["accepted"])
        self.assertFalse(replacement["retryable"])
        self.assertEqual((self.directory / VIDEO_CHECKPOINT).read_bytes(), saved_bytes)
        self.assertEqual((self.directory / "work/finished.mp4").read_bytes(), original)

    def test_legacy_checkpoint_and_malformed_failure_do_not_become_known_failure(self):
        legacy = VideoCheckpoint.model_validate({"input_digest": "input", "thread_id": "thread",
                                                  "handoff_protocol": "submit-tool-v1"})
        self.assertIsNone(legacy.failure_sha256)
        inconsistent = self.submit({**self.arguments, "complete": False, "error": "未完成"})
        self.assertFalse(inconsistent["accepted"])
        self.assertTrue(inconsistent["retryable"])
        self.assertIsNone(self.checkpoint.failure_sha256)
        missing = self.submit({**self.arguments, "video_path": None})
        self.assertTrue(missing["retryable"])
        self.assertTrue(self.submit(self.arguments)["accepted"])

    def test_failure_after_success_cannot_replace_existing_receipt(self):
        self.assertTrue(self.submit(self.arguments)["accepted"])
        saved = (self.directory / VIDEO_CHECKPOINT).read_bytes()
        result = self.submit({**self.arguments, "complete": False, "video_path": None,
                              "error": "后续失败"})
        self.assertFalse(result["accepted"])
        self.assertFalse(result["retryable"])
        self.assertEqual(saved, (self.directory / VIDEO_CHECKPOINT).read_bytes())

    def test_terminal_receipt_write_then_disconnect_reports_unknown_and_can_be_recovered(self):
        from creatoros.delivery.files import atomic_json as real_atomic_json
        failure = {**self.arguments, "complete": False, "video_path": None, "error": "解码失败"}
        def write_then_fail(path, checkpoint):
            real_atomic_json(path, checkpoint)
            raise OSError("lost response after atomic replacement")
        with patch("creatoros.delivery.video_receipt.atomic_json", side_effect=write_then_fail):
            result = self.submit(failure)
        self.assertFalse(result["retryable"])
        self.assertTrue(result["outcome_unknown"])
        self.assertIsNone(self.checkpoint.failure_sha256)
        saved = VideoCheckpoint.model_validate_json((self.directory / VIDEO_CHECKPOINT).read_text(encoding="utf-8"))
        self.assertTrue(VideoSubmission(self.directory, saved)(failure)["terminal"])

    def test_failure_receipt_write_before_replace_then_crash_blocks_restart(self):
        failure = {**self.arguments, "complete": False, "video_path": None, "error": "音轨不可用"}
        @asynccontextmanager
        async def client(deadline, cancel, *, dynamic_tools):
            handler = dynamic_tools[0].handler
            class Turn:
                id, thread_id = "write-fail-turn", "write-fail-thread"
                async def stream(self):
                    await handler(failure)
                    await asyncio.sleep(1)
                    if False:
                        yield None
                async def interrupt(self):
                    pass
            class Thread:
                id = "write-fail-thread"
                async def turn(self, inputs, **controls):
                    return Turn()
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()
        with (patch("creatoros.integrations.video_production._production_client", client),
              patch("creatoros.delivery.video_receipt.atomic_json", side_effect=OSError("before replace"))):
            with self.assertRaises(CodexProducerError) as first:
                self.fixture.produce()
        self.assertEqual(first.exception.error_type, "video_delivery_unconfirmed")
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("no SDK")) as sdk:
            with self.assertRaises(CodexProducerError) as recovered:
                self.fixture.produce(attempt=2)
        self.assertEqual(recovered.exception.error_type, "video_delivery_unconfirmed")
        sdk.assert_not_called()

    def test_guard_written_before_turn_start_conservatively_blocks_restart(self):
        calls = []
        @asynccontextmanager
        async def client(deadline, cancel, *, dynamic_tools):
            class Thread:
                id = "not-started-thread"
                async def turn(self, inputs, **controls):
                    calls.append("turn started")
                    raise AssertionError("must not start")
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()
        with (patch("creatoros.integrations.video_production._production_client", client),
              patch("creatoros.integrations.video_production.record_thread", side_effect=OSError("crash before turn"))):
            with self.assertRaises(CodexProducerError):
                self.fixture.produce()
        old = self.fixture.run / "revision-001/attempt-001" / VIDEO_CHECKPOINT
        before = old.read_bytes()
        saved = VideoCheckpoint.model_validate_json(before)
        self.assertEqual(saved.submission_guard, "armed")
        self.assertEqual(calls, [])
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("no SDK")) as sdk:
            with self.assertRaises(CodexProducerError) as recovered:
                self.fixture.produce(attempt=2)
        self.assertEqual(recovered.exception.error_type, "video_delivery_unconfirmed")
        self.assertIn("可能未开始", str(recovered.exception))
        self.assertEqual(old.read_bytes(), before)
        sdk.assert_not_called()

    def test_guard_write_failure_before_dispatch_never_starts_turn(self):
        turns = []
        @asynccontextmanager
        async def client(deadline, cancel, *, dynamic_tools):
            class Thread:
                id = "guard-write-failed-thread"
                async def turn(self, inputs, **controls):
                    turns.append(True)
                    raise AssertionError("turn must not start")
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()
        with (patch("creatoros.integrations.video_production._production_client", client),
              patch("creatoros.integrations.video_production.atomic_json", side_effect=OSError("no space"))):
            with self.assertRaises(CodexProducerError):
                self.fixture.produce()
        self.assertEqual(turns, [])

    def test_user_stop_preserves_unknown_outcome_and_resume_does_not_restart(self):
        from creatoros.integrations.video_production import produce_video
        cancel, interrupts = threading.Event(), []
        @asynccontextmanager
        async def client(deadline, cancel_event, *, dynamic_tools):
            class Turn:
                id, thread_id = "user-stop-turn", "user-stop-thread"
                async def stream(self):
                    cancel.set()
                    await asyncio.sleep(1)
                    if False:
                        yield None
                async def interrupt(self):
                    interrupts.append(True)
            class Thread:
                id = "user-stop-thread"
                async def turn(self, inputs, **controls):
                    return Turn()
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()
        with patch("creatoros.integrations.video_production._production_client", client):
            with self.assertRaises(CodexProducerError) as stopped:
                produce_video(self.fixture.producer, directory=self.fixture.run / "revision-001/attempt-001",
                    pack_id="run-r001", creator_id="creator", series_id="series", topic_id="topic", topic_title="Title",
                    topic_brief="brief", series_description="", audience="", revision_instruction=None,
                    execution_spec=self.fixture.spec, skills_root=self.fixture.catalog.root, cancel_event=cancel)
        self.assertEqual(stopped.exception.error_type, "codex_interrupted")
        self.assertEqual(interrupts, [True])
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("no SDK")) as sdk:
            with self.assertRaises(CodexProducerError) as recovered:
                self.fixture.produce(attempt=2)
        self.assertEqual(recovered.exception.error_type, "video_delivery_unconfirmed")
        sdk.assert_not_called()

    def test_corrupt_guard_checkpoint_is_an_evidence_conflict(self):
        async def damaged(producer, directory, spec, payload, checkpoint, request):
            saved = VideoCheckpoint(input_digest=_identity(spec, payload), thread_id="thread",
                                    handoff_protocol="submit-tool-v1")
            data = saved.model_dump()
            data["submission_guard"] = "damaged"
            (directory / VIDEO_CHECKPOINT).write_text(json.dumps(data), encoding="utf-8")
            raise OSError("injected crash")
        with patch("creatoros.integrations.video_production._execute_video", damaged):
            with self.assertRaises(CodexProducerError):
                self.fixture.produce()
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("no SDK")) as sdk:
            with self.assertRaises(CodexProducerError) as recovered:
                self.fixture.produce(attempt=2)
        self.assertEqual(recovered.exception.error_type, "video_delivery_evidence_conflict")
        sdk.assert_not_called()

    def test_failure_saved_before_stream_disconnect_still_requests_interrupt(self):
        failure = {**self.arguments, "complete": False, "video_path": None, "error": "音轨不可用"}
        interrupts = []
        @asynccontextmanager
        async def client(deadline, cancel, *, dynamic_tools):
            handler = dynamic_tools[0].handler
            class Turn:
                id, thread_id = "disconnect-turn", "disconnect-thread"
                async def stream(self):
                    await handler(failure)
                    raise OSError("stream disconnected before turn/completed")
                    if False:
                        yield None
                async def interrupt(self):
                    interrupts.append(True)
            class Thread:
                id = "disconnect-thread"
                async def turn(self, inputs, **controls):
                    return Turn()
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()
        with patch("creatoros.integrations.video_production._production_client", client):
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce()
        self.assertEqual(error.exception.error_type, "video_generation_failed")
        self.assertIn("音轨不可用", str(error.exception))
        self.assertEqual(interrupts, [True])

    def test_failure_after_disconnect_reports_unconfirmed_when_interrupt_fails(self):
        failure = {**self.arguments, "complete": False, "video_path": None, "error": "音轨不可用"}
        @asynccontextmanager
        async def client(deadline, cancel, *, dynamic_tools):
            handler = dynamic_tools[0].handler
            class Turn:
                id, thread_id = "interrupt-error-turn", "interrupt-error-thread"
                async def stream(self):
                    await handler(failure)
                    raise OSError("stream disconnected")
                    if False:
                        yield None
                async def interrupt(self):
                    raise OSError("interrupt not acknowledged")
            class Thread:
                id = "interrupt-error-thread"
                async def turn(self, inputs, **controls):
                    return Turn()
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()
        with patch("creatoros.integrations.video_production._production_client", client):
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce()
        self.assertEqual(error.exception.error_type, "codex_interrupt_unconfirmed")
        self.assertIn("音轨不可用", str(error.exception))
        self.assertIn("停止请求未得到 Codex 确认", str(error.exception))

    def test_failed_terminal_event_for_current_turn_keeps_original_failure(self):
        failure = {**self.arguments, "complete": False, "video_path": None, "error": "音轨不可用"}
        interrupts = []
        @asynccontextmanager
        async def client(deadline, cancel, *, dynamic_tools):
            handler = dynamic_tools[0].handler
            class Turn:
                id, thread_id = "failed-turn", "failed-thread"
                async def stream(self):
                    await handler(failure)
                    yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                        "threadId": self.thread_id, "turn": {"id": self.id, "items": [], "status": "failed"}}))
                async def interrupt(self):
                    interrupts.append(True)
                    raise OSError("turn already ended")
            class Thread:
                id = "failed-thread"
                async def turn(self, inputs, **controls):
                    return Turn()
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()
        with patch("creatoros.integrations.video_production._production_client", client):
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce()
        self.assertEqual(error.exception.error_type, "video_generation_failed")
        self.assertIn("音轨不可用", str(error.exception))
        self.assertEqual(interrupts, [])

    def test_terminal_event_is_enough_when_stream_has_not_closed(self):
        failure = {**self.arguments, "complete": False, "video_path": None, "error": "音轨不可用"}
        interrupts = []
        @asynccontextmanager
        async def client(deadline, cancel, *, dynamic_tools):
            handler = dynamic_tools[0].handler
            class Turn:
                id, thread_id = "open-stream-turn", "open-stream-thread"
                async def stream(self):
                    await handler(failure)
                    yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                        "threadId": self.thread_id, "turn": {"id": self.id, "items": [], "status": "failed"}}))
                    await asyncio.sleep(1)
                async def interrupt(self):
                    interrupts.append(True)
                    raise OSError("turn already ended")
            class Thread:
                id = "open-stream-thread"
                async def turn(self, inputs, **controls):
                    return Turn()
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()
        with patch("creatoros.integrations.video_production._production_client", client):
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce()
        self.assertEqual(error.exception.error_type, "video_generation_failed")
        self.assertIn("音轨不可用", str(error.exception))
        self.assertEqual(interrupts, [])
        attempt = self.fixture.run / "revision-001/attempt-001"
        worker = json.loads((attempt / "worker_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(worker["turns"][-1]["status"], "failed")

    def test_terminal_event_arriving_during_interrupt_preserves_failure_reason(self):
        failure = {**self.arguments, "complete": False, "video_path": None, "error": "音轨不可用"}
        @asynccontextmanager
        async def client(deadline, cancel, *, dynamic_tools):
            handler = dynamic_tools[0].handler
            interrupt_started, terminal_processed = asyncio.Event(), asyncio.Event()
            class Turn:
                id, thread_id = "race-turn", "race-thread"
                async def stream(self):
                    await handler(failure)
                    await interrupt_started.wait()
                    try:
                        yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                            "threadId": self.thread_id, "turn": {"id": self.id, "items": [], "status": "failed"}}))
                    finally:
                        terminal_processed.set()
                async def interrupt(self):
                    interrupt_started.set()
                    await asyncio.wait_for(terminal_processed.wait(), 2)
                    raise OSError("turn already ended")
            class Thread:
                id = "race-thread"
                async def turn(self, inputs, **controls):
                    return Turn()
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()
        with patch("creatoros.integrations.video_production._production_client", client):
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce()
        self.assertEqual(error.exception.error_type, "video_generation_failed")
        self.assertIn("音轨不可用", str(error.exception))
        self.assertNotIn("停止请求未得到 Codex 确认", str(error.exception))

    def test_user_stop_with_matching_terminal_during_interrupt_is_not_unknown(self):
        from creatoros.integrations.video_production import produce_video
        cancel = threading.Event()
        @asynccontextmanager
        async def client(deadline, cancel_event, *, dynamic_tools):
            interrupt_started, terminal_processed = asyncio.Event(), asyncio.Event()
            class Turn:
                id, thread_id = "cancel-race-turn", "cancel-race-thread"
                async def stream(self):
                    cancel.set()
                    await interrupt_started.wait()
                    try:
                        yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                            "threadId": self.thread_id, "turn": {"id": self.id, "items": [], "status": "interrupted"}}))
                    finally:
                        terminal_processed.set()
                async def interrupt(self):
                    interrupt_started.set()
                    await asyncio.wait_for(terminal_processed.wait(), 2)
                    raise OSError("turn already ended")
            class Thread:
                id = "cancel-race-thread"
                async def turn(self, inputs, **controls):
                    return Turn()
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()
        with patch("creatoros.integrations.video_production._production_client", client):
            with self.assertRaises(CodexProducerError) as error:
                produce_video(self.fixture.producer, directory=self.fixture.run / "revision-001/attempt-001",
                    pack_id="run-r001", creator_id="creator", series_id="series", topic_id="topic", topic_title="Title",
                    topic_brief="brief", series_description="", audience="", revision_instruction=None,
                    execution_spec=self.fixture.spec, skills_root=self.fixture.catalog.root, cancel_event=cancel)
        self.assertEqual(error.exception.error_type, "codex_interrupted")

    def test_unrelated_terminal_events_do_not_prove_current_turn_ended(self):
        failure = {**self.arguments, "complete": False, "video_path": None, "error": "音轨不可用"}
        for event_thread, event_turn, status in [("other-thread", "current-turn", "failed"),
                                                 ("current-thread", "other-turn", "failed"),
                                                 ("other-thread", "current-turn", "completed"),
                                                 ("current-thread", "other-turn", "interrupted")]:
            with self.subTest(event_thread=event_thread, event_turn=event_turn, status=status):
                fixture = fixtures.TaskVideoExecutionTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                interrupts = []
                @asynccontextmanager
                async def client(deadline, cancel, *, dynamic_tools):
                    handler = dynamic_tools[0].handler
                    class Turn:
                        id, thread_id = "current-turn", "current-thread"
                        async def stream(self):
                            await handler(failure)
                            yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                                "threadId": event_thread, "turn": {"id": event_turn, "items": [], "status": status}}))
                        async def interrupt(self):
                            interrupts.append(True)
                            raise OSError("interrupt not acknowledged")
                    class Thread:
                        id = "current-thread"
                        async def turn(self, inputs, **controls):
                            return Turn()
                    class Client:
                        async def thread_start(self, **options):
                            return Thread()
                    yield Client()
                with patch("creatoros.integrations.video_production._production_client", client):
                    with self.assertRaises(CodexProducerError) as error:
                        fixture.produce()
                self.assertEqual(error.exception.error_type, "codex_interrupt_unconfirmed")
                self.assertIn("音轨不可用", str(error.exception))
                self.assertEqual(interrupts, [True])

    def test_known_failure_survives_locked_old_work_directory(self):
        failure = {**self.arguments, "complete": False, "video_path": None, "error": "音轨不可用"}
        async def saved_then_disconnected(producer, directory, spec, payload, checkpoint, request):
            saved = VideoCheckpoint(input_digest=_identity(spec, payload), thread_id="thread",
                                    handoff_protocol="submit-tool-v1")
            self.assertTrue(VideoSubmission(directory, saved)(failure)["terminal"])
            raise OSError("process lost after receipt")
        with patch("creatoros.integrations.video_production._execute_video", saved_then_disconnected):
            with self.assertRaises(CodexProducerError):
                self.fixture.produce()
        original = shutil.copytree
        def locked_old_work(source, *args, **kwargs):
            from pathlib import Path
            if Path(source).name == "work" and Path(source).parent.name == "attempt-001":
                raise OSError("work directory locked")
            return original(source, *args, **kwargs)
        with patch("creatoros.delivery.video_receipt.shutil.copytree", locked_old_work):
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce(attempt=2)
        self.assertEqual(error.exception.error_type, "video_generation_failed")
        self.assertIn("音轨不可用", str(error.exception))

    def test_known_failure_is_recovered_without_a_new_codex_turn(self):
        failure = {**self.arguments, "complete": False, "video_path": None,
                   "error": "音频解码器不可用"}
        async def failure_then_disconnect(producer, directory, spec, payload, checkpoint, request):
            checkpoint = VideoCheckpoint(input_digest=_identity(spec, payload), thread_id="thread",
                                         handoff_protocol="submit-tool-v1")
            self.assertTrue(VideoSubmission(directory, checkpoint)(failure)["terminal"])
            raise OSError("injected transport disconnect after failure receipt")

        with patch("creatoros.integrations.video_production._execute_video", failure_then_disconnect):
            with self.assertRaises(CodexProducerError):
                self.fixture.produce()
        old = self.fixture.run / "revision-001/attempt-001"
        before = {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()}
        with patch("creatoros.integrations.video_production._execute_video",
                   side_effect=AssertionError("known failure must not relaunch Codex")) as sdk:
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce(attempt=2)
        self.assertEqual(error.exception.error_type, "video_generation_failed")
        self.assertIn("音频解码器不可用", str(error.exception))
        sdk.assert_not_called()
        self.assertEqual(before, {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()})

    def test_completed_known_failure_is_recovered_without_a_new_codex_turn(self):
        failure = {**self.arguments, "complete": False, "video_path": None,
                   "error": "剪辑依赖不可用"}
        async def failure_then_disconnect(producer, directory, spec, payload, checkpoint, request):
            checkpoint = VideoCheckpoint(input_digest=_identity(spec, payload), thread_id="thread",
                                         handoff_protocol="submit-tool-v1", turn_completed=True)
            self.assertTrue(VideoSubmission(directory, checkpoint)(failure)["terminal"])
            record_thread(directory, "thread")
            record_turn(directory, "turn", "production", "completed")
            raise OSError("disconnect after completion")
        with patch("creatoros.integrations.video_production._execute_video", failure_then_disconnect):
            with self.assertRaises(CodexProducerError):
                self.fixture.produce()
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("no SDK")) as sdk:
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce(attempt=2)
        self.assertEqual(error.exception.error_type, "video_generation_failed")
        self.assertIn("剪辑依赖不可用", str(error.exception))
        sdk.assert_not_called()

    def test_tampered_failure_receipt_and_mismatched_worker_block_resume(self):
        failure = {**self.arguments, "complete": False, "video_path": None, "error": "音轨不可用"}
        async def save_then_disconnect(producer, directory, spec, payload, checkpoint, request):
            saved = VideoCheckpoint(input_digest=_identity(spec, payload), thread_id="original-thread",
                                    handoff_protocol="submit-tool-v1")
            self.assertTrue(VideoSubmission(directory, saved)(failure)["terminal"])
            record_thread(directory, "another-thread")
            record_turn(directory, "turn", "production", "completed")
            raise OSError("disconnect")
        with patch("creatoros.integrations.video_production._execute_video", save_then_disconnect):
            with self.assertRaises(CodexProducerError):
                self.fixture.produce()
        original = self.fixture.run / "revision-001/attempt-001" / VIDEO_CHECKPOINT
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("no SDK")) as sdk:
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce(attempt=2)
        self.assertEqual(error.exception.error_type, "video_delivery_evidence_conflict")
        sdk.assert_not_called()
        # A failed receipt with a bad checksum is independently rejected too.
        checkpoint = VideoCheckpoint.model_validate_json(original.read_text(encoding="utf-8"))
        checkpoint.failure_sha256 = "0" * 64
        atomic_json(original, checkpoint)
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("no SDK")) as sdk:
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce(attempt=3)
        self.assertEqual(error.exception.error_type, "video_delivery_evidence_conflict")
        sdk.assert_not_called()

    def test_terminal_failure_interrupts_current_turn_and_surfaces_the_reason(self):
        failure = {**self.arguments, "complete": False, "video_path": None,
                   "error": "无法读取背景音乐"}
        interactions = {"interrupted": False, "post_failure_work": False, "replies": []}

        @asynccontextmanager
        async def client(deadline, cancel, *, dynamic_tools):
            handler = dynamic_tools[0].handler

            class Turn:
                id, thread_id = "failure-turn", "failure-thread"

                async def stream(self):
                    interactions["replies"].append(await handler(failure))
                    await asyncio.sleep(0.25)
                    if not interactions["interrupted"]:
                        interactions["post_failure_work"] = True
                        interactions["replies"].append(await handler(self_arguments))
                    yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                        "threadId": self.thread_id, "turn": {"id": self.id, "items": [],
                        "status": "interrupted" if interactions["interrupted"] else "completed"}}))

                async def interrupt(self):
                    interactions["interrupted"] = True

            class Thread:
                id = "failure-thread"
                async def turn(self, inputs, **controls):
                    return Turn()

            class Client:
                async def thread_start(self, **options):
                    return Thread()

            yield Client()

        self_arguments = self.arguments
        with patch("creatoros.integrations.video_production._production_client", client):
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce()
        self.assertEqual(error.exception.error_type, "video_generation_failed")
        self.assertIn("无法读取背景音乐", str(error.exception))
        self.assertTrue(interactions["interrupted"])
        self.assertFalse(interactions["post_failure_work"])
        self.assertFalse(interactions["replies"][0]["retryable"])
        attempt = self.fixture.run / "revision-001/attempt-001"
        self.assertFalse((attempt / "video_artifact.json").exists())
        saved = VideoCheckpoint.model_validate_json((attempt / VIDEO_CHECKPOINT).read_text(encoding="utf-8"))
        self.assertIn("无法读取背景音乐", saved.response)

    def test_terminal_failure_marks_sqlite_run_failed_and_not_retryable(self):
        from pathlib import Path
        from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest, skills_root_for
        from creatoros.runs import ContentRunError, ContentRunExecutionError, ContentRunService
        from creatoros.runs.task_contracts import TaskSkill
        from creatoros.storage import (ContentRunStatus, Creator, CreatorPlatform, Database, Series, Topic,
                                      TopicSource, upgrade_database)

        db_url = "sqlite:///" + (self.fixture.root / "terminal-video.db").as_posix()
        upgrade_database(db_url)
        database = Database(db_url)
        self.addCleanup(database.close)
        catalog = ProducerSkillCatalog(skills_root_for(database), project_root=self.fixture.root)
        record = catalog.register_local(self.fixture.root / "source-skill", role="production")
        spec = self.fixture.spec.model_copy(update={"skills": [TaskSkill(skill_id=record["id"],
            digest=_digest(Path(record["local_path"])), role="primary")], "materials": [
                item.model_copy(update={"path": str(self.fixture.root / ("images.zip" if item.kind == "images_zip" else "source.wav"))})
                for item in self.fixture.spec.materials]})
        with database.session() as session:
            session.add(Creator(id="creator", display_name="Test", platform=CreatorPlatform.XIAOHONGSHU))
            session.add(Series(id="series", creator_id="creator", name="Series", description="", audience="", skill_name="knowledge-to-carousel"))
            session.add(Topic(id="topic", series_id="series", title="Title", brief="brief", source=TopicSource.MANUAL, position=1))
        service = ContentRunService(database, producer_factory=lambda: self.fixture.producer,
                                    output_root=self.fixture.root / "outputs")
        run = service._create("topic", execution_spec=spec)
        interaction = {"turns": 0, "interrupted": False}

        @asynccontextmanager
        async def client(deadline, cancel, *, dynamic_tools):
            handler = dynamic_tools[0].handler
            class Turn:
                id, thread_id = "sqlite-turn", "sqlite-thread"
                async def stream(self):
                    await handler({"title": "失败", "text": "未生成", "video_path": None,
                                   "complete": False, "error": "音频解码失败"})
                    await asyncio.sleep(1)
                    yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                        "threadId": self.thread_id, "turn": {"id": self.id, "items": [], "status": "completed"}}))
                async def interrupt(self):
                    interaction["interrupted"] = True
            class Thread:
                id = "sqlite-thread"
                async def turn(self, inputs, **controls):
                    interaction["turns"] += 1
                    return Turn()
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()

        with patch("creatoros.integrations.video_production._production_client", client):
            with self.assertRaises(ContentRunExecutionError):
                service.execute(run.id)
            failed = service.get(run.id)
            self.assertEqual(failed.status, ContentRunStatus.FAILED)
            self.assertEqual(failed.error_type, "video_generation_failed")
            self.assertIn("音频解码失败", failed.error_message)
            self.assertFalse(failed.retryable)
            with self.assertRaises(ContentRunError):
                service.execute(run.id)
        self.assertEqual(interaction["turns"], 1)
        self.assertTrue(interaction["interrupted"])

    def test_unknown_failure_receipt_blocks_concurrent_followup_submission(self):
        from creatoros.delivery.files import atomic_json as real_atomic_json
        replies, writes = [], []
        @asynccontextmanager
        async def client(deadline, cancel, *, dynamic_tools):
            handler = dynamic_tools[0].handler
            class Turn:
                id, thread_id = "unknown-turn", "unknown-thread"
                async def stream(self):
                    failure = {**self_arguments, "complete": False, "video_path": None, "error": "依赖不可用"}
                    async def call(arguments):
                        replies.append(await handler(arguments))
                    await asyncio.gather(call(failure), call(self_arguments))
                    await asyncio.sleep(1)
                    yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                        "threadId": self.thread_id, "turn": {"id": self.id, "items": [], "status": "completed"}}))
                async def interrupt(self):
                    pass
            class Thread:
                id = "unknown-thread"
                async def turn(self, inputs, **controls):
                    return Turn()
            class Client:
                async def thread_start(self, **options):
                    return Thread()
            yield Client()

        def write_then_disconnect(path, checkpoint):
            writes.append(path)
            real_atomic_json(path, checkpoint)
            raise OSError("response lost after atomic replacement")
        self_arguments = self.arguments
        with (patch("creatoros.integrations.video_production._production_client", client),
              patch("creatoros.delivery.video_receipt.atomic_json", side_effect=write_then_disconnect)):
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce()
        self.assertEqual(error.exception.error_type, "video_delivery_unconfirmed")
        self.assertGreaterEqual(len(replies), 1)
        self.assertTrue(all(reply.get("outcome_unknown") for reply in replies))
        self.assertEqual(len(writes), 1)
        attempt = self.fixture.run / "revision-001/attempt-001"
        self.assertFalse((attempt / "received-video.mp4").exists())
        checkpoint = VideoCheckpoint.model_validate_json((attempt / VIDEO_CHECKPOINT).read_text(encoding="utf-8"))
        self.assertIsNotNone(checkpoint.failure_sha256)

    def test_identical_repeat_uses_receipt_and_other_delivery_cannot_replace(self):
        first = self.submit(self.arguments)
        again = self.submit(self.arguments)
        self.assertTrue(again["reused"])
        self.assertEqual(first["receipt_id"], again["receipt_id"])
        rejected = self.submit({**self.arguments, "title": "changed"})
        self.assertFalse(rejected["accepted"])
        self.assertFalse(rejected["retryable"])
        self.assertEqual(self.checkpoint.submission_attempts, 1)

    def test_invalid_submissions_have_persisted_bound(self):
        for _ in range(3):
            result = self.submit({"bad": "schema"})
        self.assertFalse(result["retryable"])
        resumed = VideoSubmission(self.directory, VideoCheckpoint.model_validate_json(
            (self.directory / VIDEO_CHECKPOINT).read_text()))
        self.assertFalse(resumed(self.arguments)["accepted"])

    def test_accepted_bytes_are_protected_from_work_file_changes(self):
        self.assertTrue(self.submit(self.arguments)["accepted"])
        (self.directory / "work/finished.mp4").write_bytes(b"later modification")
        self.assertTrue(self.submit(self.arguments)["accepted"])
        self.assertEqual((self.directory / "received-video.mp4").read_bytes(), self.fixture.clip.read_bytes())

    def test_receipt_write_failure_never_acknowledges_acceptance(self):
        with patch("creatoros.delivery.video_receipt.atomic_json", side_effect=OSError("disk full")):
            result = self.submit(self.arguments)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["retryable"])
        self.assertIsNone(self.checkpoint.accepted_sha256)
        self.assertTrue(self.submit(self.arguments)["accepted"])

    def test_rework_thread_inherits_registered_tool_but_legacy_does_not(self):
        previous = self.fixture.run / "revision-001/attempt-001"
        previous.mkdir(parents=True)
        next_attempt = self.fixture.run / "revision-002/attempt-001"
        atomic_json(previous / VIDEO_CHECKPOINT, self.checkpoint)
        self.assertTrue(_thread_has_submission_tool(next_attempt, "thread"))
        self.checkpoint.handoff_protocol = "final-json-v1"
        atomic_json(previous / VIDEO_CHECKPOINT, self.checkpoint)
        self.assertFalse(_thread_has_submission_tool(next_attempt, "thread"))
        self.assertFalse(_thread_has_submission_tool(next_attempt, "unknown-thread"))

    def test_acknowledged_but_interrupted_turn_recovers_offline(self):
        async def acknowledged_then_interrupted(producer, directory, spec, payload, checkpoint, request):
            shutil.copyfile(self.fixture.clip, directory / "work/finished.mp4")
            checkpoint = VideoCheckpoint(input_digest=_identity(spec, payload), thread_id="thread",
                                         handoff_protocol="submit-tool-v1", submission_guard="armed")
            self.assertTrue(VideoSubmission(directory, checkpoint)(self.arguments)["accepted"])
            raise OSError("connection lost after acknowledgment")
        with patch("creatoros.integrations.video_production._execute_video", acknowledged_then_interrupted):
            with self.assertRaises(CodexProducerError):
                self.fixture.produce()
        old = self.fixture.run / "revision-001/attempt-001"
        before = {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()}
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("no model")):
            recovered = self.fixture.produce(attempt=2)
        self.assertTrue(validate_video_artifact(recovered.directory, self.fixture.spec).video.has_audio)
        self.assertEqual(before, {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()})

    def make_checkpoint_write_failure(self, status, receipt_thread="same-thread"):
        async def completed_before_checkpoint(producer, directory, spec, payload, checkpoint, request):
            saved = VideoCheckpoint(input_digest=_identity(spec, payload), thread_id="same-thread",
                                    handoff_protocol="submit-tool-v1")
            atomic_json(directory / VIDEO_CHECKPOINT, saved)
            shutil.copyfile(self.fixture.clip, directory / "work/finished.mp4")
            record_thread(directory, receipt_thread)
            record_turn(directory, "recorded-turn", "production", status)
            raise OSError("injected failure before checkpoint completion write")

        with patch("creatoros.integrations.video_production._execute_video", completed_before_checkpoint):
            with self.assertRaises(CodexProducerError) as original_error:
                self.fixture.produce()
        self.assertEqual(original_error.exception.error_type, "video_delivery_failed")
        return self.fixture.run / "revision-001/attempt-001"

    def test_completed_worker_receipt_blocks_retry_when_checkpoint_write_was_lost(self):
        old = self.make_checkpoint_write_failure("completed")
        before = {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()}
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("must not call SDK")) as sdk:
            with self.assertRaises(CodexProducerError) as retry_error:
                self.fixture.produce(attempt=2)
        self.assertEqual(retry_error.exception.error_type, "video_delivery_unconfirmed")
        sdk.assert_not_called()
        self.assertEqual(before, {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()})

    def test_interrupted_matching_worker_turn_is_not_misclassified_as_completed(self):
        self.make_checkpoint_write_failure("interrupted")
        calls = []

        async def controlled_resume(producer, directory, spec, payload, checkpoint, request):
            calls.append((checkpoint.thread_id, checkpoint.turn_completed))
            raise CodexProducerError("controlled resume boundary", error_type="controlled_resume")

        with patch("creatoros.integrations.video_production._execute_video", controlled_resume):
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce(attempt=2)
        self.assertEqual(error.exception.error_type, "controlled_resume")
        self.assertEqual(calls, [("same-thread", False)])

    def test_worker_receipt_from_another_thread_stops_as_evidence_conflict(self):
        old = self.make_checkpoint_write_failure("completed", receipt_thread="other-thread")
        before = {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()}
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("must not call SDK")) as sdk:
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce(attempt=2)
        self.assertEqual(error.exception.error_type, "video_delivery_evidence_conflict")
        self.assertIn("原会话不匹配", str(error.exception))
        sdk.assert_not_called()
        self.assertEqual(before, {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()})

    def test_unknown_worker_status_stops_without_claiming_completion(self):
        self.make_checkpoint_write_failure("unknown")
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("must not call SDK")) as sdk:
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce(attempt=2)
        self.assertEqual(error.exception.error_type, "video_delivery_evidence_conflict")
        self.assertIn("执行回执不完整", str(error.exception))
        sdk.assert_not_called()

    def test_non_string_worker_status_stops_as_evidence_conflict(self):
        self.make_checkpoint_write_failure([])
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("must not call SDK")) as sdk:
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce(attempt=2)
        self.assertEqual(error.exception.error_type, "video_delivery_evidence_conflict")
        sdk.assert_not_called()


class VideoRecoveryLinkTests(unittest.TestCase):
    def test_newer_attempt_link_never_falls_back_to_older_checkpoint(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from creatoros.delivery.video_receipt import _recover_work, VideoRecoveryEvidenceConflict
        with TemporaryDirectory(prefix="video-attempt-link-") as temporary:
            root = Path(temporary)
            older = root / "attempt-001"
            (older / "work").mkdir(parents=True)
            checkpoint = VideoCheckpoint(input_digest="a" * 64, thread_id="fixture")
            atomic_json(older / VIDEO_CHECKPOINT, checkpoint)
            external = root / "external"
            external.mkdir()
            newer = root / "attempt-002"
            newer.symlink_to(external, target_is_directory=True)
            current = root / "attempt-003"
            current.mkdir()
            with self.assertRaises(VideoRecoveryEvidenceConflict):
                _recover_work(current, "a" * 64)
            self.assertEqual(list(external.iterdir()), [])
            self.assertFalse((current / "work").exists())
            newer.unlink()
            recovered = _recover_work(current, "a" * 64)
            self.assertEqual(recovered.thread_id, "fixture")


if __name__ == "__main__":
    unittest.main()
