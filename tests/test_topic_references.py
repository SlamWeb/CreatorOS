"""Real local files and SDK input objects; no paid generation in this wiring test."""
import base64
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from openai_codex import LocalImageInput

from creatoros.integrations.codex import CodexSdkProducer
from creatoros.integrations.native_production import (
    evidence_files, load_checkpoint, produce_native, request_digest, safe_reference,
)
from creatoros.integrations.topic_references import (
    TopicReferenceStore, frozen_references, prepare_references, reference_prompt,
)
from creatoros.storage.database import Database
from tests.smoke_native_production import NativeTransport, create_pair


def encoded(color="blue"):
    output = io.BytesIO()
    Image.new("RGB", (24, 32), color).save(output, format="PNG")
    raw = output.getvalue()
    return raw, base64.b64encode(raw).decode()


class ReferencesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="topic-references-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = TopicReferenceStore(self.root / "fixture.sqlite")
        self.raw, data = encoded()
        self.upload = self.store.upload("series-a", "example.png", data)

    def freeze(self, **kwargs):
        return self.store.freeze("series-a", [self.upload["id"]], **kwargs)

    def test_dedupe_originals_and_isolated_database(self):
        _, data = encoded("red")
        other = self.store.upload("series-a", "other.png", data)
        ref = self.store.freeze("series-a", [other["id"], self.upload["id"], other["id"]], "保留完整要求")
        self.assertEqual([image.sha256 for image in ref.images], [other["id"], self.upload["id"]])
        self.assertEqual(Path(ref.images[1].path).read_bytes(), self.raw)
        self.assertEqual(self.store.load("series-a", ref.id), ref)
        self.assertEqual(self.store.freeze("series-a", [other["id"], self.upload["id"]], "保留完整要求"), ref)
        self.assertNotEqual(self.freeze().id, self.freeze(reference_presentation=True).id)
        with self.assertRaises(ValueError):
            self.store.load("series-b", ref.id)
        with self.assertRaises(ValueError):
            TopicReferenceStore(self.root / "other.sqlite").load("series-a", ref.id)
        with self.assertRaises(HTTPException):
            self.store.image("series-b", self.upload["id"])

    def test_database_object_supported(self):
        database = Database("sqlite:///" + (self.root / "db-object.sqlite").as_posix())
        self.addCleanup(database.close)
        self.assertNotEqual(TopicReferenceStore(database).root, self.store.root)

    def test_invalid_inputs(self):
        for ids in ([], [self.upload["id"]] * 21, [[]]):
            with self.assertRaises(ValueError):
                self.store.freeze("series-a", ids)
        with self.assertRaises(HTTPException):
            self.store.upload("series-a", "bad.png", base64.b64encode(b"not image").decode())
        with self.assertRaises(ValueError):
            self.freeze(instructions="a" * 3001)

    def test_prepare_is_immutable_and_validates_hash(self):
        ref = self.freeze(instructions="借鉴选题，不要换 IP")
        attempt = self.root / "attempt"
        attempt.mkdir()
        paths = prepare_references(ref, attempt)
        self.assertEqual(paths[0].read_bytes(), self.raw)
        self.assertEqual(prepare_references(ref, attempt), paths)
        self.assertEqual(frozen_references(attempt).id, ref.id)
        self.assertFalse((attempt / "skills").exists())
        self.assertIn("只借鉴内容", reference_prompt(ref, paths))
        self.assertIn(ref.instructions, reference_prompt(ref, paths))
        with self.assertRaises(ValueError):
            prepare_references(self.freeze(reference_presentation=True), attempt)
        paths[0].write_bytes(encoded("red")[0])
        with self.assertRaises(ValueError):
            prepare_references(ref, attempt)
        with self.assertRaises(ValueError):
            frozen_references(attempt)

    def test_original_manifest_and_image_tampering_rejected(self):
        ref = self.freeze()
        original = Path(ref.images[0].path)
        original.write_bytes(b"not an image")
        with self.assertRaises(ValueError):
            self.store.load("series-a", ref.id)
        with self.assertRaises(ValueError):
            prepare_references(ref, self.root / "new-attempt")

    def test_symlink_rejected(self):
        ref = self.freeze()
        path = Path(ref.images[0].path)
        backup = self.root / "external.png"
        backup.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(backup)
        except OSError:
            self.skipTest("host does not permit symbolic links")
        with self.assertRaises(ValueError):
            self.store.load("series-a", ref.id)


