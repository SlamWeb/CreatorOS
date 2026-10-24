"""Local SDK fault injection: revision continuity, not model-quality validation."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from creatoros.integrations.codex import CodexProducerError, CodexSdkProducer
from creatoros.integrations.native_image_assets import INVENTORY
from creatoros.integrations.native_production import CHECKPOINT, load_checkpoint
from tests.smoke_native_production import NativeTransport, Turn, create_pair, image


class NativeThreadContinuityTests(unittest.TestCase):
    def setUp(self):
        # Preserve coverage of existing file-v1 Revision/Attempt recovery.
        legacy = patch("creatoros.integrations.native_production.DEFAULT_HANDOFF", "file-v1")
        legacy.start()
        self.addCleanup(legacy.stop)
        self.temp = TemporaryDirectory(prefix="native-thread-continuity-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog, self.pair = create_pair(self.root)
        self.generated = self.root / "generated"
        self.producer = CodexSdkProducer(project_root=self.root, generated_images_root=self.generated,
                                         timeout_seconds=5)
        NativeTransport.reset(self.generated)
        self.sdk = patch("openai_codex.AsyncCodex", NativeTransport)
        self.sdk.start()
        self.addCleanup(self.sdk.stop)
        self.usage = patch.object(NativeTransport, "usage_total", side_effect=lambda mode, turn: (150 * turn, 35 * turn))
        self.usage.start()
        self.addCleanup(self.usage.stop)

    def directory(self, revision=1, attempt=1, run="run"):
        return self.root / run / f"revision-{revision:03d}" / f"attempt-{attempt:03d}"

    def produce(self, revision=1, attempt=1, run="run", thread_id=None):
        return self.producer.produce_to(
            directory=self.directory(revision, attempt, run), pack_id=f"{run}-r{revision:03d}",
            creator_id="creator-test", series_id="series-test", topic_id="topic-test",
            topic_title="One topic", topic_brief="Same topic throughout revisions", series_description="",
            audience="", composition=self.pair, skills_root=self.catalog.root, production_protocol="native-v1",
            thread_id=thread_id, revision_instruction="Make a small correction" if revision > 1 else None,
            previous_pages='{"pages":[{"content":"previous draft"}]}' if revision > 1 else None,
        )

    def test_plain_revision_resumes_but_has_separate_assets_and_incremental_usage(self):
        first = self.produce()
        before = {p.relative_to(first.directory): p.read_bytes() for p in first.directory.rglob("*") if p.is_file()}
        second = self.produce(revision=2, thread_id=first.session.thread_id)
        self.assertEqual(first.session.thread_id, second.session.thread_id)
        self.assertEqual(NativeTransport.starts, [first.session.thread_id])
        self.assertEqual(NativeTransport.resumes, [first.session.thread_id])
        first_assets = json.loads((first.directory / INVENTORY).read_text())
        second_assets = json.loads((second.directory / INVENTORY).read_text())
        self.assertEqual(len(second_assets["assets"]), 1)
        self.assertNotEqual(first_assets["assets"][0]["asset_id"], second_assets["assets"][0]["asset_id"])
        self.assertEqual(second.session.usage.input_tokens, 150)
        self.assertEqual(second.session.usage.output_tokens, 35)
        self.assertEqual(load_checkpoint(second.directory).usage.input_tokens, 300)
        self.assertEqual(before, {p.relative_to(first.directory): p.read_bytes()
                                  for p in first.directory.rglob("*") if p.is_file()})
        prompt = NativeTransport.calls[-1][1][0].text
        self.assertIn("普通返工", prompt)
        self.assertIn("previous draft", prompt)
        self.assertIn("Make a small correction", prompt)
        trace = [json.loads(line) for line in (second.directory / "codex_trace.jsonl").read_text().splitlines()]
        self.assertEqual(trace[0]["type"], "thread.resumed")

    def test_fresh_run_starts_new_thread_even_for_same_topic(self):
        first = self.produce()
        fresh = self.produce(run="fresh-run")
        self.assertNotEqual(first.session.thread_id, fresh.session.thread_id)
        self.assertEqual(len(NativeTransport.starts), 2)
        self.assertFalse(NativeTransport.resumes)

    def test_checkpoint_wins_over_run_level_thread_hint(self):
        NativeTransport.mode = "partial-failure"
        with self.assertRaises(CodexProducerError):
            self.produce()
        old = load_checkpoint(self.directory())
        NativeTransport.mode = "recover"
        result = self.produce(attempt=2, thread_id="unrelated-stale-thread")
        self.assertEqual(result.session.thread_id, old.thread_id)
        self.assertEqual(NativeTransport.resumes, [old.thread_id])
        self.assertEqual(NativeTransport.generated[(old.thread_id, 1)], 1)

    def test_revision_cannot_resume_thread_without_this_run_evidence(self):
        with self.assertRaisesRegex(CodexProducerError, "有效会话证据"):
            self.produce(revision=2, thread_id="foreign-thread")
        self.assertFalse(NativeTransport.starts)
        self.assertFalse(NativeTransport.resumes)

    def test_old_assets_cannot_be_submitted_as_new_revision_delivery(self):
        first = self.produce()
        old_asset = json.loads((first.directory / INVENTORY).read_text())["assets"][0]["asset_id"]

        def old_delivery(transport, thread_id):
            async def turn(inputs, **controls):
                if "output_schema" in controls:
                    return Turn(thread_id, '{"status":"ready","note":"Use current inputs"}',
                                (200, 40), is_review=True, turn_id="review-new-revision")
                work = Path(controls["cwd"])
                (work / "delivery.json").write_text(json.dumps({"complete": True, "artifacts": [
                    {"order": 1, "asset_id": old_asset, "image_prompt": "Attempt to reuse old asset"}]}))
                return Turn(thread_id, "old image submitted", (300, 70), turn_id="new-revision-turn")
            return SimpleNamespace(id=thread_id, turn=turn)

        # A same-thread asset still needs this Revision's own SDK receipt.
        with patch.object(NativeTransport, "_thread", old_delivery):
            with self.assertRaisesRegex(CodexProducerError, "未知图片编号"):
                self.produce(revision=2, thread_id=first.session.thread_id)
        current = json.loads((self.directory(2) / INVENTORY).read_text())
        self.assertEqual(current["assets"], [])
        self.assertEqual(json.loads((self.directory(2) / CHECKPOINT).read_text())["pages"], [])

    def test_retry_before_new_images_is_not_misclassified_by_old_thread_images(self):
        first = self.produce()

        def disconnected(transport, thread_id):
            async def turn(*args, **kwargs):
                raise RuntimeError("injected network failure before new image")
            return SimpleNamespace(id=thread_id, turn=turn)

        with patch.object(NativeTransport, "_thread", disconnected):
            with self.assertRaisesRegex(CodexProducerError, "injected network failure"):
                self.produce(revision=2, thread_id=first.session.thread_id)
        result = self.produce(revision=2, attempt=2, thread_id="ignored-checkpoint-wins")
        self.assertEqual(result.session.thread_id, first.session.thread_id)
        self.assertIn("本 Revision 技术恢复", NativeTransport.calls[-1][1][0].text)
        self.assertNotIn("只修复当前 work/delivery.json", NativeTransport.calls[-1][1][0].text)
        self.assertEqual(result.session.usage.input_tokens, 150)

    def test_service_dispatch_preserves_first_run_isolation_and_revision_thread(self):
        from tests.smoke_native_run_wiring import main
        main()

    def test_unreceived_new_thread_image_still_forces_index_only_recovery(self):
        first = self.produce()
        captured = []

        def failed_after_image(transport, thread_id):
            async def turn(inputs, **controls):
                if "output_schema" in controls:
                    return Turn(thread_id, '{"status":"ready","note":"Use current inputs"}',
                                (200, 40), is_review=True, turn_id="review-before-disconnect")
                captured.append(inputs[0].text)
                image(self.generated / thread_id / "unreceived-new-image.png", (101, 122, 143))
                raise RuntimeError("injected receiver disconnect")
            return SimpleNamespace(id=thread_id, turn=turn)

        with patch.object(NativeTransport, "_thread", failed_after_image):
            with self.assertRaisesRegex(CodexProducerError, "injected receiver disconnect"):
                self.produce(revision=2, thread_id=first.session.thread_id)
            with self.assertRaisesRegex(CodexProducerError, "injected receiver disconnect"):
                self.produce(revision=2, attempt=2, thread_id=first.session.thread_id)
        self.assertIn("只修复当前 work/delivery.json", captured[-1])
        self.assertIn("不得调用生图工具", captured[-1])


if __name__ == "__main__":
    unittest.main()
