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

    def test_image_unknown_crosses_999_to_1000_and_1001(self):
        from creatoros.delivery.image_recovery import ImageSubmissionUnconfirmed, recover_checkpoint
        self.first.rename(self.root / "attempt-999")
        previous = self.root / "attempt-999"
        (previous / SUBMISSION_INTENT).write_text("partial", encoding="utf-8")
        for number in (1000, 1001):
            with self.subTest(number=number):
                current = self.root / f"attempt-{number}"
                current.mkdir()
                self.assertEqual(inspect_recovery_evidence(current)[0], "image_delivery_unconfirmed")
                with self.assertRaises(ImageSubmissionUnconfirmed):
                    recover_checkpoint(current, "a" * 64)

    def test_video_unknown_crosses_999_to_1000_and_1001(self):
        from creatoros.delivery.video_receipt import VideoRecoveryUnconfirmed, _recover_work
        self.first.rename(self.root / "attempt-999")
        self.first = self.root / "attempt-999"
        self.video_checkpoint(submission_guard="armed")
        for number in (1000, 1001):
            with self.subTest(number=number):
                current = self.root / f"attempt-{number}"
                current.mkdir()
                self.assertEqual(inspect_recovery_evidence(current)[0], "video_delivery_unconfirmed")
                with self.assertRaises(VideoRecoveryUnconfirmed):
                    _recover_work(current, "a" * 64)

    def test_legal_newer_video_checkpoint_wins_numerically_over_999(self):
        from creatoros.delivery.video_receipt import _recover_work
        self.first.rename(self.root / "attempt-999")
        self.first = self.root / "attempt-999"
        self.video_checkpoint(submission_guard="armed")
        (self.root / "attempt-001").mkdir()
        (self.root / "attempt-1").mkdir()  # Older aliases are behind the valid 1000 boundary.
        newer = self.root / "attempt-1000"
        (newer / "work").mkdir(parents=True)
        checkpoint = VideoCheckpoint(input_digest="a" * 64, thread_id="newer", handoff_protocol="final-json-v1")
        (newer / VIDEO_CHECKPOINT).write_text(checkpoint.model_dump_json(), encoding="utf-8")
        current = self.root / "attempt-1001"
        current.mkdir()
        self.assertIsNone(inspect_recovery_evidence(current))
        self.assertEqual(_recover_work(current, "a" * 64).thread_id, "newer")

    def test_invalid_attempt_current_never_looks_like_no_prior_evidence(self):
        from creatoros.delivery.image_recovery import ImageSubmissionUnconfirmed, recover_checkpoint
        from creatoros.delivery.video_receipt import VideoRecoveryEvidenceConflict, _recover_work
        for name in ("attempt-invalid", "attempt-000"):
            with self.subTest(name=name):
                current = self.root / name
                current.mkdir()
                self.assertEqual(inspect_recovery_evidence(current)[0], "delivery_evidence_conflict")
                with self.assertRaises(ImageSubmissionUnconfirmed):
                    recover_checkpoint(current, "a" * 64)
                with self.assertRaises(VideoRecoveryEvidenceConflict):
                    _recover_work(current, "a" * 64)

    def test_non_attempt_directory_still_has_no_recovery_history(self):
        from creatoros.delivery.image_recovery import recover_checkpoint
        from creatoros.delivery.video_receipt import _recover_work
        current = self.root / "ordinary-workspace"
        current.mkdir()
        self.assertIsNone(inspect_recovery_evidence(current))
        self.assertIsNone(recover_checkpoint(current, "a" * 64))
        self.assertIsNone(_recover_work(current, "a" * 64))

    def test_1000_unknown_is_seen_by_1001_for_both_media(self):
        from creatoros.delivery.image_recovery import ImageSubmissionUnconfirmed, recover_checkpoint
        from creatoros.delivery.video_receipt import VideoRecoveryUnconfirmed, _recover_work
        self.first.rename(self.root / "attempt-1000")
        self.first = self.root / "attempt-1000"
        current = self.root / "attempt-1001"
        current.mkdir()
        marker = self.first / SUBMISSION_INTENT
        marker.write_text("unknown", encoding="utf-8")
        self.assertEqual(inspect_recovery_evidence(current)[0], "image_delivery_unconfirmed")
        with self.assertRaises(ImageSubmissionUnconfirmed):
            recover_checkpoint(current, "a" * 64)
        marker.unlink()  # The following isolated fixture models a video Attempt.
        self.video_checkpoint(submission_guard="armed")
        self.assertEqual(inspect_recovery_evidence(current)[0], "video_delivery_unconfirmed")
        with self.assertRaises(VideoRecoveryUnconfirmed):
            _recover_work(current, "a" * 64)

    def test_duplicate_numeric_attempt_names_fail_closed(self):
        from creatoros.delivery.image_recovery import ImageSubmissionUnconfirmed, recover_checkpoint
        from creatoros.delivery.video_receipt import VideoRecoveryEvidenceConflict, _recover_work
        (self.root / "attempt-1").mkdir()
        current = self.root / "attempt-1000"
        current.mkdir()
        self.assertEqual(inspect_recovery_evidence(current)[0], "delivery_evidence_conflict")
        with self.assertRaises(ImageSubmissionUnconfirmed):
            recover_checkpoint(current, "a" * 64)
        with self.assertRaises(VideoRecoveryEvidenceConflict):
            _recover_work(current, "a" * 64)

    def test_alias_for_current_numeric_identity_is_rejected(self):
        from creatoros.delivery.image_recovery import ImageSubmissionUnconfirmed, recover_checkpoint
        from creatoros.delivery.video_receipt import VideoRecoveryEvidenceConflict, _recover_work
        current = self.root / "attempt-1"
        current.mkdir()
        self.assertEqual(inspect_recovery_evidence(current)[0], "delivery_evidence_conflict")
        with self.assertRaises(ImageSubmissionUnconfirmed):
            recover_checkpoint(current, "a" * 64)
        with self.assertRaises(VideoRecoveryEvidenceConflict):
            _recover_work(current, "a" * 64)

    def test_image_recovery_never_follows_prior_attempt_symlink(self):
        from creatoros.delivery.image_recovery import ImageSubmissionUnconfirmed, recover_checkpoint
        external = self.root / "external"
        external.mkdir()
        (external / "unrelated").write_bytes(b"unchanged")
        (self.root / "attempt-999").symlink_to(external, target_is_directory=True)
        current = self.root / "attempt-1000"
        current.mkdir()
        self.assertEqual(inspect_recovery_evidence(current)[0], "delivery_evidence_conflict")
        with self.assertRaises(ImageSubmissionUnconfirmed):
            recover_checkpoint(current, "a" * 64)
        self.assertEqual((external / "unrelated").read_bytes(), b"unchanged")

    def test_unsafe_regular_file_attempt_is_not_skipped(self):
        from creatoros.delivery.image_recovery import ImageSubmissionUnconfirmed, recover_checkpoint
        from creatoros.delivery.video_receipt import VideoRecoveryEvidenceConflict, _recover_work
        (self.root / "attempt-999").write_bytes(b"evidence")
        current = self.root / "attempt-1000"
        current.mkdir()
        self.assertEqual(inspect_recovery_evidence(current)[0], "delivery_evidence_conflict")
        with self.assertRaises(ImageSubmissionUnconfirmed):
            recover_checkpoint(current, "a" * 64)
        with self.assertRaises(VideoRecoveryEvidenceConflict):
            _recover_work(current, "a" * 64)

    def test_very_large_attempt_numbers_have_no_fixed_width_limit(self):
        from creatoros.delivery.image_recovery import ImageSubmissionUnconfirmed, recover_checkpoint
        self.first.rename(self.root / "attempt-999999999999999999999999")
        previous = self.root / "attempt-999999999999999999999999"
        (previous / SUBMISSION_INTENT).write_text("unknown", encoding="utf-8")
        current = self.root / "attempt-1000000000000000000000000"
        current.mkdir()
        self.assertEqual(inspect_recovery_evidence(current)[0], "image_delivery_unconfirmed")
        with self.assertRaises(ImageSubmissionUnconfirmed):
            recover_checkpoint(current, "a" * 64)

    @unittest.skipUnless(__import__("os").name == "nt", "Windows junction")
    def test_prior_attempt_junction_is_never_followed(self):
        import os
        import subprocess
        from creatoros.delivery.image_recovery import ImageSubmissionUnconfirmed, recover_checkpoint
        from creatoros.delivery.video_receipt import VideoRecoveryEvidenceConflict, _recover_work
        external = self.root / "external"
        external.mkdir()
        (external / "unrelated").write_bytes(b"unchanged")
        link = self.root / "attempt-999"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(external)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(os.rmdir, link)
        current = self.root / "attempt-1000"
        current.mkdir()
        self.assertEqual(inspect_recovery_evidence(current)[0], "delivery_evidence_conflict")
        with self.assertRaises(ImageSubmissionUnconfirmed):
            recover_checkpoint(current, "a" * 64)
        with self.assertRaises(VideoRecoveryEvidenceConflict):
            _recover_work(current, "a" * 64)
        self.assertEqual((external / "unrelated").read_bytes(), b"unchanged")


if __name__ == "__main__":
    unittest.main()
