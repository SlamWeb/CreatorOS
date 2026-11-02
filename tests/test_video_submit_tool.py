"""Real local MP4 acceptance; transport interruption is explicit fault injection."""
import shutil
import unittest
from unittest.mock import patch

from creatoros.integrations.codex import CodexProducerError
from creatoros.integrations.video_production import (
    VIDEO_CHECKPOINT, VideoCheckpoint, VideoDelivery, VideoSubmission,
    _identity, _thread_has_submission_tool, validate_video_artifact,
)
from creatoros.integrations.visual_production import atomic_json
from creatoros.integrations.worker_protocol import record_thread, record_turn
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
                                         handoff_protocol="submit-tool-v1")
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


if __name__ == "__main__":
    unittest.main()
