"""Current tool-v1 original-image order and interrupted receipt recovery; no model."""
from contextlib import asynccontextmanager
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openai_codex import LocalImageInput
from openai_codex.generated import v2_all as sdk_types
from openai_codex.models import Notification

from creatoros.integrations.codex import CodexSdkProducer, CodexProducerError
from creatoros.integrations.native_production import load_checkpoint
from tests.smoke_native_production import create_pair, image
from tests import test_topic_references as reference_fixtures


class ToolReferenceHandoffTests(unittest.TestCase):
    setUp = reference_fixtures.ReferencesTest.setUp

    def test_original_order_and_accepted_receipt_resume_without_regeneration(self):
        catalog, pair = create_pair(self.root)
        second_raw, data = reference_fixtures.encoded("red")
        second = self.store.upload("series-a", "second.png", data)
        ref = self.store.freeze("series-a", [second["id"], self.upload["id"]], "保留原图顺序和完整要求")
        originals = [second_raw, self.raw]
        generated = self.root / "generated"
        paths = [generated / "reference-thread" / f"{n}.png" for n in (1, 2)]
        payload = {"title": "Reference delivery", "text": "Full caption", "hashtags": [], "complete": True,
                   "artifacts": [{"order": n, "source_image_path": str(path), "image_prompt": f"Actual prompt {n}",
                                  "reference_ids": [], "content": f"Full content {n}"}
                                 for n, path in enumerate(reversed(paths), 1)]}
        inputs_seen, replies, created = [], [], []

        # Same callback seam used by SubmissionExecutionTests: actual producer,
        # typed SDK image events, and real submit_artifacts handler/receipts.
        @asynccontextmanager
        async def client(deadline, cancel_event, dynamic_tools):
            self.assertEqual([tool.name for tool in dynamic_tools], ["submit_artifacts"])
            handler = dynamic_tools[0].handler
            async def start(*args, **options):
                async def turn(inputs, **controls):
                    inputs_seen.append(inputs)
                    turn_id = f"reference-turn-{len(inputs_seen)}"
                    async def stream():
                        if len(inputs_seen) == 1:
                            self.assertIn("保留原图顺序和完整要求", inputs[0].text)
                            supplied = [Path(item.path).read_bytes() for item in inputs if isinstance(item, LocalImageInput)]
                            self.assertEqual(supplied, originals)
                            for n, path in enumerate(paths):
                                image(path, (30 + n * 70, 80, 120))
                                created.append(path)
                                yield Notification("item/completed", sdk_types.ItemCompletedNotification.model_validate({
                                    "threadId": "reference-thread", "turnId": turn_id, "completedAtMs": 1,
                                    "item": {"type": "imageGeneration", "id": f"image-{n}", "status": "completed",
                                             "result": "", "savedPath": str(path)}}))
                        else:
                            self.assertIn("只修交付结果", inputs[0].text)
                            self.assertFalse([item for item in inputs if isinstance(item, LocalImageInput)])
                        replies.append(await handler(payload))
                        yield Notification("turn/completed", sdk_types.TurnCompletedNotification.model_validate({
                            "threadId": "reference-thread", "turn": {"id": turn_id, "items": [],
                                "status": "interrupted" if len(inputs_seen) == 1 else "completed"}}))
                    async def interrupt():
                        pass
                    return SimpleNamespace(id=turn_id, thread_id="reference-thread", stream=stream, interrupt=interrupt)
                return SimpleNamespace(id="reference-thread", turn=turn)
            yield SimpleNamespace(thread_start=start, thread_resume=start)

        producer = CodexSdkProducer(project_root=self.root, generated_images_root=generated, timeout_seconds=10)
        def produce(attempt):
            return producer.produce_to(directory=self.root / "run" / "revision-001" / f"attempt-{attempt:03d}",
                pack_id="pack", creator_id="creator-a", series_id="series-a", topic_id="topic-a", topic_title="Reference",
                skill_directory=Path(pair.production.local_path), skill_name=pair.production.name,
                task_references=ref.model_dump(), production_protocol="native-v1")
        with patch("creatoros.integrations.native_production._production_client", client):
            with self.assertRaises(CodexProducerError):
                produce(1)
            result = produce(2)
            # Complete delivery is resolved offline on subsequent replay.
            produce(3)
        self.assertEqual(len(created), 2)
        self.assertEqual(len(inputs_seen), 2)
        self.assertTrue(all(reply["accepted"] for reply in replies), replies)
        self.assertEqual(replies[0]["receipt_id"], replies[1]["receipt_id"])
        checkpoint = load_checkpoint(result.directory)
        self.assertEqual(checkpoint.handoff_protocol, "tool-v1")
        self.assertTrue(checkpoint.turn_completed)
        self.assertEqual([page.sha256 for page in checkpoint.pages],
                         [hashlib.sha256(path.read_bytes()).hexdigest() for path in reversed(paths)])
        self.assertEqual([Path(item.path).read_bytes() for item in ref.images], originals)


if __name__ == "__main__":
    unittest.main()