class ImageTransport(NativeTransport):
    image_calls = []

    def _thread(self, thread_id):
        thread = super()._thread(thread_id)
        original = thread.turn

        async def capture(inputs, **controls):
            images = [item for item in inputs if isinstance(item, LocalImageInput)]
            ImageTransport.image_calls.append((images, inputs[0].text, controls))
            self.assertions(images, inputs[0].text, controls)
            turn = await original([item for item in inputs if not isinstance(item, LocalImageInput)], **controls)
            delivery = Path(controls["cwd"]) / "delivery.json"
            if images and "output_schema" not in controls and delivery.exists():
                data = json.loads(delivery.read_text())
                data["artifacts"][0]["reference_assets"] = [item.path for item in images]
                delivery.write_text(json.dumps(data), encoding="utf-8")
            return turn

        thread.turn = capture
        return thread

    @staticmethod
    def assertions(images, prompt, controls):
        if "不得重新研究或重画" in prompt:
            assert not images, "index-only repair must not resend image inputs"
        else:
            assert len(images) == 1
            assert "完整用户要求：保持故事转折" in prompt
            assert "只借鉴内容" in prompt
            assert all(Path(item.path).is_file() and Path(item.path).parent.name == "references" for item in images)


class NativeReferencesTest(ReferencesTest):
    def production(self, directory, pair, catalog):
        return produce_native(self.producer, directory=directory, pack_id="run-reference", creator_id="creator-a",
            series_id="series-a", topic_id="topic-a", topic_title="A new topic", topic_brief="Do not lose my intent",
            series_description="", audience="", composition=pair, skills_root=catalog.root,
            task_references=self.ref.model_dump())

    def test_native_inputs_resume_digest_and_evidence(self):
        catalog, pair = create_pair(self.root)
        before = {p: p.read_bytes() for p in catalog.root.rglob("*") if p.is_file()}
        generated = self.root / "generated"
        self.producer = CodexSdkProducer(project_root=self.root, generated_images_root=generated, timeout_seconds=8)
        self.ref = self.freeze(instructions="完整用户要求：保持故事转折")
        NativeTransport.reset(generated)
        ImageTransport.image_calls = []
        directory = self.root / "revision-001" / "attempt-001"
        with patch("openai_codex.AsyncCodex", ImageTransport):
            self.production(directory, pair, catalog)
            self.assertEqual(len(ImageTransport.image_calls), 2)
            checkpoint = load_checkpoint(directory)
            self.assertIsNotNone(checkpoint)
            self.assertEqual(checkpoint.pages[0].reference_assets, ["references/reference-01.png"])
            self.assertIn(directory / "references/input.json", evidence_files(directory))
            self.production(directory.parent / "attempt-002", pair, catalog)
            self.assertEqual(len(ImageTransport.image_calls), 2, "completed delivery resumes offline")
        self.assertEqual(before, {p: p.read_bytes() for p in catalog.root.rglob("*") if p.is_file()})
        with self.assertRaises(ValueError):
            safe_reference(directory, Path(self.ref.images[0].path))
        (directory / "references/reference-01.png").write_bytes(encoded("red")[0])
        self.assertIsNone(load_checkpoint(directory))
        with self.assertRaises(ValueError):
            request_digest(directory)

    def test_index_repair_does_not_resend_images(self):
        catalog, pair = create_pair(self.root)
        generated = self.root / "generated"
        self.producer = CodexSdkProducer(project_root=self.root, generated_images_root=generated, timeout_seconds=8)
        self.ref = self.freeze(instructions="完整用户要求：保持故事转折")
        NativeTransport.reset(generated, "repair-success")
        ImageTransport.image_calls = []
        with patch("openai_codex.AsyncCodex", ImageTransport):
            self.production(self.root / "repair" / "attempt-001", pair, catalog)
        self.assertEqual([len(call[0]) for call in ImageTransport.image_calls], [1, 1, 0])
        self.assertEqual(sum(NativeTransport.generated.values()), 1)

    def test_single_skill_has_one_image_bearing_turn(self):
        catalog, pair = create_pair(self.root)
        generated = self.root / "generated"
        producer = CodexSdkProducer(project_root=self.root, generated_images_root=generated, timeout_seconds=8)
        NativeTransport.reset(generated)
        ImageTransport.image_calls = []
        ref = self.freeze(instructions="完整用户要求：保持故事转折")
        with patch("openai_codex.AsyncCodex", ImageTransport):
            producer.produce_to(directory=self.root / "single" / "attempt-001", pack_id="run-single",
                creator_id="creator-a", series_id="series-a", topic_id="topic-single", topic_title="Same reference",
                skill_name=pair.production.name, skill_directory=Path(pair.production.local_path),
                skill_digest=pair.production.digest, task_references=ref.model_dump(), production_protocol="native-v1")
        self.assertEqual(len(ImageTransport.image_calls), 1)
        self.assertNotIn("output_schema", ImageTransport.image_calls[0][2])


if __name__ == "__main__":
    unittest.main()
