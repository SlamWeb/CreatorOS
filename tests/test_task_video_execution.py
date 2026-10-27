"""Real local MP4 inspection plus injected SDK failures; no model-quality claim."""
import json
import math
import os
import shutil
import subprocess
import unittest
import zipfile
import wave
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from creatoros.integrations.codex import CodexSdkProducer, CodexUsage, CodexProducerError
from creatoros.integrations.media_tools import media_tool, probe_media
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest
from creatoros.integrations.video_production import (VIDEO_CHECKPOINT, VideoCheckpoint, VideoDelivery,
    _identity, produce_video, validate_video_artifact)
from creatoros.integrations.visual_production import atomic_json
from creatoros.runs.task_contracts import TaskExecutionSpec, TaskSkill, TaskMaterial
from creatoros.runs.task_execution import file_sha256, freeze_execution_spec
from creatoros.runs import ContentRunService, ContentRunExecutionError
from creatoros.storage import (Database, Creator, CreatorPlatform, Series, Topic, TopicSource,
                              ContentRunStatus, upgrade_database)
from creatoros.integrations.producer_skills import skills_root_for


class TaskVideoExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            media_tool("ffmpeg")
            media_tool("ffprobe")
        except ValueError as error:
            raise unittest.SkipTest(str(error))

    def setUp(self):
        temporary = TemporaryDirectory(prefix="creatoros-video-adapter-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog = ProducerSkillCatalog(self.root / "catalog", project_root=self.root)
        source = self.root / "source-skill"
        source.mkdir()
        (source / "SKILL.md").write_text("---\nname: video-test\ndescription: local fixture\ncreatoros-outputs: [video]\n---\nOriginal skill body.\n", encoding="utf-8")
        record = self.catalog.register_local(source, role="production")
        self.clip = self.root / "source.mp4"
        self.audio = self.root / "source.wav"
        raw = self.root / "source.rgb"
        raw.write_bytes(bytes([0, 0, 255]) * 32 * 48 * 4)
        with wave.open(str(self.audio), "wb") as output:
            output.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
            output.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 44100)))
                                       for i in range(17640)))
        subprocess.run([media_tool("ffmpeg"), "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
                        "-video_size", "32x48", "-framerate", "10", "-i", str(raw), "-i", str(self.audio), "-t", "0.4",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(self.clip)], check=True)
        archive = self.root / "images.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("readme.txt", "input fixture, no image-generation claim")
        self.spec = TaskExecutionSpec(kind="video", skills=[TaskSkill(skill_id=record["id"],
            digest=_digest(Path(record["local_path"])), role="primary")], materials=[
                TaskMaterial(id="images", kind="images_zip", path=str(archive), sha256=file_sha256(archive), name="images.zip"),
                TaskMaterial(id="bgm", kind="audio", path=str(self.audio), sha256=file_sha256(self.audio), name="bgm.wav")])
        self.run = self.root / "run"
        self.spec = freeze_execution_spec(self.spec, self.catalog, self.run / "skill-snapshot")
        self.producer = CodexSdkProducer(project_root=self.root, generated_images_root=self.root / "generated", timeout_seconds=5)
        self.calls = []

    def produce(self, attempt=1):
        return produce_video(self.producer, directory=self.run / "revision-001" / f"attempt-{attempt:03d}",
            pack_id="run-r001", creator_id="creator", series_id="series", topic_id="topic", topic_title="Title",
            topic_brief="brief", series_description="", audience="", revision_instruction=None,
            execution_spec=self.spec, skills_root=self.catalog.root)

    async def completed_transport(self, producer, directory, spec, payload, checkpoint, request):
        self.calls.append(checkpoint.thread_id if checkpoint else None)
        shutil.copyfile(self.clip, directory / "work" / "finished.mp4")
        saved = VideoCheckpoint(input_digest=_identity(spec, payload), thread_id="video-thread",
            turn_completed=True, response=VideoDelivery(title="Title", text="Caption", video_path="finished.mp4",
            complete=True).model_dump_json(), usage=CodexUsage(input_tokens=10))
        atomic_json(directory / VIDEO_CHECKPOINT, saved)
        return saved

    def test_real_mp4_acceptance_and_tamper_rejection(self):
        with patch("creatoros.integrations.video_production._execute_video", self.completed_transport):
            result = self.produce()
        checked = validate_video_artifact(result.directory, self.spec)
        self.assertEqual(checked.card_count, 0)
        self.assertEqual(checked.images, [])
        self.assertEqual((checked.video.width, checked.video.height), (32, 48))
        self.assertTrue(checked.video.has_audio)
        self.assertGreater(checked.video.duration_seconds, 0)
        (result.directory / "video.mp4").write_bytes(b"broken")
        with self.assertRaisesRegex(ValueError, "已变化"):
            validate_video_artifact(result.directory, self.spec)

    def test_repeat_playback_checks_bytes_without_redecoding_full_video(self):
        from creatoros.integrations.video_production import _decoded_video_metadata
        from creatoros.integrations.media_tools import decode_media
        with patch("creatoros.integrations.video_production._execute_video", self.completed_transport):
            result = self.produce()
        _decoded_video_metadata.cache_clear()
        with patch("creatoros.integrations.video_production.decode_media", wraps=decode_media) as decoder:
            first = validate_video_artifact(result.directory, self.spec)
            again = validate_video_artifact(result.directory, self.spec)
            self.assertEqual(first, again)
            self.assertEqual(decoder.call_count, 1)
            (result.directory / "video.mp4").write_bytes(b"changed after cached decode")
            with self.assertRaisesRegex(ValueError, "已变化"):
                validate_video_artifact(result.directory, self.spec)
            self.assertEqual(decoder.call_count, 1)

    def test_completed_sdk_receipt_recovers_offline_after_host_io_failure(self):
        async def host_fault(*args):
            await self.completed_transport(*args)
            raise OSError("injected host persistence failure")
        with patch("creatoros.integrations.video_production._execute_video", host_fault):
            with self.assertRaises(CodexProducerError) as failure:
                self.produce()
            self.assertEqual(failure.exception.error_type, "video_delivery_failed")
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("must recover offline")):
            result = self.produce(attempt=2)
        self.assertEqual(result.session.thread_id, "video-thread")
        self.assertTrue(validate_video_artifact(result.directory, self.spec).video.has_audio)

    def test_interrupted_attempt_preserves_thread_and_workspace(self):
        async def interrupted(producer, directory, spec, payload, checkpoint, request):
            (directory / "work" / "edit-plan.json").write_text("frozen draft", encoding="utf-8")
            atomic_json(directory / VIDEO_CHECKPOINT, VideoCheckpoint(input_digest=_identity(spec, payload),
                thread_id="video-interrupted"))
            raise RuntimeError("injected transport failure")
        with patch("creatoros.integrations.video_production._execute_video", interrupted):
            with self.assertRaises(RuntimeError):
                self.produce()
        async def resumed(*args):
            self.assertEqual(args[4].thread_id, "video-interrupted")
            self.assertEqual((args[1] / "work" / "edit-plan.json").read_text(), "frozen draft")
            return await self.completed_transport(*args)
        with patch("creatoros.integrations.video_production._execute_video", resumed):
            result = self.produce(attempt=2)
        self.assertEqual(self.calls, ["video-interrupted"])
        validate_video_artifact(result.directory, self.spec)

    def test_skill_and_material_digest_frozen_before_queue(self):
        selected = Path(self.spec.skills[0].path)
        self.assertIn("Original skill body", (selected / "SKILL.md").read_text())
        self.assertNotIn("path", self.spec.public_dict()["skills"][0])
        self.assertNotIn("path", self.spec.public_dict()["materials"][0])
        Path(self.spec.materials[0].path).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            freeze_execution_spec(self.spec, self.catalog, self.root / "copy", trusted_skill_roots=[self.run])

    def test_run_lifecycle_projection_revision_and_fresh_use_same_core(self):
        from creatoros.web.artifacts import StudioArtifacts
        url = "sqlite:///" + (self.root / "video.db").as_posix()
        upgrade_database(url)
        database = Database(url)
        self.addCleanup(database.close)
        catalog = ProducerSkillCatalog(skills_root_for(database), project_root=self.root)
        record = catalog.register_local(self.root / "source-skill", role="production")
        spec = self.spec.model_copy(update={"skills": [TaskSkill(skill_id=record["id"],
            digest=_digest(Path(record["local_path"])), role="primary")],
            "materials": [item.model_copy(update={"path": str(self.root / ("images.zip" if item.kind == "images_zip" else "source.wav"))})
                          for item in self.spec.materials]})
        with database.session() as session:
            session.add(Creator(id="creator", display_name="Test", platform=CreatorPlatform.XIAOHONGSHU))
            session.add(Series(id="series", creator_id="creator", name="Series", description="", audience="", skill_name="knowledge-to-carousel"))
            session.add(Topic(id="topic", series_id="series", title="Title", brief="brief", source=TopicSource.MANUAL, position=1))
        service = ContentRunService(database, producer_factory=lambda: self.producer, output_root=self.root / "outputs")
        run = service._create("topic", execution_spec=spec)
        async def host_fault(*args):
            await self.completed_transport(*args)
            raise OSError("injected IO after completed SDK turn")
        with patch("creatoros.integrations.video_production._execute_video", host_fault):
            with self.assertRaises(ContentRunExecutionError):
                service.execute(run.id)
        failed = service.get(run.id)
        self.assertTrue(failed.retryable)
        self.assertEqual(failed.error_type, "video_delivery_failed")
        with patch("creatoros.integrations.video_production._execute_video", side_effect=AssertionError("resume must finalize offline")):
            result = service.execute(run.id)
        current = service.get(run.id)
        self.assertEqual(current.status, ContentRunStatus.AWAITING_APPROVAL)
        artifacts = StudioArtifacts(database, service.output_root)
        from creatoros.web.queries import StudioQueryService
        detail = StudioQueryService(database, artifacts=artifacts).get_run(run.id)
        self.assertEqual(detail.task_kind, "video")
        self.assertNotIn("path", detail.input_snapshot["execution_spec"]["materials"][0])
        revision = service.get_active_revision(run.id)
        view = artifacts.projection(run.id, revision.id)
        self.assertTrue(view["artifact_available"])
        self.assertEqual(view["cards"], [])
        self.assertTrue(view["video"].url.endswith("checksum=" + view["video"].sha256))
        self.assertTrue(artifacts.video(run.id, revision.id, digest=revision.artifact_digest,
                                       checksum=view["video"].sha256).is_file())
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from creatoros.web.run_routes import review_routes
        app = FastAPI()
        app.include_router(review_routes(service, None, artifacts, None))
        with TestClient(app) as client:
            playback = client.get(view["video"].url, headers={"Range": "bytes=0-31"})
            self.assertEqual(playback.status_code, 206)
            self.assertEqual(len(playback.content), 32)
            self.assertEqual(playback.headers["content-type"], "video/mp4")
            download = client.get(f"/api/runs/{run.id}/revisions/{revision.id}/download?digest={revision.artifact_digest}")
            self.assertEqual(download.status_code, 200)
            self.assertIn("creatoros-video.mp4", download.headers["content-disposition"])
        second = service.request_revision(run.id, "Shorten ending", expected_version=current.version)
        spec2 = TaskExecutionSpec.model_validate(second.production_input_json["execution_spec"])
        self.assertTrue(any(item.kind == "video" and item.id == "previous-video" for item in spec2.materials))
        with patch("creatoros.integrations.video_production._execute_video", self.completed_transport):
            second_result = service.execute(run.id)
        self.assertEqual(service.get(run.id).producer_thread_id, "video-thread")
        self.assertIsNone(json.loads((Path(second_result.artifact_directory) / "production_request.txt").read_text()).get("previous_pages"))
        fresh = service.restart_fresh(run.id, expected_version=service.get(run.id).version, request_id="fresh-video")
        self.assertNotEqual(fresh.id, run.id)
        self.assertIsNone(fresh.producer_thread_id)
        self.assertEqual(fresh.input_snapshot_json["execution_spec"]["kind"], "video")

    def test_real_sdk_collector_with_local_transport(self):
        from tests.smoke_native_production import Turn, NativeTransport
        NativeTransport.reset(self.root / "generated")
        clip = self.clip
        starts, closes = [], []
        class Transport:
            def __init__(self, config):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                closes.append(True)
            async def thread_start(self, **options):
                starts.append(True)
                class Thread:
                    id = "video-sdk-thread"
                    async def turn(self, inputs, **controls):
                        shutil.copyfile(clip, Path(controls["cwd"]) / "rendered.mp4")
                        return Turn(self.id, VideoDelivery(title="SDK", text="Local transport", complete=True,
                            video_path="rendered.mp4").model_dump_json())
                return Thread()
        with patch("openai_codex.AsyncCodex", Transport):
            result = self.produce()
        self.assertEqual(result.session.thread_id, "video-sdk-thread")
        self.assertEqual(starts, [True])
        self.assertEqual(closes, [True])
        validate_video_artifact(result.directory, self.spec)


