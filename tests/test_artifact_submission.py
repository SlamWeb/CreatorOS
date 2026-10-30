"""Real temporary files, fault-injected SDK image events; no paid calls."""
import json
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creatoros.integrations.artifact_submission import ImageSubmission, SUBMISSION, accepted_submission
from creatoros.integrations.native_production import verified_pages
from tests import test_file_delivery as file_fixtures


class ArtifactSubmissionTests(unittest.TestCase):
    setUp = file_fixtures.FileDeliveryTests.setUp
    receive = file_fixtures.FileDeliveryTests.receive
    page = staticmethod(file_fixtures.FileDeliveryTests.page)

    def handler(self):
        self.checkpoint.handoff_protocol = "tool-v1"
        handler = ImageSubmission(self.directory, self.checkpoint, self.generated, self.bundle)
        handler.turn_id = "turn-1"
        return handler

    def payload(self, path):
        return {"title": "图文标题", "text": "文案\n\n保留换行", "hashtags": ["英语"],
                "complete": True, "artifacts": [self.page(1, source_image_path=str(path))]}

    def test_wrong_path_then_correct_same_handler_preserves_one_image(self):
        asset, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        bad = handler.submit(self.payload(path.with_name("missing.png")))
        self.assertFalse(bad["accepted"])
        self.assertTrue(bad["retryable"])
        self.assertEqual(bad["received_images"][0]["source_image_path"], str(path))
        self.assertFalse((self.directory / SUBMISSION).exists())
        good = handler.submit(self.payload(path))
        self.assertTrue(good["accepted"])
        self.assertEqual(len(self.store.read().assets), 1)
        self.assertEqual(self.checkpoint.pages[0].asset_id, asset.asset_id)
        self.assertFalse(self.checkpoint.turn_completed, "Tool success is not SDK turn completion")
        self.assertEqual(accepted_submission(self.directory, self.checkpoint)["turn_id"], "turn-1")

    def test_duplicate_success_reuses_durable_receipt(self):
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        first = handler.submit(self.payload(path))
        before = (self.directory / SUBMISSION).read_bytes()
        self.assertEqual(handler.submit(self.payload(path)), first)
        self.assertEqual((self.directory / SUBMISSION).read_bytes(), before)
        second_handler = self.handler()
        second_handler.turn_id = "turn-2"
        self.assertEqual(second_handler.submit(self.payload(path)), first)

    def test_different_payload_cannot_replace_accepted_delivery(self):
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        handler.submit(self.payload(path))
        changed = {**self.payload(path), "title": "changed"}
        result = handler.submit(changed)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["retryable"])
        self.assertEqual(self.checkpoint.delivery.title, "图文标题")

    def test_three_invalid_submissions_stop_without_deleting_image(self):
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        for index in range(3):
            result = handler.submit(self.payload(path.with_name("missing.png")))
            self.assertEqual(result["retryable"], index < 2)
        self.assertFalse(handler.submit(self.payload(path))["accepted"])
        self.assertTrue(path.is_file())
        self.assertEqual(len(self.store.read().assets), 1)

    def test_host_receipt_tampering_is_rejected(self):
        _, path = self.receive("first", (10, 80, 120))
        self.handler().submit(self.payload(path))
        target = self.directory / SUBMISSION
        data = json.loads(target.read_text(encoding="utf-8"))
        data["submission"]["title"] = "forged"
        target.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            verified_pages(self.directory, self.checkpoint)

    def test_submission_requires_file_paths_not_model_internal_ids(self):
        asset, path = self.receive("first", (10, 80, 120))
        value = self.payload(path)
        value["artifacts"] = [self.page(1, asset_id=asset.asset_id)]
        self.assertFalse(self.handler().submit(value)["accepted"])

    def test_failed_receipt_checkpoint_write_cannot_acknowledge_in_memory_success(self):
        _, path = self.receive("first", (10, 80, 120))
        handler = self.handler()
        with patch("creatoros.integrations.visual_production.atomic_json", side_effect=OSError("disk full")):
            first = handler.submit(self.payload(path))
            second = handler.submit(self.payload(path))
        self.assertFalse(first["accepted"])
        self.assertFalse(second["accepted"])
        self.assertIsNone(self.checkpoint.submission_sha256)
        self.assertTrue(handler.submit(self.payload(path))["accepted"])


