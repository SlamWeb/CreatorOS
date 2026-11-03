"""Read-only recovery classification using isolated local evidence."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.delivery.image import CHECKPOINT, SUBMISSION, SUBMISSION_INTENT, Checkpoint
from creatoros.delivery.video import VIDEO_CHECKPOINT, VideoCheckpoint, VideoDelivery
from creatoros.runs.recovery_evidence import inspect_recovery_evidence


class RecoveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="recovery-evidence-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "revision-001"
        self.first = self.root / "attempt-001"
        self.first.mkdir(parents=True)
        self.second = self.root / "attempt-002"

    def image_checkpoint(self, **kwargs):
        checkpoint = Checkpoint(input_digest="a" * 64, thread_id="fixture", skill_digests={},
                                handoff_protocol="tool-v1", **kwargs)
        (self.first / CHECKPOINT).write_text(checkpoint.model_dump_json(), encoding="utf-8")
        return checkpoint

    def video_checkpoint(self, **kwargs):
        checkpoint = VideoCheckpoint(input_digest="a" * 64, thread_id="fixture",
                                     handoff_protocol="submit-tool-v1", **kwargs)
        (self.first / VIDEO_CHECKPOINT).write_text(checkpoint.model_dump_json(), encoding="utf-8")
        return checkpoint

    def files(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_new_empty_or_missing_attempt_does_not_mask_prior_unknown_intent(self):
        (self.first / SUBMISSION_INTENT).write_text("partial", encoding="utf-8")
        before = self.files()
        self.assertEqual(inspect_recovery_evidence(self.second)[0], "image_delivery_unconfirmed")
        self.second.mkdir()
        self.assertEqual(inspect_recovery_evidence(self.second)[0], "image_delivery_unconfirmed")
        self.assertEqual(self.files(), before)

    def test_checkpoint_declaring_missing_or_modified_image_receipt_is_not_retryable(self):
        self.image_checkpoint(submission_sha256="b" * 64)
        self.assertEqual(inspect_recovery_evidence(self.first)[0], "image_delivery_unconfirmed")
        (self.first / SUBMISSION).write_text('{"accepted":true}', encoding="utf-8")
        self.assertEqual(inspect_recovery_evidence(self.first)[0], "image_delivery_unconfirmed")

    def test_genuine_old_image_without_receipt_remains_recoverable(self):
        self.image_checkpoint()
        self.assertIsNone(inspect_recovery_evidence(self.first))

    def test_valid_completed_image_and_old_accepted_receipt_are_not_blocked(self):
        payload = {"accepted": True, "thread_id": "fixture", "input_digest": "a" * 64,
                   "turn_id": "turn-1", "payload_sha256": "c" * 64}
        raw = json.dumps(payload)
        (self.first / SUBMISSION).write_text(raw, encoding="utf-8")
        self.image_checkpoint(submission_sha256=hashlib.sha256(raw.encode()).hexdigest(), turn_completed=True)
        self.assertIsNone(inspect_recovery_evidence(self.first))  # Old receipt without intent.
        (self.first / SUBMISSION_INTENT).write_text(json.dumps({
            "protocol": "image-submission-intent-v1", "thread_id": "fixture", "input_digest": "a" * 64,
            "turn_id": "turn-1", "payload_sha256": "c" * 64}), encoding="utf-8")
        (self.first / "worker_receipt.json").write_text(json.dumps({
            "protocol": "creatoros-worker-v1", "thread_id": "fixture",
            "turns": [{"id": "turn-1", "phase": "production", "status": "completed"}]}), encoding="utf-8")
        before = self.files()
        self.assertIsNone(inspect_recovery_evidence(self.first))
        self.assertEqual(before, self.files())

    def test_video_armed_and_corrupt_checkpoint(self):
        self.video_checkpoint(submission_guard="armed")
        self.assertEqual(inspect_recovery_evidence(self.first)[0], "video_delivery_unconfirmed")
        (self.first / VIDEO_CHECKPOINT).write_text("{broken", encoding="utf-8")
        self.assertEqual(inspect_recovery_evidence(self.first)[0], "video_delivery_evidence_conflict")

    def test_video_accepted_file_missing_changed_and_valid(self):
        delivery = VideoDelivery(title="fixture", text="caption", video_path="clip.mp4", complete=True)
        response = delivery.model_dump_json()
        raw = b"fixture bytes; no media decoder is invoked"
        self.video_checkpoint(accepted_sha256=hashlib.sha256(raw).hexdigest(), response=response,
                              submission_digest=hashlib.sha256(response.encode()).hexdigest())
        self.assertEqual(inspect_recovery_evidence(self.first)[0], "video_delivery_evidence_conflict")
        received = self.first / "received-video.mp4"
        received.write_bytes(b"wrong bytes")
        self.assertEqual(inspect_recovery_evidence(self.first)[0], "video_delivery_evidence_conflict")
        received.write_bytes(raw)
        before = self.files()
        self.assertIsNone(inspect_recovery_evidence(self.first))
        self.assertEqual(before, self.files())

    def test_video_terminal_failure_keeps_original_reason(self):
        failure = VideoDelivery(title="fixture", text="caption", video_path=None, complete=False,
                                error="original failure evidence")
        response = failure.model_dump_json()
        self.video_checkpoint(failure_sha256=hashlib.sha256(response.encode()).hexdigest(), response=response)
        self.assertEqual(inspect_recovery_evidence(self.first), ("video_generation_failed", "original failure evidence"))


if __name__ == "__main__":
    unittest.main()
