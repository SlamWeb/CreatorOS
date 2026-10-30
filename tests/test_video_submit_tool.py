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
        with patch("creatoros.integrations.video_production.atomic_json", side_effect=OSError("disk full")):
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


if __name__ == "__main__":
    unittest.main()
