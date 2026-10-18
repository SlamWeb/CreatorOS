"""SDK-completion image receipt regressions; local fixtures, no model calls.

Typed SDK notifications exercise the receipt boundary. Files and injected
failures are isolated from production data and are not image-quality Evals.
"""
from __future__ import annotations

import hashlib
import json
import errno
import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openai_codex.generated import v2_all as sdk_types
from openai_codex.models import Notification

from creatoros.integrations.native_image_assets import ImageAssetStore
from creatoros.integrations.atomic_file import atomic_write_bytes
from creatoros.integrations.codex import CodexProducerError, CodexSdkProducer
from creatoros.integrations.native_production import ingest, verified_pages
from tests.smoke_native_production import (
    NativeTransport, create_pair, image, make_delivery, prepare_attempt, producer_request,
)


def tree_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class NativeImageAssetTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="native-image-assets-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.generated = self.root / "generated"
        self.directory = self.root / "revision-001" / "attempt-001"
        self.thread = "native-assets-thread"
        self.digest = "a" * 64
        (self.directory / "work").mkdir(parents=True)
        self.store = self.make_store(self.directory)
        self.store.initialize()

    def make_store(self, directory, *, thread=None, digest=None):
        return ImageAssetStore(directory=directory, generated_root=self.generated,
                               thread_id=thread or self.thread,
                               input_digest=digest or self.digest)

    def notification(self, source=None, *, item_id="image-1", turn_id="turn-1",
                     thread_id=None, status="completed", method="item/completed"):
        source = source or self.generated / self.thread / "image-1.png"
        payload = sdk_types.ItemCompletedNotification.model_validate({
            "threadId": thread_id or self.thread, "turnId": turn_id,
            "completedAtMs": 1,
            "item": {"type": "imageGeneration", "id": item_id, "result": "",
                     "status": status, "savedPath": str(source),
                     "revisedPrompt": "A new educational comic."},
        })
        return Notification(method, payload)

    def receive(self, *, item_id="image-1", turn_id="turn-1", color=(24, 72, 120)):
        source = self.generated / self.thread / f"{item_id}.png"
        image(source, color)
        self.store.observe(self.notification(source, item_id=item_id, turn_id=turn_id))
        asset = self.store.read().assets[-1]
        return asset, source

    def test_completion_copies_and_records_typed_sdk_receipt(self):
        asset, source = self.receive()
        self.assertTrue(asset.asset_id.startswith("img_"))
        self.assertEqual(asset.item_id, "image-1")
        self.assertEqual(asset.turn_id, "turn-1")
        self.assertEqual(Path(asset.source_image_path), source)
        self.assertEqual((asset.width, asset.height), (48, 32))
        self.assertEqual(asset.revised_prompt, "A new educational comic.")
        self.assertEqual(asset.sha256, hashlib.sha256(source.read_bytes()).hexdigest())
        resolved, received = self.store.resolve(asset.asset_id)
        self.assertEqual(resolved.asset_id, asset.asset_id)
        self.assertTrue(received.is_relative_to(self.directory / "received-images"))
        self.assertEqual(received.read_bytes(), source.read_bytes())
        self.assertTrue((self.directory / "native_image_assets.json").is_file())

    def test_model_view_has_identifiers_and_no_absolute_source_paths(self):
        asset, source = self.receive()
        raw = (self.directory / "work" / "generated-assets.json").read_text(encoding="utf-8")
        view = json.loads(raw)
        self.assertIn(asset.asset_id, raw)
        self.assertNotIn(str(source), raw)
        self.assertNotIn(str(self.directory), raw)
        self.assertNotIn("source_image_path", raw)
        self.assertIsInstance(view, (dict, list))

    def test_duplicate_completed_notification_is_idempotent(self):
        asset, source = self.receive()
        before = tree_hashes(self.directory)
        self.store.observe(self.notification(source))
        self.assertEqual([row.asset_id for row in self.store.read().assets], [asset.asset_id])
        self.assertEqual(tree_hashes(self.directory), before)

    def test_distinct_completed_items_remain_distinct_candidates(self):
        first, _ = self.receive()
        second, _ = self.receive(item_id="image-2", turn_id="turn-2", color=(0, 140, 80))
        self.assertNotEqual(first.asset_id, second.asset_id)
        self.assertEqual(len(self.store.read().assets), 2)

    def test_failed_or_non_completed_notifications_do_not_register_images(self):
        source = self.generated / self.thread / "image-1.png"
        image(source)
        for options in ({"status": "failed"}, {"status": "inProgress"},
                        {"method": "item/started"}):
            with self.subTest(options=options):
                self.store.observe(self.notification(source, **options))
                self.assertEqual(self.store.read().assets, [])

    def test_non_image_item_is_ignored(self):
        payload = sdk_types.ItemCompletedNotification.model_validate({
            "threadId": self.thread, "turnId": "turn-1", "completedAtMs": 1,
            "item": {"type": "agentMessage", "id": "reply", "text": "Done."},
        })
        self.store.observe(Notification("item/completed", payload))
        self.assertEqual(self.store.read().assets, [])

    def test_cross_thread_event_cannot_register_even_current_thread_path(self):
        source = self.generated / self.thread / "image-1.png"
        image(source)
        with self.assertRaises(ValueError):
            self.store.observe(self.notification(source, thread_id="other-thread"))
        self.assertEqual(self.store.read().assets, [])

    def test_other_turn_cannot_register_when_collector_binds_current_turn(self):
        source = self.generated / self.thread / "image-1.png"
        image(source)
        self.store.expected_turn_id = "turn-expected"
        with self.assertRaises(ValueError):
            self.store.observe(self.notification(source, turn_id="turn-unrelated"))
        self.assertEqual(self.store.read().assets, [])

    def test_completed_item_with_failure_payload_is_not_a_received_image(self):
        source = self.generated / self.thread / "image-1.png"
        image(source)
        event = self.notification(source)
        failure = sdk_types.ImageGenerationFailure.model_validate({
            "type": "usageLimitExceeded", "limitId": "fixture-limit",
        })
        event.payload.item.root.failure = failure
        self.store.observe(event)
        self.assertEqual(self.store.read().assets, [])

    def test_missing_turn_id_is_rejected(self):
        source = self.generated / self.thread / "image-1.png"
        image(source)
        with self.assertRaises(ValueError):
            self.store.observe(self.notification(source, turn_id=""))
        self.assertEqual(self.store.read().assets, [])

    def test_event_source_outside_current_thread_is_rejected(self):
        source = self.generated / "other-thread" / "foreign.png"
        image(source)
        with self.assertRaises(ValueError):
            self.store.observe(self.notification(source))
        self.assertEqual(self.store.read().assets, [])

    def test_symlink_source_is_rejected(self):
        outside = self.root / "outside.png"
        image(outside)
        source = self.generated / self.thread / "linked.png"
        source.parent.mkdir(parents=True)
        try:
            source.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"This Windows account cannot create symbolic links: {error}")
        with self.assertRaises(ValueError):
            self.store.observe(self.notification(source))
        self.assertEqual(self.store.read().assets, [])

    def test_non_image_bytes_do_not_create_an_asset(self):
        source = self.generated / self.thread / "invalid.png"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"not an image")
        with self.assertRaises((ValueError, OSError)):
            self.store.observe(self.notification(source))
        self.assertEqual(self.store.read().assets, [])

    def test_unknown_identifier_is_rejected(self):
        self.receive()
        with self.assertRaises(ValueError):
            self.store.resolve("img_not_recorded")

    def test_model_readable_view_is_not_an_authority_for_receiving_images(self):
        self.receive()
        view = self.directory / "work" / "generated-assets.json"
        view.write_text(json.dumps({"images": [{"asset_id": "img_forged",
            "preview_file": "invented.png", "width": 48, "height": 32}]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.resolve("img_forged")
        self.assertEqual(len(self.store.read().assets), 1)

    def test_received_image_tampering_is_rejected(self):
        asset, _ = self.receive()
        _, received = self.store.resolve(asset.asset_id)
        image(received, (255, 0, 0))
        with self.assertRaises(ValueError):
            self.store.resolve(asset.asset_id)

    def test_deleted_original_still_resolves_verified_host_copy(self):
        asset, source = self.receive()
        original_bytes = source.read_bytes()
        source.unlink()
        _, received = self.store.resolve(asset.asset_id)
        self.assertEqual(received.read_bytes(), original_bytes)

    def test_path_compatibility_maps_only_registered_source_or_matching_work_copy(self):
        asset, source = self.receive()
        copy = self.directory / "work" / "copy.png"
        shutil.copy2(source, copy)
        for raw in (str(source), str(copy), copy.name):
            with self.subTest(raw=raw):
                resolved, received = self.store.resolve_path(raw)
                self.assertEqual(resolved.asset_id, asset.asset_id)
                self.assertEqual(received.read_bytes(), source.read_bytes())
        image(copy, (255, 0, 0))
        with self.assertRaises(ValueError):
            self.store.resolve_path(str(copy))

    def test_unregistered_image_cannot_be_received_via_final_path(self):
        self.receive()
        rogue = self.generated / self.thread / "no-completion-event.png"
        image(rogue, (180, 0, 0))
        with self.assertRaises(ValueError):
            self.store.resolve_path(str(rogue))
        copy = self.directory / "work" / "rogue.png"
        shutil.copy2(rogue, copy)
        with self.assertRaises(ValueError):
            self.store.resolve_path(str(copy))

    def test_foreign_matching_bytes_cannot_be_delivered_by_foreign_path(self):
        _, source = self.receive()
        foreign = self.generated / "other-thread" / "same.png"
        foreign.parent.mkdir(parents=True)
        shutil.copy2(source, foreign)
        with self.assertRaises(ValueError):
            self.store.resolve_path(str(foreign))

    def test_reused_item_cannot_silently_replace_existing_image(self):
        asset, source = self.receive()
        before = tree_hashes(self.directory)
        image(source, (255, 0, 0))
        with self.assertRaises(ValueError):
            self.store.observe(self.notification(source))
        self.assertEqual(tree_hashes(self.directory), before)
        self.assertEqual(self.store.resolve(asset.asset_id)[0].sha256, asset.sha256)

    def test_initialize_does_not_clear_existing_inventory(self):
        asset, _ = self.receive()
        before = tree_hashes(self.directory)
        self.make_store(self.directory).initialize()
        self.assertEqual(tree_hashes(self.directory), before)
        self.assertEqual(self.store.read().assets[0].asset_id, asset.asset_id)

    def test_repeated_restore_keeps_old_attempt_bytes_and_validates_local_copy(self):
        asset, source = self.receive()
        source.unlink()
        previous = self.directory
        old_hashes = tree_hashes(previous)
        for number in (2, 3):
            current = self.directory.parent / f"attempt-{number:03d}"
            (current / "work").mkdir(parents=True)
            store = self.make_store(current)
            store.restore_from(previous)
            self.assertEqual(tree_hashes(self.directory), old_hashes)
            self.assertEqual(store.read().assets[0].asset_id, asset.asset_id)
            _, received = store.resolve(asset.asset_id)
            self.assertTrue(received.is_relative_to(current / "received-images"))
            self.assertEqual(hashlib.sha256(received.read_bytes()).hexdigest(), asset.sha256)
            previous = current

    def test_restore_rejects_wrong_thread_or_input_scope_without_old_writes(self):
        self.receive()
        before = tree_hashes(self.directory)
        for name, options in (("foreign-thread", {"thread": "other-thread"}),
                              ("foreign-input", {"digest": "b" * 64})):
            with self.subTest(name=name):
                current = self.root / name
                (current / "work").mkdir(parents=True)
                with self.assertRaises(ValueError):
                    self.make_store(current, **options).restore_from(self.directory)
                self.assertEqual(tree_hashes(self.directory), before)

    def integration_attempt(self):
        catalog, pair = create_pair(self.root / "integration")
        directory = self.root / "integration" / "revision-001" / "attempt-001"
        checkpoint = prepare_attempt(directory, pair, catalog)
        store = self.make_store(directory, thread=checkpoint.thread_id, digest=checkpoint.input_digest)
        store.initialize()
        source = self.generated / checkpoint.thread_id / "selected.png"
        image(source)
        return directory, checkpoint, store, source

    def test_asset_identifier_ingests_after_native_original_is_deleted(self):
        directory, checkpoint, store, source = self.integration_attempt()
        store.observe(self.notification(source, thread_id=checkpoint.thread_id))
        asset = store.read().assets[0]
        make_delivery(directory, checkpoint.thread_id, source)
        index = directory / "work" / "delivery.json"
        value = json.loads(index.read_text(encoding="utf-8"))
        value["artifacts"][0].pop("source_image_path")
        value["artifacts"][0]["asset_id"] = asset.asset_id
        index.write_text(json.dumps(value), encoding="utf-8")
        raw_index = index.read_bytes()
        source.unlink()
        ingest(directory, checkpoint, self.generated)
        self.assertEqual(verified_pages(directory, checkpoint)[0].asset_id, asset.asset_id)
        self.assertEqual(checkpoint.pages[0].sha256, asset.sha256)
        self.assertEqual(index.read_bytes(), raw_index)

    def test_inventory_mode_rejects_native_path_without_completion_receipt(self):
        directory, checkpoint, _, source = self.integration_attempt()
        make_delivery(directory, checkpoint.thread_id, source)
        with self.assertRaises(ValueError):
            ingest(directory, checkpoint, self.generated)
        self.assertEqual(checkpoint.pages, [])

    def test_only_selected_candidate_is_delivered_and_unknown_id_keeps_existing_page(self):
        directory, checkpoint, store, source = self.integration_attempt()
        store.observe(self.notification(source, thread_id=checkpoint.thread_id))
        first = store.read().assets[0]
        other = source.with_name("alternative.png")
        image(other, (200, 120, 0))
        store.observe(self.notification(other, thread_id=checkpoint.thread_id, item_id="image-alternative"))
        self.assertEqual(len(store.read().assets), 2)
        make_delivery(directory, checkpoint.thread_id, source)
        index = directory / "work" / "delivery.json"
        value = json.loads(index.read_text(encoding="utf-8"))
        value["artifacts"][0].pop("source_image_path")
        value["artifacts"][0]["asset_id"] = first.asset_id
        index.write_text(json.dumps(value), encoding="utf-8")
        ingest(directory, checkpoint, self.generated)
        self.assertEqual(len(checkpoint.pages), 1)
        self.assertEqual(checkpoint.pages[0].asset_id, first.asset_id)
        before = [row.model_dump() for row in checkpoint.pages]
        value["artifacts"][0]["asset_id"] = "img_" + "f" * 24
        index.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            ingest(directory, checkpoint, self.generated)
        self.assertEqual([row.model_dump() for row in checkpoint.pages], before)
        self.assertEqual(len(verified_pages(directory, checkpoint)), 1)

    def test_failed_authority_write_propagates_and_does_not_register_orphan_copy(self):
        source = self.generated / self.thread / "image-1.png"
        image(source)
        event = self.notification(source)
        before = (self.directory / "native_image_assets.json").read_bytes()
        with patch("creatoros.integrations.native_image_assets.atomic_json",
                   side_effect=OSError("injected authority write denied")):
            with self.assertRaisesRegex(OSError, "injected authority write denied"):
                self.store.observe(event)
        self.assertEqual((self.directory / "native_image_assets.json").read_bytes(), before)
        self.assertEqual(self.store.read().assets, [])
        with self.assertRaises(ValueError):
            self.store.resolve_path(str(source))
        # A later duplicate event may safely finish the interrupted receipt;
        # merely finding the orphan file must not have counted as success.
        self.store.observe(event)
        self.assertEqual(len(self.store.read().assets), 1)

    def test_critical_receipt_failure_interrupts_original_turn_without_repair(self):
        catalog, pair = create_pair(self.root / "critical-observer")
        directory = self.root / "critical-observer" / "revision-001" / "attempt-001"
        producer = CodexSdkProducer(project_root=self.root, generated_images_root=self.generated,
                                    timeout_seconds=8)
        NativeTransport.reset(self.generated, "success")
        with patch("openai_codex.AsyncCodex", NativeTransport), patch.object(
                ImageAssetStore, "observe", side_effect=OSError("injected critical receipt denied")):
            with self.assertRaisesRegex(CodexProducerError, "injected critical receipt denied"):
                producer_request(producer, directory, pair, catalog.root)
        self.assertEqual(NativeTransport.interrupted, ["turn-1"])
        self.assertEqual(len(NativeTransport.calls), 1, "a failed receipt must not schedule repair or new generation")
        saved = json.loads((directory / "native_checkpoint.json").read_text(encoding="utf-8"))
        self.assertFalse(saved["turn_completed"])
        self.assertFalse(saved["repair_attempted"])
        self.assertEqual(saved["pages"], [])
        receipt = json.loads((directory / "worker_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["turns"][-1]["status"], "failed")

    def test_binary_atomic_write_retries_windows_sharing_failures_then_publishes(self):
        target = self.root / "binary.png"
        original, replacement = b"old\x00\xff", b"new\x00\xff\r\n"
        real_replace = os.replace
        for winerror in (5, 32, 33):
            with self.subTest(winerror=winerror):
                target.write_bytes(original)
                seen = []
                denied = PermissionError("injected sharing failure")
                denied.winerror = winerror

                def once(source, destination):
                    seen.append(Path(source))
                    self.assertEqual(Path(source).read_bytes(), replacement)
                    if len(seen) == 1:
                        self.assertEqual(target.read_bytes(), original)
                        raise denied
                    real_replace(source, destination)

                with patch("creatoros.integrations.atomic_file.os.replace", side_effect=once):
                    atomic_write_bytes(target, replacement)
                self.assertEqual(target.read_bytes(), replacement)
                self.assertEqual(len(seen), 2)
                self.assertFalse(list(self.root.glob(".binary.png.*.tmp")))

    def test_binary_atomic_permanent_failure_keeps_old_bytes_and_cleans_temporary(self):
        target = self.root / "binary.png"
        original = b"old\x00\xff"
        target.write_bytes(original)
        denied = PermissionError("injected persistent sharing failure")
        denied.winerror = 5
        with patch("creatoros.integrations.atomic_file.os.replace", side_effect=denied) as replace:
            with self.assertRaises(PermissionError) as caught:
                atomic_write_bytes(target, b"not published\x00\xff")
        self.assertIs(caught.exception, denied)
        self.assertEqual(replace.call_count, 4)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(list(self.root.glob(".binary.png.*.tmp")))

    def test_binary_atomic_non_sharing_failure_is_not_retried(self):
        target = self.root / "binary.png"
        original = b"old\x00\xff"
        target.write_bytes(original)
        disk_full = OSError(errno.ENOSPC, "injected disk full")
        with patch("creatoros.integrations.atomic_file.os.replace", side_effect=disk_full) as replace:
            with self.assertRaises(OSError) as caught:
                atomic_write_bytes(target, b"not published\x00\xff")
        self.assertIs(caught.exception, disk_full)
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(list(self.root.glob(".binary.png.*.tmp")))


if __name__ == "__main__":
    unittest.main()