class SubmissionExecutionTests(unittest.TestCase):
    def test_same_turn_correction_and_interrupted_receipt_resume_without_regeneration(self):
        from openai_codex.generated import v2_all as sdk_types
        from openai_codex.models import Notification
        from creatoros.integrations.codex import CodexSdkProducer, CodexProducerError
        from creatoros.integrations.native_production import load_checkpoint
        from tests.smoke_native_production import image, write_skill
        with TemporaryDirectory(prefix="submission-execute-") as temporary:
            root = Path(temporary)
            skill = root / "skill"
            write_skill(skill, "submit-fixture", "Test receipt")
            generated = root / "generated"
            image_path = generated / "submit-thread" / "image.png"
            calls, replies, created = [], [], []
            payload = {"title": "Title", "text": "Caption", "hashtags": [], "complete": True,
                       "artifacts": [{"order": 1, "source_image_path": str(image_path),
                                      "image_prompt": "A scene", "reference_ids": [], "content": "text"}]}
            @asynccontextmanager
            async def client(deadline, cancel_event, dynamic_tools):
                handler = dynamic_tools[0].handler
                async def start(*args, **options):
                    async def turn(inputs, **controls):
                        calls.append(inputs[0].text)
                        turn_id = f"turn-{len(calls)}"
                        async def stream():
                            current_path = image_path if len(calls) < 3 else image_path.with_name("rework.png")
                            current_payload = {**payload, "artifacts": [{**payload["artifacts"][0], "source_image_path": str(current_path)}]}
                            if len(calls) in {1, 3}:
                                image(current_path)
                                created.append(current_path)
                                yield Notification("item/completed", sdk_types.ItemCompletedNotification.model_validate({
                                    "threadId": "submit-thread", "turnId": turn_id, "completedAtMs": 1,
                                    "item": {"type": "imageGeneration", "id": "image-1", "status": "completed",
                                             "result": "", "savedPath": str(current_path)}}))
                            if len(calls) == 1:
                                bad = {**payload, "artifacts": [{**payload["artifacts"][0], "source_image_path": "missing.png"}]}
                                replies.append(await handler(bad))
                            replies.append(await handler(current_payload))
                            # First turn simulates a disconnect after tool success.
                            yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                                "threadId": "submit-thread", "turn": {"id": turn_id, "items": [],
                                    "status": "interrupted" if len(calls) == 1 else "completed"}}))
                        async def interrupt():
                            pass
                        return SimpleNamespace(id=turn_id, thread_id="submit-thread", stream=stream, interrupt=interrupt)
                    return SimpleNamespace(id="submit-thread", turn=turn)
                yield SimpleNamespace(thread_start=start, thread_resume=start)
            producer = CodexSdkProducer(project_root=root, generated_images_root=generated, timeout_seconds=10)
            def produce(attempt, revision=1, **options):
                return producer.produce_to(directory=root / "run" / f"revision-{revision:03d}" / f"attempt-{attempt:03d}",
                    pack_id="pack", creator_id="creator", series_id="series", topic_id="topic", topic_title="Topic",
                    skill_directory=skill, skill_name="submit-fixture", production_protocol="native-v1", **options)
            with patch("creatoros.integrations.native_production._production_client", client):
                with self.assertRaises(CodexProducerError):
                    produce(1)
                result = produce(2)
            self.assertEqual(len(created), 1)
            self.assertEqual([r["accepted"] for r in replies], [False, True, True])
            self.assertEqual(replies[1]["receipt_id"], replies[2]["receipt_id"])
            self.assertEqual(len(calls), 2)
            self.assertIn("只修交付结果", calls[1])
            checkpoint = load_checkpoint(result.directory)
            self.assertTrue(checkpoint.turn_completed)
            self.assertEqual(len(checkpoint.pages), 1)
            # An ordinary rework uses a new Revision but the same SDK thread.
            # Its persisted dynamic tool must have a fresh callback and receipt.
            with patch("creatoros.integrations.native_production._production_client", client):
                rework = produce(1, revision=2, thread_id="submit-thread")
            revised = load_checkpoint(rework.directory)
            self.assertEqual(revised.handoff_protocol, "tool-v1")
            self.assertTrue(revised.turn_completed)
            self.assertEqual(len(revised.pages), 1)
            self.assertEqual(len(created), 2)
            self.assertTrue(replies[-1]["accepted"])
            self.assertNotEqual(replies[-1]["receipt_id"], replies[1]["receipt_id"])


if __name__ == "__main__":
    unittest.main()
