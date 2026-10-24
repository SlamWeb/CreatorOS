"""Structured production regressions with real files and typed SDK fault injection.

The fake transport emits SDK image events; it never calls a model. Each test
uses a private temporary catalog and output tree, leaving formal data alone.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openai_codex import LocalImageInput, SkillInput, TextInput
from openai_codex.generated import v2_all as sdk_types
from openai_codex.models import Notification

from creatoros.integrations.codex import CodexProducerError, CodexSdkProducer
from creatoros.integrations.native_image_assets import VIEW, ImageAssetStore
from creatoros.integrations.native_production import CHECKPOINT, CompositionReview, load_checkpoint
from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.integrations.production_materials import MATERIALS, RESPONSE
from creatoros.integrations.skill_pair import snapshot_pair
from tests.smoke_native_production import NativeTransport, image, prepare_attempt, write_skill


def tree_bytes(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


class StructuredTransport:
    """Separate composition and delivery by schema, never schema presence alone."""

    def __init__(self, generated):
        self.generated = generated
        self.starts, self.resumes, self.calls, self.configs = [], [], [], []
        self.generated_count = 0
        self.turn_count = 0
        self.invalid_kind = None
        self.invalid_repair = False
        self.interrupt_production = False
        self.poison_index = False
        self.forge_envelope = False
        self.preview_pages = None
        self.interrupts = []

    def __call__(self, config):
        self.configs.append(config)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def thread_start(self, **options):
        thread_id = f"structured-thread-{len(self.starts) + 1}"
        self.starts.append(thread_id)
        return self.thread(thread_id)

    async def thread_resume(self, thread_id, **options):
        self.resumes.append(thread_id)
        return self.thread(thread_id)

    def thread(self, thread_id):
        async def turn(inputs, **controls):
            schema = controls.get("output_schema")
            if schema is None:
                raise AssertionError("New structured turns must supply output_schema")
            properties = schema.get("properties", {})
            if "status" in properties:
                phase = "composition"
            elif "artifacts" in properties:
                phase = "delivery_repair" if "只修交付结果" in inputs[0].text else "production"
            else:
                raise AssertionError("Unknown structured turn schema")
            self.turn_count += 1
            call = {"phase": phase, "inputs": inputs, "controls": controls,
                    "thread_id": thread_id, "turn_id": f"structured-turn-{self.turn_count}"}
            self.calls.append(call)
            return StructuredTurn(self, call)
        return SimpleNamespace(id=thread_id, turn=turn)

    @property
    def deliveries(self):
        return [call for call in self.calls if call["phase"] != "composition"]


class StructuredTurn:
    def __init__(self, harness, call):
        self.harness, self.call = harness, call
        self.id, self.thread_id = call["turn_id"], call["thread_id"]

    async def interrupt(self):
        self.harness.interrupts.append(self.id)

    def completed(self, status):
        return Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
            "threadId": self.thread_id, "turn": {"id": self.id, "status": status, "items": []}}))

    async def stream(self):
        harness, call = self.harness, self.call
        work = Path(call["controls"]["cwd"])
        if call["phase"] == "composition":
            response = {"status": "ready", "note": "Use the complete frozen content and visual materials."}
        else:
            if call["phase"] == "production":
                for number in range(2 if harness.poison_index else 1):
                    harness.generated_count += 1
                    source = harness.generated / self.thread_id / f"candidate-{harness.generated_count}.png"
                    image(source, (20 + number * 60, 80, 120))
                    yield Notification("item/completed", sdk_types.ItemCompletedNotification.model_validate({
                        "threadId": self.thread_id, "turnId": self.id, "completedAtMs": 1,
                        "item": {"type": "imageGeneration", "id": f"candidate-{harness.generated_count}",
                                 "status": "completed", "result": "", "savedPath": str(source)}}))
                if harness.interrupt_production:
                    yield self.completed("interrupted")
                    return
            # The host has consumed the preceding event and updated this view.
            assets = json.loads((work / VIEW).read_text(encoding="utf-8"))["images"]
            bundle = json.loads((work.parent / MATERIALS).read_text(encoding="utf-8"))
            selected = {"order": 1, "asset_id": assets[0]["asset_id"],
                        "image_prompt": "Actual prompt passed to the native image tool.",
                        "reference_ids": [bundle["resources"][0]["id"]],
                        "content": "The full final teaching text, preserved by the host."}
            if harness.invalid_kind and (call["phase"] == "production" or harness.invalid_repair):
                if harness.invalid_kind == "asset":
                    selected["asset_id"] = "img_" + "f" * 24
                elif harness.invalid_kind == "reference":
                    selected["reference_ids"] = ["res_999"]
                else:
                    raise AssertionError("Unknown fault injection")
            response = {"title": "Structured production", "text": "A draft publication caption.",
                        "hashtags": ["#test"], "complete": True, "artifacts": [selected]}
            if harness.poison_index and call["phase"] == "production":
                (work / "poison.md").write_text("Untrusted model-written preview", encoding="utf-8")
                poison = {"title": "Wrong preview", "text": "Do not accept", "complete": True,
                          "artifacts": [{"order": index, "asset_id": asset["asset_id"],
                              "image_prompt": "Untrusted preview prompt", "content_file": "poison.md"}
                              for index, asset in enumerate(assets, 1)]}
                (work / "delivery.json").write_text(json.dumps(poison), encoding="utf-8")
                # Cross at least one real 0.5-second ingest poll before final JSON.
                await asyncio.sleep(0.65)
                harness.preview_pages = len(json.loads((work.parent / CHECKPOINT).read_text())["pages"])
        text = json.dumps(response, ensure_ascii=False)
        call["response"] = response
        if harness.forge_envelope and call["phase"] == "production":
            checkpoint = json.loads((work.parent / CHECKPOINT).read_text(encoding="utf-8"))
            forged = json.loads(text)
            forged["title"] = "FORGED model-written envelope"
            forged["artifacts"][0]["content"] = "FORGED text absent from the actual final SDK response"
            (work / RESPONSE).write_text(json.dumps({
                "protocol": "structured-v1", "thread_id": self.thread_id,
                "input_digest": checkpoint["input_digest"], "turn_id": self.id,
                "phase": "production", "response": json.dumps(forged),
            }), encoding="utf-8")
        total = {"inputTokens": harness.turn_count * 100, "cachedInputTokens": 0,
                 "outputTokens": harness.turn_count * 20, "reasoningOutputTokens": 0,
                 "totalTokens": harness.turn_count * 120}
        yield Notification("thread/tokenUsage/updated", sdk_types.ThreadTokenUsageUpdatedNotification.model_validate({
            "threadId": self.thread_id, "turnId": self.id, "tokenUsage": {"last": total, "total": total}}))
        yield Notification("item/completed", sdk_types.ItemCompletedNotification.model_validate({
            "threadId": self.thread_id, "turnId": self.id, "completedAtMs": 1,
            "item": {"type": "agentMessage", "id": "final-" + self.id, "phase": "final_answer", "text": text}}))
        yield self.completed("completed")


class StructuredHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="structured-handoff-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.generated = self.root / "generated"
        self.catalog = ProducerSkillCatalog(self.root / "catalog", project_root=self.root)
        author = self.root / "authoring"
        write_skill(author / "mind", "local-mind", "Preserve the complete teaching content.")
        write_skill(author / "production", "local-maker", "Use the frozen visual reference.")
        image(author / "production/assets/character.png")
        (author / "production/assets/reference.md").write_text("A complete example, not an abstract summary.", encoding="utf-8")
        mind = self.catalog.register_local(author / "mind", role="mind")
        maker = self.catalog.register_local(author / "production", role="production")
        self.pair = snapshot_pair(self.catalog, mind["id"], maker["id"], native=True)
        self.producer = CodexSdkProducer(project_root=self.root, generated_images_root=self.generated, timeout_seconds=8)
        self.transport = StructuredTransport(self.generated)
        sdk = patch("openai_codex.AsyncCodex", side_effect=self.transport)
        self.sdk = sdk.start()
        self.addCleanup(sdk.stop)

    def directory(self, attempt=1, name="run"):
        return self.root / name / "revision-001" / f"attempt-{attempt:03d}"

    def produce(self, attempt=1, name="run"):
        return self.producer.produce_to(
            directory=self.directory(attempt, name), pack_id="run-r001", creator_id="creator-test",
            series_id="series-test", topic_id="topic-test", topic_title="Native production",
            topic_brief="A scoped smoke test", series_description="", audience="", composition=self.pair,
            skills_root=self.catalog.root, production_protocol="native-v1")

    def inventory(self, directory):
        checkpoint = load_checkpoint(directory)
        self.assertIsNotNone(checkpoint)
        store = ImageAssetStore(directory, self.generated, checkpoint.thread_id, checkpoint.input_digest)
        return store.read().assets

    def assert_structured_inputs(self, call, *, images):
        inputs = call["inputs"]
        self.assertFalse(any(isinstance(item, SkillInput) for item in inputs))
        self.assertEqual(sum(isinstance(item, LocalImageInput) for item in inputs), images)
        self.assertIsInstance(inputs[0], TextInput)
        self.assertNotIn(str(self.root), inputs[0].text, "The model should not have to copy long task paths")
        bundle = json.loads((Path(call["controls"]["cwd"]).parent / MATERIALS).read_text(encoding="utf-8"))
        for skill in bundle["skills"]:
            self.assertIn(skill["content"], inputs[0].text.replace("\\n", "\n"))
            self.assertTrue(skill["resource_root"].startswith("../skills/"))

    def test_new_attempt_inlines_frozen_skills_attaches_image_and_uses_schema(self):
        produced = self.produce()
        checkpoint = load_checkpoint(produced.directory)
        self.assertEqual(checkpoint.handoff_protocol, "structured-v1")
        self.assertTrue(checkpoint.turn_completed)
        self.assertEqual([call["phase"] for call in self.transport.calls], ["composition", "production"])
        for call in self.transport.calls:
            self.assert_structured_inputs(call, images=1)
        delivered = self.transport.deliveries[0]["response"]
        self.assertEqual(set(delivered["artifacts"][0]),
                         {"order", "asset_id", "image_prompt", "reference_ids", "content"})
        page = checkpoint.pages[0]
        self.assertEqual(page.content, delivered["artifacts"][0]["content"])
        self.assertEqual(page.asset_id, self.inventory(produced.directory)[0].asset_id)
        self.assertEqual(len(produced.pack.cards), 1)
        envelope = json.loads((produced.directory / "work" / RESPONSE).read_text(encoding="utf-8"))
        self.assertEqual(envelope["thread_id"], checkpoint.thread_id)
        self.assertEqual(envelope["input_digest"], checkpoint.input_digest)
        self.assertEqual(json.loads(envelope["response"]), delivered)

    def test_single_skill_also_uses_structured_handoff_without_composition_turn(self):
        maker = self.catalog.locate(self.pair.production.id)
        produced = self.producer.produce_to(
            directory=self.directory(), pack_id="single-r001", creator_id="creator-test",
            series_id="series-test", topic_id="topic-test", topic_title="Single Skill",
            skill_directory=maker, skill_name=self.pair.production.id, production_protocol="native-v1")
        checkpoint = load_checkpoint(produced.directory)
        self.assertEqual([call["phase"] for call in self.transport.calls], ["production"])
        self.assertEqual(checkpoint.handoff_protocol, "structured-v1")
        self.assertTrue(checkpoint.turn_completed)
        self.assertEqual(len(checkpoint.pages), 1)
        self.assert_structured_inputs(self.transport.calls[0], images=1)

    def test_model_written_delivery_is_ignored_even_between_poll_ticks(self):
        self.transport.poison_index = True
        produced = self.produce()
        checkpoint = load_checkpoint(produced.directory)
        self.assertEqual(self.transport.preview_pages, 0)
        self.assertEqual(len(self.inventory(produced.directory)), 2)
        self.assertEqual(len(checkpoint.pages), 1)
        self.assertEqual(checkpoint.delivery.title, "Structured production")
        self.assertEqual(len(self.transport.deliveries), 1)
        self.assertFalse(checkpoint.repair_attempted)

    def test_unknown_asset_or_reference_gets_only_one_repair_and_no_regeneration(self):
        for kind in ("asset", "reference"):
            with self.subTest(kind=kind):
                self.transport.invalid_kind, self.transport.invalid_repair = kind, True
                calls_before, generated_before = len(self.transport.deliveries), self.transport.generated_count
                with self.assertRaises(CodexProducerError) as raised:
                    self.produce(name=kind)
                self.assertEqual(raised.exception.error_type, "native_delivery_failed")
                calls = self.transport.deliveries[calls_before:]
                self.assertEqual([call["phase"] for call in calls], ["production", "delivery_repair"])
                self.assertEqual(self.transport.generated_count - generated_before, 1)
                self.assert_structured_inputs(calls[-1], images=0)
                checkpoint = load_checkpoint(self.directory(name=kind))
                self.assertTrue(checkpoint.repair_attempted)
                self.assertFalse(checkpoint.turn_completed)
                self.assertEqual(checkpoint.pages, [])
                source = self.directory(name=kind)
                before = tree_bytes(source)
                with patch("openai_codex.AsyncCodex", side_effect=AssertionError("Exhausted repair must stay offline")) as sdk:
                    with self.assertRaises(CodexProducerError):
                        self.produce(attempt=2, name=kind)
                sdk.assert_not_called()
                self.assertEqual(tree_bytes(source), before)

    def test_repair_can_select_the_existing_asset_without_another_image_turn(self):
        self.transport.invalid_kind = "reference"
        produced = self.produce()
        checkpoint = load_checkpoint(produced.directory)
        self.assertEqual([call["phase"] for call in self.transport.deliveries], ["production", "delivery_repair"])
        self.assertEqual(self.transport.generated_count, 1)
        self.assertEqual(len(checkpoint.pages), 1)
        self.assertTrue(checkpoint.turn_completed and checkpoint.repair_attempted)
        self.assert_structured_inputs(self.transport.deliveries[-1], images=0)

    def test_completed_response_with_host_index_write_failure_recovers_offline(self):
        from creatoros.integrations import production_materials

        write = production_materials.atomic_json

        def deny_index(path, value):
            if Path(path).name == "delivery.json":
                raise OSError("Injected host index write failure after completed SDK turn")
            return write(path, value)

        with patch.object(production_materials, "atomic_json", side_effect=deny_index):
            with self.assertRaises(CodexProducerError):
                self.produce()
        source = self.directory()
        checkpoint = load_checkpoint(source)
        self.assertEqual(len(self.transport.deliveries), 1, "Host disk failure must not spend a repair turn")
        self.assertEqual(self.transport.generated_count, 1)
        self.assertFalse(checkpoint.turn_completed)
        self.assertEqual(len(self.inventory(source)), 1)
        self.assertTrue((source / "work" / RESPONSE).is_file())
        before = tree_bytes(source)
        with patch("openai_codex.AsyncCodex", side_effect=AssertionError("Retained completed response must recover offline")) as sdk:
            produced = self.produce(attempt=2)
        sdk.assert_not_called()
        self.assertEqual(tree_bytes(source), before)
        self.assertTrue(load_checkpoint(produced.directory).turn_completed)
        self.assertEqual(produced.session.usage.input_tokens, 0)
        self.assertEqual(produced.session.usage.output_tokens, 0)
        self.assertEqual(len(produced.pack.cards), 1)

    def test_interrupted_image_without_final_response_resumes_only_repair(self):
        self.transport.interrupt_production = True
        with self.assertRaises(CodexProducerError) as raised:
            self.produce()
        self.assertEqual(raised.exception.error_type, "codex_interrupted")
        source = self.directory()
        checkpoint = load_checkpoint(source)
        self.assertEqual(checkpoint.pages, [])
        self.assertEqual(len(self.inventory(source)), 1)
        self.assertFalse((source / "work" / RESPONSE).exists())
        self.assertEqual(self.transport.interrupts, [], "SDK interrupted terminal must not be interrupted twice")
        before = tree_bytes(source)
        self.transport.interrupt_production = False
        produced = self.produce(attempt=2)
        self.assertEqual([call["phase"] for call in self.transport.deliveries], ["production", "delivery_repair"])
        self.assertEqual(self.transport.generated_count, 1)
        self.assertEqual(self.transport.resumes, [checkpoint.thread_id])
        self.assertEqual(tree_bytes(source), before)
        self.assertTrue(load_checkpoint(produced.directory).turn_completed)
        self.assert_structured_inputs(self.transport.deliveries[-1], images=0)

    def test_model_envelope_left_by_failed_host_write_requires_repair(self):
        from creatoros.integrations import production_materials

        self.transport.forge_envelope = True
        write = production_materials.atomic_write_bytes

        def deny_retained_response(path, value):
            if Path(path).name == RESPONSE:
                raise OSError("Injected host response write failure; model envelope remains")
            return write(path, value)

        with patch.object(production_materials, "atomic_write_bytes", side_effect=deny_retained_response):
            with self.assertRaises(CodexProducerError):
                self.produce()
        source = self.directory()
        checkpoint = load_checkpoint(source)
        self.assertIsNone(getattr(checkpoint, "response_sha256", None))
        self.assertEqual(len(self.inventory(source)), 1)
        self.assertEqual(len(self.transport.deliveries), 1)
        forged = json.loads((source / "work" / RESPONSE).read_text(encoding="utf-8"))
        self.assertEqual(json.loads(forged["response"])["title"], "FORGED model-written envelope")
        before = tree_bytes(source)
        self.transport.forge_envelope = False
        produced = self.produce(attempt=2)
        current = load_checkpoint(produced.directory)
        self.assertEqual([call["phase"] for call in self.transport.deliveries], ["production", "delivery_repair"],
                         "A model-written response without a host checksum must not finalize offline")
        self.assertEqual(self.transport.generated_count, 1)
        self.assertEqual(current.delivery.title, "Structured production")
        self.assertEqual(current.pages[0].content, "The full final teaching text, preserved by the host.")
        self.assertEqual(tree_bytes(source), before)
        self.assertTrue(current.turn_completed and current.repair_attempted)

    def test_completed_response_envelope_tampering_is_rejected(self):
        from creatoros.integrations.production_materials import accept_response

        produced = self.produce()
        checkpoint = load_checkpoint(produced.directory)
        bundle = json.loads((produced.directory / MATERIALS).read_text(encoding="utf-8"))
        envelope_path = produced.directory / "work" / RESPONSE
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        forged = json.loads(envelope["response"])
        forged["title"] = "Tampered after completed SDK delivery"
        forged["artifacts"][0]["content"] = "Tampered final text"
        envelope["response"] = json.dumps(forged)
        envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
        # The byte change must be caught before host content or delivery changes.
        before = tree_bytes(produced.directory)
        with self.assertRaises(ValueError):
            accept_response(produced.directory, checkpoint, self.generated, bundle)
        self.assertIsNone(load_checkpoint(produced.directory))
        with patch("openai_codex.AsyncCodex", side_effect=AssertionError("Tampered evidence must stay offline")) as sdk:
            with self.assertRaisesRegex(ValueError, "拒绝自动恢复"):
                self.produce(attempt=2)
        sdk.assert_not_called()
        self.assertEqual(tree_bytes(produced.directory), before)

    def test_legacy_checkpoint_without_marker_keeps_file_delivery_protocol(self):
        source = self.directory()
        checkpoint = prepare_attempt(source, self.pair, self.catalog)
        checkpoint.composition_review = CompositionReview(status="ready", note="Previously checked legacy pair")
        raw = checkpoint.model_dump()
        raw.pop("handoff_protocol", None)  # This is genuinely old persisted data.
        (source / CHECKPOINT).write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(load_checkpoint(source).handoff_protocol, "file-v1")
        before = tree_bytes(source)
        NativeTransport.reset(self.generated, "success")
        with patch("openai_codex.AsyncCodex", NativeTransport):
            produced = self.produce(attempt=2)
        current = load_checkpoint(produced.directory)
        self.assertEqual(current.handoff_protocol, "file-v1")
        self.assertEqual(NativeTransport.resumes, [checkpoint.thread_id])
        self.assertTrue(current.turn_completed)
        self.assertNotIn("output_schema", NativeTransport.calls[0][2])
        self.assertTrue(any(isinstance(item, SkillInput) for item in NativeTransport.calls[0][1]))
        self.assertFalse((produced.directory / MATERIALS).exists())
        self.assertEqual(tree_bytes(source), before)


if __name__ == "__main__":
    unittest.main()
