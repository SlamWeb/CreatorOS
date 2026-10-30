"""Real MP4 file contracts with isolated, explicitly injected SDK receipts."""
import shutil
import subprocess
import unittest
from unittest.mock import patch

from creatoros.integrations.codex import CodexProducerError
from creatoros.integrations.media_tools import media_tool
from creatoros.integrations.video_production import (
    VIDEO_CHECKPOINT, VideoDelivery, validate_video_artifact,
)
from creatoros.integrations.visual_production import atomic_json
from tests import test_task_video_execution as fixtures


class VideoDeliveryContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TaskVideoExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def deliver(self, *, path="finished.mp4", mutate=None, attempt=1):
        async def transport(*args):
            checkpoint = await self.fixture.completed_transport(*args)
            if mutate:
                mutate(args[1] / "work" / "finished.mp4")
            receipt = VideoDelivery.model_validate_json(checkpoint.response)
            receipt.video_path = path
            checkpoint.response = receipt.model_dump_json()
            atomic_json(args[1] / VIDEO_CHECKPOINT, checkpoint)
            return checkpoint
        with patch("creatoros.integrations.video_production._execute_video", transport):
            return self.fixture.produce(attempt=attempt)

    def test_valid_mp4_is_copied_and_decode_verified(self):
        result = self.deliver()
        checked = validate_video_artifact(result.directory, self.fixture.spec)
        self.assertEqual(checked.video.width, 32)
        self.assertEqual(checked.video.height, 48)
        self.assertTrue(checked.video.has_audio)
        self.assertEqual((result.directory / "video.mp4").read_bytes(), self.fixture.clip.read_bytes())

    def test_missing_path_preserves_render_and_refuses_manifest(self):
        with self.assertRaises(CodexProducerError):
            self.deliver(path="missing.mp4")
        root = self.fixture.run / "revision-001/attempt-001"
        self.assertTrue((root / "work/finished.mp4").exists())
        self.assertFalse((root / "video_artifact.json").exists())

    def test_path_escape_is_rejected(self):
        with self.assertRaises(CodexProducerError):
            self.deliver(path="../../source.mp4")

    def test_absolute_path_is_rejected(self):
        with self.assertRaises(CodexProducerError):
            self.deliver(path=str(self.fixture.clip))

    def test_nested_unicode_relative_path_is_accepted(self):
        def nested(path):
            target = path.parent / "成片" / "最终视频.mp4"
            target.parent.mkdir()
            shutil.copyfile(path, target)
        result = self.deliver(path="成片/最终视频.mp4", mutate=nested)
        self.assertTrue(validate_video_artifact(result.directory, self.fixture.spec).video.has_audio)

    def test_fake_mp4_extension_does_not_pass(self):
        with self.assertRaises(CodexProducerError):
            self.deliver(mutate=lambda path: path.write_text("not a video"))

    def test_missing_audio_track_is_rejected(self):
        def strip_audio(path):
            silent = path.with_name("silent.mp4")
            subprocess.run([media_tool("ffmpeg"), "-v", "error", "-i", str(path),
                            "-an", "-c:v", "copy", str(silent)], check=True)
            shutil.copyfile(silent, path)
        with self.assertRaisesRegex(CodexProducerError, "音轨"):
            self.deliver(mutate=strip_audio)

    def test_completed_receipt_restores_offline_without_touching_old_attempt(self):
        result = self.deliver()
        before = {p.relative_to(result.directory): p.read_bytes()
                  for p in result.directory.rglob("*") if p.is_file()}
        with patch("creatoros.integrations.video_production._execute_video",
                   side_effect=AssertionError("must not render again")):
            restored = self.fixture.produce(attempt=2)
        self.assertEqual((restored.directory / "video.mp4").read_bytes(), self.fixture.clip.read_bytes())
        self.assertEqual(before, {p.relative_to(result.directory): p.read_bytes()
                                 for p in result.directory.rglob("*") if p.is_file()})

    @unittest.expectedFailure
    def test_invalid_completed_receipt_can_be_repaired_on_explicit_retry(self):
        """Known bug: SDK completed is confused with a reusable valid delivery."""
        with self.assertRaises(CodexProducerError):
            self.deliver(path="missing.mp4")
        # A bounded correction should be allowed to point at the existing render.
        # Today _execute_video is never reached because turn_completed is true.
        repaired = self.deliver(attempt=2)
        validate_video_artifact(repaired.directory, self.fixture.spec)


if __name__ == "__main__":
    unittest.main()
