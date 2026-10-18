"""Native receipt-path and restart regressions, with local files and SDK fault injection.

These tests do not call a model or generate paid images. PNG fixtures stand for
already generated native files; production SDK construction is forbidden on
offline and exhausted-repair paths.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from creatoros.integrations.codex import CodexProducerError, CodexSdkProducer
from creatoros.integrations.native_production import (
    CHECKPOINT,
    CompositionReview,
    ingest,
    load_checkpoint,
    recover_checkpoint,
    request_digest,
    verified_pages,
)
from creatoros.integrations.visual_production import atomic_json
from creatoros.integrations.worker_protocol import record_thread, record_turn
from tests.smoke_native_production import (
    NativeTransport,
    create_pair,
    image,
    make_delivery,
    prepare_attempt,
    producer_request,
)


def file_hashes(directory: Path) -> dict[str, str]:
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


class NativeDeliveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="native-delivery-recovery-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.catalog, self.pair = create_pair(self.root)
        self.generated = self.root / "generated"
        self.producer = CodexSdkProducer(project_root=self.root,
                                         generated_images_root=self.generated, timeout_seconds=8)

    def attempt(self, name="run"):
        directory = self.root / name / "revision-001" / "attempt-001"
        checkpoint = prepare_attempt(directory, self.pair, self.catalog)
        return directory, checkpoint

    def copied_delivery(self, name="run", *, complete=True):
        directory, checkpoint = self.attempt(name)
        original = self.generated / checkpoint.thread_id / "1.png"
        image(original)
        copied = directory / "work" / "native-copy.png"
        shutil.copy2(original, copied)
        make_delivery(directory, checkpoint.thread_id, copied)
        if not complete:
            path = directory / "work" / "delivery.json"
            value = json.loads(path.read_text())
            value["complete"] = False
            path.write_text(json.dumps(value), encoding="utf-8")
        return directory, checkpoint, original, copied

    def save_failed_attempt(self, directory, checkpoint, *, repair_attempted=True,
                            completed=True):
        checkpoint.repair_attempted = repair_attempted
        atomic_json(directory / CHECKPOINT, checkpoint)
        record_thread(directory, checkpoint.thread_id)
        record_turn(directory, "turn-final", "delivery_repair", "completed" if completed else "failed")
        (directory / "error.txt").write_text("historical failure: outside image path", encoding="utf-8")
        (directory / "production_progress.json").write_text('{"status":"failed"}', encoding="utf-8")

    def test_work_copy_requires_same_thread_native_bytes_and_keeps_raw_index(self):
        directory, checkpoint, original, _ = self.copied_delivery()
        path = directory / "work" / "delivery.json"
        before = path.read_bytes()
        delivery = ingest(directory, checkpoint, self.generated)
        self.assertEqual(Path(delivery.artifacts[0].source_image_path), original)
        self.assertEqual(checkpoint.pages[0].sha256, hashlib.sha256(original.read_bytes()).hexdigest())
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue((directory / "delivery_path_resolution.json").is_file())

    def test_relative_work_copy_is_resolved_from_work_not_project(self):
        directory, checkpoint, original, copied = self.copied_delivery()
        make_delivery(directory, checkpoint.thread_id, copied.name)
        ingest(directory, checkpoint, self.generated)
        self.assertEqual(Path(checkpoint.pages[0].source_image_path), original)

    def test_other_thread_native_path_is_rejected_even_when_bytes_match(self):
        directory, checkpoint, original, _ = self.copied_delivery()
        other = self.generated / "other-thread" / "1.png"
        other.parent.mkdir()
        shutil.copy2(original, other)
        make_delivery(directory, checkpoint.thread_id, other)
        with self.assertRaises(ValueError):
            ingest(directory, checkpoint, self.generated)
        self.assertFalse(checkpoint.pages)

    def test_work_copy_with_only_other_thread_match_is_rejected(self):
        directory, checkpoint, original, _ = self.copied_delivery()
        other = self.generated / "other-thread" / "1.png"
        other.parent.mkdir()
        shutil.move(original, other)
        with self.assertRaises(ValueError):
            ingest(directory, checkpoint, self.generated)
        self.assertFalse(checkpoint.pages)

    def test_forged_work_image_is_rejected(self):
        directory, checkpoint, _, copied = self.copied_delivery()
        image(copied, (255, 1, 1))
        with self.assertRaises(ValueError):
            ingest(directory, checkpoint, self.generated)
        self.assertFalse(checkpoint.pages)

    def test_previously_accepted_saved_bytes_cannot_be_tampered(self):
        directory, checkpoint, _, _ = self.copied_delivery()
        ingest(directory, checkpoint, self.generated)
        Path(checkpoint.pages[0].image_path).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            verified_pages(directory, checkpoint)
        self.assertIsNone(load_checkpoint(directory))

    def test_work_symlink_is_not_a_copy_receipt(self):
        directory, checkpoint, original, copied = self.copied_delivery()
        copied.unlink()
        try:
            copied.symlink_to(original)
        except OSError as error:
            self.fail(f"test environment cannot create required symlink: {error}")
        with self.assertRaises(ValueError):
            ingest(directory, checkpoint, self.generated)

    def test_native_symlink_cannot_supply_hash_match(self):
        directory, checkpoint, original, copied = self.copied_delivery()
        original.unlink()
        try:
            original.symlink_to(copied)
        except OSError as error:
            self.fail(f"test environment cannot create required symlink: {error}")
        with self.assertRaises(ValueError):
            ingest(directory, checkpoint, self.generated)

    def test_resolution_audit_symlink_cannot_overwrite_external_file(self):
        directory, checkpoint, _, _ = self.copied_delivery()
        outside = self.root / "unrelated-audit.json"
        outside.write_bytes(b'{"unrelated":"preserve"}')
        before = outside.read_bytes()
        try:
            (directory / "delivery_path_resolution.json").symlink_to(outside)
        except OSError as error:
            self.fail(f"test environment cannot create required symlink: {error}")
        with self.assertRaises(ValueError):
            ingest(directory, checkpoint, self.generated)
        self.assertEqual(outside.read_bytes(), before)
        self.assertFalse(checkpoint.pages)

    def test_exact_current_revision_skill_mislevel_is_normalized(self):
        directory, checkpoint, original, _ = self.copied_delivery()
        mistaken = directory.parent / "skills" / "production" / "assets" / "reference.md"
        self.assertFalse(mistaken.exists())
        make_delivery(directory, checkpoint.thread_id, original, reference_assets=[str(mistaken)])
        before = (directory / "work" / "delivery.json").read_bytes()
        ingest(directory, checkpoint, self.generated)
        self.assertEqual(checkpoint.pages[0].reference_assets, ["skills/production/assets/reference.md"])
        self.assertEqual((directory / "work" / "delivery.json").read_bytes(), before)

    def test_existing_mislevel_skill_with_wrong_bytes_is_rejected(self):
        directory, checkpoint, original, _ = self.copied_delivery()
        mistaken = directory.parent / "skills" / "production" / "assets" / "reference.md"
        mistaken.parent.mkdir(parents=True)
        mistaken.write_text("different task reference", encoding="utf-8")
        make_delivery(directory, checkpoint.thread_id, original, reference_assets=[str(mistaken)])
        with self.assertRaises(ValueError):
            ingest(directory, checkpoint, self.generated)

    def test_existing_mislevel_skill_with_identical_bytes_is_normalized(self):
        directory, checkpoint, original, _ = self.copied_delivery()
        mistaken = directory.parent / "skills" / "production" / "assets" / "reference.md"
        mistaken.parent.mkdir(parents=True)
        shutil.copy2(directory / "skills" / "production" / "assets" / "reference.md", mistaken)
        make_delivery(directory, checkpoint.thread_id, original, reference_assets=[str(mistaken)])
        ingest(directory, checkpoint, self.generated)
        self.assertEqual(checkpoint.pages[0].reference_assets, ["skills/production/assets/reference.md"])

    def test_other_revision_skill_path_cannot_be_normalized_by_filename(self):
        directory, checkpoint, original, _ = self.copied_delivery()
        other = directory.parent.parent / "revision-999" / "skills" / "production" / "assets" / "reference.md"
        make_delivery(directory, checkpoint.thread_id, original, reference_assets=[str(other)])
        with self.assertRaises(ValueError):
            ingest(directory, checkpoint, self.generated)

    def test_completed_receipt_zero_pages_recovers_offline_and_retains_old_failure(self):
        directory, checkpoint, original, _ = self.copied_delivery()
        self.save_failed_attempt(directory, checkpoint)
        historical = file_hashes(directory)
        with patch("openai_codex.AsyncCodex") as sdk:
            pack = producer_request(self.producer, directory.parent / "attempt-002", self.pair, self.catalog.root)
        sdk.assert_not_called()
        current = Path(pack.directory)
        self.assertEqual(file_hashes(directory), historical)
        self.assertEqual((current / "work" / "previous-delivery.json").read_bytes(),
                         (directory / "work" / "delivery.json").read_bytes())
        self.assertEqual(len(pack.pack.cards), 1)
        self.assertEqual((current / pack.pack.cards[0].image_path).read_bytes(), original.read_bytes())
        self.assertTrue(load_checkpoint(current).turn_completed)
        self.assertEqual(pack.session.usage.input_tokens, 0)
        self.assertEqual(pack.session.usage.output_tokens, 0)

    def test_repair_budget_exhausted_blocks_sdk_before_any_production(self):
        directory, checkpoint, original, _ = self.copied_delivery()
        make_delivery(directory, checkpoint.thread_id, original, content_file="../missing.md")
        self.save_failed_attempt(directory, checkpoint)
        historical = file_hashes(directory)
        with patch("openai_codex.AsyncCodex") as sdk:
            with self.assertRaises(CodexProducerError) as raised:
                producer_request(self.producer, directory.parent / "attempt-002", self.pair, self.catalog.root)
        sdk.assert_not_called()
        self.assertEqual(raised.exception.error_type, "native_delivery_failed")
        self.assertIn("修复", str(raised.exception))
        self.assertRegex(str(raised.exception), r"已核验\s*0\s*张")
        self.assertIn("文件", str(raised.exception))
        self.assertEqual(file_hashes(directory), historical)

    def test_missing_delivery_with_native_image_and_exhausted_budget_never_calls_sdk(self):
        directory, checkpoint, _, _ = self.copied_delivery()
        (directory / "work" / "delivery.json").unlink()
        self.save_failed_attempt(directory, checkpoint)
        historical = file_hashes(directory)
        with patch("openai_codex.AsyncCodex") as sdk:
            with self.assertRaises(CodexProducerError) as raised:
                producer_request(self.producer, directory.parent / "attempt-002", self.pair, self.catalog.root)
        sdk.assert_not_called()
        self.assertEqual(raised.exception.error_type, "native_delivery_failed")
        self.assertRegex(str(raised.exception), r"已核验\s*0\s*张")
        self.assertEqual(file_hashes(directory), historical)

    def test_incomplete_receipt_does_not_offline_finalize_without_completed_turn(self):
        directory, checkpoint, _, _ = self.copied_delivery()
        self.save_failed_attempt(directory, checkpoint, completed=False)
        next_attempt = directory.parent / "attempt-002"
        prepare_attempt(next_attempt, self.pair, self.catalog)
        restored = recover_checkpoint(next_attempt, request_digest(next_attempt), self.generated)
        self.assertIsNotNone(restored)
        self.assertFalse(restored.turn_completed)

    def test_normalized_skill_and_copy_survive_second_offline_attempt(self):
        directory, checkpoint, original, copied = self.copied_delivery()
        mistaken = directory.parent / "skills" / "production" / "assets" / "reference.md"
        make_delivery(directory, checkpoint.thread_id, copied, reference_assets=[str(mistaken)])
        self.save_failed_attempt(directory, checkpoint)
        historical = file_hashes(directory)
        with patch("openai_codex.AsyncCodex") as sdk:
            second = producer_request(self.producer, directory.parent / "attempt-002", self.pair, self.catalog.root)
            second_before = file_hashes(Path(second.directory))
            third = producer_request(self.producer, directory.parent / "attempt-003", self.pair, self.catalog.root)
        sdk.assert_not_called()
        self.assertEqual(file_hashes(directory), historical)
        self.assertEqual(file_hashes(Path(second.directory)), second_before)
        saved = load_checkpoint(Path(third.directory))
        self.assertTrue(saved.turn_completed)
        self.assertEqual(saved.pages[0].reference_assets, ["skills/production/assets/reference.md"])
        self.assertEqual(Path(saved.pages[0].image_path).read_bytes(), original.read_bytes())

    def test_available_repair_resumes_only_index_turn_and_never_generates_again(self):
        directory, checkpoint, original, _ = self.copied_delivery()
        checkpoint.composition_review = CompositionReview(status="ready", note="frozen selection")
        make_delivery(directory, checkpoint.thread_id, original, content_file="../missing.md")
        self.save_failed_attempt(directory, checkpoint, repair_attempted=False)
        historical = file_hashes(directory)
        NativeTransport.reset(self.generated, "repair-success")
        NativeTransport.turn_numbers[checkpoint.thread_id] = 1
        NativeTransport.generated[(checkpoint.thread_id, 1)] = 1
        with patch("openai_codex.AsyncCodex", NativeTransport):
            pack = producer_request(self.producer, directory.parent / "attempt-002", self.pair, self.catalog.root)
        self.assertFalse(NativeTransport.starts)
        self.assertEqual(NativeTransport.resumes, [checkpoint.thread_id])
        self.assertEqual(len(NativeTransport.calls), 1)
        self.assertIn("不得调用生图工具，不得重新研究或重画", NativeTransport.calls[0][1][0].text)
        self.assertEqual(NativeTransport.generated, {(checkpoint.thread_id, 1): 1})
        self.assertEqual(file_hashes(directory), historical)
        self.assertTrue(load_checkpoint(Path(pack.directory)).turn_completed)


if __name__ == "__main__":
    unittest.main()
