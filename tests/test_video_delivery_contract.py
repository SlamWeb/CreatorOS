"""Real MP4 file contracts with isolated, explicitly injected SDK receipts."""
import shutil
import subprocess
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
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

    def test_invalid_completed_receipt_is_not_guessed_or_reexecuted_on_retry(self):
        """A wrong path cannot identify the intended file without explicit selection."""
        with self.assertRaises(CodexProducerError):
            self.deliver(path="missing.mp4")
        source = self.fixture.run / "revision-001/attempt-001"
        before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        with patch("creatoros.integrations.video_production._execute_video",
                   side_effect=AssertionError("plain retry must not call the SDK")):
            with self.assertRaises(CodexProducerError):
                self.fixture.produce(attempt=2)
        self.assertEqual(before, {p.relative_to(source): p.read_bytes()
                                  for p in source.rglob("*") if p.is_file()})
        self.assertFalse((self.fixture.run / "revision-001/attempt-002/video_artifact.json").exists())

    def test_completed_tool_turn_without_accepted_receipt_never_restarts_sdk(self):
        async def completed_without_submission(*args):
            checkpoint = await self.fixture.completed_transport(*args)
            checkpoint.handoff_protocol = "submit-tool-v1"
            checkpoint.response = None
            atomic_json(args[1] / VIDEO_CHECKPOINT, checkpoint)
            raise ValueError("completed turn did not submit an accepted video")

        with patch("creatoros.integrations.video_production._execute_video", completed_without_submission):
            with self.assertRaises(CodexProducerError):
                self.fixture.produce()
        source = self.fixture.run / "revision-001/attempt-001"
        before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        with patch("creatoros.integrations.video_production._execute_video",
                   side_effect=AssertionError("completed turn must not restart")) as sdk:
            with self.assertRaises(CodexProducerError):
                self.fixture.produce(attempt=2)
        sdk.assert_not_called()
        self.assertEqual(before, {p.relative_to(source): p.read_bytes()
                                  for p in source.rglob("*") if p.is_file()})

    def test_real_collector_marks_completed_without_submission_unconfirmed(self):
        from tests.smoke_native_production import NativeTransport, Turn

        NativeTransport.reset(self.fixture.root / "generated")
        clip = self.fixture.clip
        class Thread:
            id = "unconfirmed-video-thread"
            async def turn(self, inputs, **controls):
                shutil.copyfile(clip, Path(controls["cwd"]) / "rendered.mp4")
                return Turn(self.id, "No video was submitted.", turn_id="unconfirmed-turn")

        class Client:
            async def thread_start(self, **options):
                return Thread()

        @asynccontextmanager
        async def local_client(deadline, cancel, *, dynamic_tools):
            self.assertEqual(len(dynamic_tools), 1)
            yield Client()

        with patch("creatoros.integrations.video_production._production_client", local_client):
            with self.assertRaises(CodexProducerError) as error:
                self.fixture.produce()
        self.assertEqual(error.exception.error_type, "video_delivery_unconfirmed")
        old = self.fixture.run / "revision-001/attempt-001"
        self.assertTrue((old / "work/rendered.mp4").is_file())
        with patch("creatoros.integrations.video_production._production_client",
                   side_effect=AssertionError("must not start another SDK client")) as client:
            with self.assertRaises(CodexProducerError) as retry:
                self.fixture.produce(attempt=2)
        self.assertEqual(retry.exception.error_type, "video_delivery_unconfirmed")
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
