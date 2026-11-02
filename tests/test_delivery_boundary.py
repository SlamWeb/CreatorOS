"""Delivery acceptance can verify producer evidence without starting a producer."""
from __future__ import annotations

import base64
import builtins
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests import test_structured_handoff as image_fixtures
from tests import test_task_video_execution as video_fixtures


def without_run_or_producer_imports():
    """Fail if acceptance calls back into Run lifecycle or production adapters."""
    original_import = builtins.__import__

    def checked(name, globals=None, locals=None, fromlist=(), level=0):
        if level and globals and globals.get("__package__"):
            absolute = importlib.util.resolve_name("." * level + name, globals["__package__"])
        else:
            absolute = name
        forbidden = (
            "creatoros.runs",
            "creatoros.integrations.native_production",
            "creatoros.integrations.video_production",
        )
        if any(absolute == module or absolute.startswith(module + ".") for module in forbidden):
            raise AssertionError(f"delivery acceptance imported execution module {absolute}")
        return original_import(name, globals, locals, fromlist, level)

    return patch("builtins.__import__", checked)


class DeliveryBoundaryTests(unittest.TestCase):
    def test_import_does_not_load_lifecycle_or_production_adapters(self):
        script = """
import sys
from creatoros.delivery import validate_artifact
from creatoros.delivery.image import ingest
from creatoros.delivery.image_recovery import recover_checkpoint
from creatoros.delivery.video_receipt import VideoSubmission
for name in ('creatoros.runs', 'creatoros.integrations.native_production',
             'creatoros.integrations.video_production'):
    assert name not in sys.modules, name
"""
        subprocess.run([sys.executable, "-c", script], check=True)

    def test_historical_native_v1_digest_is_unchanged(self):
        # A fixed pre-refactor file-v1 receipt, including its original CRLF bytes.
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGOUNPJmYGBgYgADAAcAAJrwlYGnAAAAAElFTkSuQmCC"
        )
        item = {"order": 1, "asset_id": None, "source_image_path": "legacy-source.png",
                "image_prompt": "original prompt", "reference_assets": [], "content_file": None}
        checkpoint = {
            "protocol": "native-v1", "handoff_protocol": "file-v1", "response_sha256": None,
            "submission_sha256": None, "submission_failures": 0,
            "input_digest": "9b092db892a115b46930421910354208dd7c66345e80261559ed0df018925241",
            "thread_id": "historical-thread",
            "skill_digests": {"local": "a078c9915f50b5b430ca693f645bcdc6a0838c1a1453de811ff73f9b400e8cdf"},
            "pages": [{**item, "image_path": "partial-images/01.png",
                       "sha256": "4c83f38f168697526260c243ff5809af9880b5a300815575ede01d0c2356b3d7",
                       "content": "", "warnings": []}],
            "delivery": {"title": "caption", "text": "body", "hashtags": [], "complete": True,
                         "artifacts": [item]},
            "turn_completed": True, "repair_attempted": False,
            "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                      "reasoning_output_tokens": 0},
            "preexisting_thread_images": {}, "composition_review": None,
            "prompt_provenance": "producer_reported_not_image_service_verified",
        }
        pack = {"pack_id": "run-r001", "creator_id": "creator", "series_id": "series",
                "topic_id": "topic", "topic_title": "Title", "skill_name": "local",
                "generated_at": "2026-01-01T00:00:00Z", "content_summary": "Summary",
                "cards": [{"order": 1, "kind": "cover", "headline": "Title", "image_path": "images/01.png"}],
                "publish_copy": {"title": "Title", "body": "Caption"}}
        with TemporaryDirectory(prefix="creatoros-native-golden-") as temporary:
            root = Path(temporary)
            for name in ("skills/local", "images", "partial-images"):
                (root / name).mkdir(parents=True)
            (root / "skills/local/SKILL.md").write_bytes(b"stable frozen skill\n")
            (root / "production_request.txt").write_bytes(b"stable frozen request\n")
            (root / "images/01.png").write_bytes(image)
            (root / "partial-images/01.png").write_bytes(image)
            (root / "social_content_pack.json").write_text(json.dumps(pack), encoding="utf-8")
            receipt = json.dumps(checkpoint, ensure_ascii=False, indent=2).replace("\n", "\r\n")
            (root / "native_checkpoint.json").write_bytes(receipt.encode("utf-8"))
            script = """
import sys
sys.path.insert(0, sys.argv[1])
from creatoros.delivery import validate_artifact
print(validate_artifact('.', production_protocol='native-v1').artifact_digest)
"""
            repo = Path(__file__).resolve().parents[1]
            output = subprocess.run([sys.executable, "-c", script, str(repo)], cwd=root,
                                    capture_output=True, text=True, check=True)
        self.assertEqual(output.stdout.strip(),
                         "2fde202328d7aad588aecdda8cf7aa002dbeff29910d9256e0889059bc6ddd4c")

    def test_native_image_acceptance_and_tampering_stay_within_delivery(self):
        from creatoros.delivery import validate_artifact

        fixture = image_fixtures.StructuredHandoffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result = fixture.produce()
        with without_run_or_producer_imports():
            checked = validate_artifact(result.directory, composition=fixture.pair,
                                        production_protocol="native-v1")
        self.assertEqual(checked.card_count, 1)
        self.assertEqual(len(checked.images), 1)
        self.assertIsNone(checked.video)
        Path(result.directory / checked.images[0].path).write_bytes(b"tampered image")
        with without_run_or_producer_imports(), self.assertRaises((ValueError, OSError)):
            validate_artifact(result.directory, composition=fixture.pair,
                              production_protocol="native-v1")

    def test_video_acceptance_and_tampering_stay_within_delivery(self):
        from creatoros.delivery import validate_artifact

        video_fixtures.TaskVideoExecutionTests.setUpClass()
        fixture = video_fixtures.TaskVideoExecutionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch("creatoros.integrations.video_production._execute_video", fixture.completed_transport):
            result = fixture.produce()
        with without_run_or_producer_imports():
            checked = validate_artifact(result.directory, production_protocol="task-v1",
                                        execution_spec=fixture.spec)
        self.assertEqual(checked.images, [])
        self.assertEqual(checked.card_count, 0)
        self.assertTrue(checked.video.has_audio)
        (result.directory / checked.video.path).write_bytes(b"tampered video")
        with without_run_or_producer_imports(), self.assertRaises(ValueError):
            validate_artifact(result.directory, production_protocol="task-v1",
                              execution_spec=fixture.spec)

    def test_completed_image_receipt_can_be_restored_without_a_producer(self):
        from creatoros.delivery.image import request_digest
        from creatoros.delivery.image_recovery import recover_checkpoint

        fixture = image_fixtures.StructuredHandoffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source = fixture.produce().directory
        before = image_fixtures.tree_bytes(source)
        target = fixture.directory(attempt=2)
        target.mkdir(parents=True)
        (target / "work").mkdir()
        shutil.copytree(source / "skills", target / "skills")
        shutil.copyfile(source / "production_request.txt", target / "production_request.txt")
        digest = request_digest(target)
        with without_run_or_producer_imports(), patch("openai_codex.AsyncCodex", side_effect=AssertionError("producer called")):
            restored = recover_checkpoint(target, digest, fixture.generated)
        self.assertTrue(restored.turn_completed)
        self.assertTrue(restored.delivery.complete)
        self.assertEqual(len(restored.pages), 1)
        self.assertEqual(Path(restored.pages[0].image_path).read_bytes(),
                         (source / "images/01.png").read_bytes())
        self.assertEqual(image_fixtures.tree_bytes(source), before)

    def test_video_submission_is_idempotent_without_a_producer(self):
        from creatoros.delivery.video import VideoCheckpoint
        from creatoros.delivery.video_receipt import VideoSubmission

        video_fixtures.TaskVideoExecutionTests.setUpClass()
        fixture = video_fixtures.TaskVideoExecutionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root / "received"
        (root / "work").mkdir(parents=True)
        shutil.copyfile(fixture.clip, root / "work/finished.mp4")
        checkpoint = VideoCheckpoint(input_digest="input", thread_id="thread", handoff_protocol="submit-tool-v1")
        submit = VideoSubmission(root, checkpoint)
        delivery = {"title": "Title", "text": "Caption", "video_path": "finished.mp4", "complete": True}
        with without_run_or_producer_imports():
            first = submit(delivery)
            replay = submit(delivery)
        self.assertTrue(first["accepted"])
        self.assertTrue(replay["reused"])
        self.assertEqual(first["receipt_id"], replay["receipt_id"])
        self.assertEqual((root / "received-video.mp4").read_bytes(), fixture.clip.read_bytes())


if __name__ == "__main__":
    unittest.main()