class TaskImageExecutionTests(unittest.TestCase):
    def test_primary_supporting_originals_use_native_image_protocol(self):
        from tests.test_structured_handoff import StructuredTransport
        from creatoros.integrations.production_materials import MATERIALS
        from creatoros.runs.artifacts import validate_artifact
        from creatoros.runs.models import ContentRunInput
        with TemporaryDirectory(prefix="creatoros-task-images-") as temporary:
            root = Path(temporary)
            catalog = ProducerSkillCatalog(root / "catalog", project_root=root)
            selected = []
            for role, kind, body in (("primary", "production", "UNMERGED PRIMARY BODY"),
                                     ("supporting", "mind", "UNMERGED SUPPORTING BODY")):
                source = root / role
                source.mkdir()
                (source / "SKILL.md").write_text(f"---\nname: image-{role}\ndescription: fixture\ncreatoros-outputs: [image]\n---\n{body}\n", encoding="utf-8")
                (source / "reference.txt").write_text("reference", encoding="utf-8")
                record = catalog.register_local(source, role=kind)
                selected.append(TaskSkill(skill_id=record["id"], digest=_digest(Path(record["local_path"])), role=role))
            spec = TaskExecutionSpec(kind="image", skills=selected)
            run = root / "run"
            spec = freeze_execution_spec(spec, catalog, run / "skill-snapshot")
            snapshot = ContentRunInput(production_protocol="task-v1", execution_spec=spec, creator_id="creator",
                series_id="series", series_name="Series", topic_id="topic", topic_title="Task")
            self.assertIsNone(snapshot.skill_name)
            transport = StructuredTransport(root / "generated")
            producer = CodexSdkProducer(project_root=root, generated_images_root=root / "generated", timeout_seconds=10)
            with patch("openai_codex.AsyncCodex", transport):
                result = producer.produce_to(directory=run / "revision-001" / "attempt-001", pack_id="pack",
                    creator_id="creator", series_id="series", topic_id="topic", topic_title="Task",
                    execution_spec=spec, skills_root=catalog.root, production_protocol="task-v1")
            checked = validate_artifact(result.directory, production_protocol="task-v1", execution_spec=spec)
            self.assertEqual(checked.card_count, 1)
            self.assertIsNone(checked.video)
            bundle = json.loads((result.directory / MATERIALS).read_text(encoding="utf-8"))
            self.assertIn("UNMERGED PRIMARY BODY", bundle["skills"][0]["content"])
            self.assertIn("UNMERGED SUPPORTING BODY", bundle["skills"][1]["content"])
            self.assertEqual([skill["role"] for skill in bundle["skills"]], ["01-primary", "02-supporting"])


if __name__ == "__main__":
    unittest.main()
