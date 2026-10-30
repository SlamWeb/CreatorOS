"""File-reference delivery contract; real isolated PNGs and typed SDK events.

SDK events/final responses are injected at the adapter boundary. No model,
production database, existing output, or running service is touched.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from openai_codex.generated import v2_all as sdk_types
from openai_codex.models import Notification

from creatoros.integrations.native_image_assets import ImageAssetStore
from creatoros.integrations.native_production import Checkpoint, structured_prompt
from creatoros.integrations.production_materials import FileDelivery, accept_response, retain_response
from tests.smoke_native_production import image


class FileDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="file-delivery-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "attempt-001"
        (self.directory / "work").mkdir(parents=True)
        self.generated = self.root / "generated"
        self.checkpoint = Checkpoint(input_digest="a" * 64, thread_id="file-delivery-thread",
                                     skill_digests={}, handoff_protocol="structured-v1")
        self.store = ImageAssetStore(self.directory, self.generated,
                                    self.checkpoint.thread_id, self.checkpoint.input_digest)
        self.store.initialize()
        self.store.expected_turn_id = "turn-1"
        self.bundle = {"resources": []}

    def receive(self, name, color):
        source = self.generated / self.checkpoint.thread_id / f"{name}.png"
        image(source, color)
        event = sdk_types.ItemCompletedNotification.model_validate({
            "threadId": self.checkpoint.thread_id, "turnId": "turn-1", "completedAtMs": 1,
            "item": {"type": "imageGeneration", "id": name, "status": "completed",
                     "result": "", "savedPath": str(source)},
        })
        asset = self.store.observe(Notification("item/completed", event))
        return asset, source

    @staticmethod
    def page(order, **reference):
        return {"order": order, "image_prompt": f"Actual prompt {order}",
                "reference_ids": [], "content": f"Page {order}", **reference}

    def accept(self, pages):
        response = {"title": "Two scenes", "text": "Caption", "hashtags": [],
                    "complete": True, "artifacts": pages}
        retain_response(self.directory, self.checkpoint,
                        SimpleNamespace(id="turn-1", final_response=json.dumps(response)), "production")
        (self.directory / "worker_receipt.json").write_text(json.dumps({
            "thread_id": self.checkpoint.thread_id,
            "turns": [{"id": "turn-1", "phase": "production", "status": "completed"}],
        }), encoding="utf-8")
        return accept_response(self.directory, self.checkpoint, self.generated, self.bundle)

    def assert_no_delivery(self):
        self.assertFalse((self.directory / "work/delivery.json").exists())
        self.assertEqual(list((self.directory / "work").glob("host-content-*.md")), [])

    def test_final_order_can_reverse_generation_order_without_copying_ids(self):
        first, first_path = self.receive("first-generated", (10, 80, 120))
        second, second_path = self.receive("second-generated", (90, 30, 10))
        inventory_before = self.store.path.read_bytes()
        # File paths alone are sufficient; the host supplies its own internal IDs.
        delivered = self.accept([self.page(1, source_image_path=str(second_path)),
                                 self.page(2, source_image_path=str(first_path))])
        self.assertEqual([entry.asset_id for entry in delivered.artifacts],
                         [second.asset_id, first.asset_id])
        self.assertEqual([entry.order for entry in delivered.artifacts], [1, 2])
        self.assertEqual(self.store.path.read_bytes(), inventory_before)
        self.assertEqual((self.directory / "work/host-content-001.md").read_text(), "Page 1")

    def test_model_schema_requests_paths_and_order_without_internal_ids(self):
        schema = FileDelivery.model_json_schema()
        reference = schema["properties"]["artifacts"]["items"]["$ref"].split("/")[-1]
        artifact = schema["$defs"][reference]
        self.assertIn("source_image_path", artifact["required"])
        self.assertIn("order", artifact["required"])
        self.assertNotIn("asset_id", artifact["properties"])
        self.assertFalse(artifact["additionalProperties"])
        prompt = structured_prompt(None)
        self.assertIn("source_image_path", prompt)
        self.assertIn("不需要读取generated-assets.json", prompt)
        self.assertIn("order从1连续编号", prompt)

    def test_recovery_uses_received_copy_after_original_is_gone(self):
        asset, source = self.receive("recover", (10, 80, 120))
        old_directory = self.directory
        old_files = {p.relative_to(old_directory): p.read_bytes()
                     for p in old_directory.rglob("*") if p.is_file()}
        self.directory = self.root / "attempt-002"
        (self.directory / "work").mkdir(parents=True)
        self.store = ImageAssetStore(self.directory, self.generated,
                                    self.checkpoint.thread_id, self.checkpoint.input_digest)
        self.store.restore_from(old_directory)
        source.unlink()
        delivered = self.accept([self.page(1, source_image_path=str(source))])
        self.assertEqual(delivered.artifacts[0].asset_id, asset.asset_id)
        _, received = self.store.resolve(asset.asset_id)
        self.assertTrue(received.is_file())
        self.assertEqual(old_files, {p.relative_to(old_directory): p.read_bytes()
                                    for p in old_directory.rglob("*") if p.is_file()})

    def test_wrong_path_is_rejected_before_any_partial_delivery(self):
        _, source = self.receive("valid", (10, 80, 120))
        with self.assertRaises(ValueError):
            self.accept([self.page(1, source_image_path=str(source)),
                         self.page(2, source_image_path=str(source.with_name("missing.png")))])
        self.assert_no_delivery()
        self.assertEqual(len(self.store.read().assets), 1)

    def test_unregistered_external_image_is_not_a_delivery(self):
        self.receive("valid", (10, 80, 120))
        foreign = self.root / "other-task" / "foreign.png"
        image(foreign, (100, 20, 30))
        with self.assertRaises(ValueError):
            self.accept([self.page(1, source_image_path=str(foreign))])
        self.assert_no_delivery()

    def test_existing_file_in_generation_folder_without_event_is_rejected(self):
        self.receive("valid", (10, 80, 120))
        unregistered = self.generated / self.checkpoint.thread_id / "unregistered.png"
        image(unregistered, (100, 20, 30))
        with self.assertRaises(ValueError):
            self.accept([self.page(1, source_image_path=str(unregistered))])
        self.assert_no_delivery()

    def test_legacy_asset_id_delivery_remains_readable(self):
        asset, _ = self.receive("legacy", (10, 80, 120))
        delivered = self.accept([self.page(1, asset_id=asset.asset_id)])
        self.assertEqual(delivered.artifacts[0].asset_id, asset.asset_id)

    def test_delivery_does_not_depend_on_model_inventory_file(self):
        asset, source = self.receive("direct", (10, 80, 120))
        (self.directory / "work/generated-assets.json").unlink()
        delivered = self.accept([self.page(1, source_image_path=str(source))])
        self.assertEqual(delivered.artifacts[0].asset_id, asset.asset_id)

    def test_conflicting_id_and_path_are_rejected(self):
        first, _ = self.receive("first", (10, 80, 120))
        _, second_path = self.receive("second", (90, 30, 10))
        with self.assertRaises(ValueError):
            self.accept([self.page(1, source_image_path=str(second_path), asset_id=first.asset_id)])
        self.assert_no_delivery()

    def test_duplicate_file_cannot_be_delivered_as_two_pages(self):
        _, source = self.receive("single", (10, 80, 120))
        with self.assertRaises(ValueError):
            self.accept([self.page(1, source_image_path=str(source)),
                         self.page(2, source_image_path=str(source))])
        self.assert_no_delivery()


if __name__ == "__main__":
    unittest.main()
