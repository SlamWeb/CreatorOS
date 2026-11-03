"""Guard wiring checks; no server, credentials, external process or model."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creatoros.evaluation.benchmark import nonvideo_guard as guard
from creatoros.integrations import video_production, native_production
from creatoros.integrations.codex import CodexProducer


class NonvideoGuardTests(unittest.TestCase):
    def test_frozen_selection_and_policy_validation(self):
        for ids in (None, [], ["B47"], ["B41", "B47"], ["B41", "B41"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                guard.policy(ids)
        manifest = {"selected_case_ids": ["B41", "B49"], "model_configuration": {
            "benchmark_guard": guard.policy(["B41", "B49"])}}
        self.assertTrue(guard.validate(manifest, "B41"))
        with self.assertRaises(ValueError):
            guard.validate(manifest, "B47")
        manifest["selected_case_ids"].append("B47")
        with self.assertRaises(ValueError):
            guard.validate(manifest)
        self.assertFalse(guard.validate({"model_configuration": {}}))

    def test_video_dispatch_denied_and_image_dispatch_untouched(self):
        original = video_production.produce_video
        producer = object.__new__(CodexProducer)
        kwargs = dict(directory=Path("unused"), pack_id="p", creator_id="c", series_id="s",
                      topic_id="t", topic_title="test", production_protocol="task-v1")
        with patch.object(video_production, "produce_video", original), patch.object(
                native_production, "produce_native", return_value="image result") as image:
            guard.install_video_guard()
            with self.assertRaisesRegex(RuntimeError, "video production is forbidden"):
                producer.produce_to(**kwargs, execution_spec=SimpleNamespace(kind="video"))
            image.assert_not_called()
            result = producer.produce_to(**kwargs, execution_spec=SimpleNamespace(kind="image"))
            self.assertEqual(result, "image result")
            image.assert_called_once()
        self.assertIs(video_production.produce_video, original)

    def test_publication_adapter_fails_closed(self):
        adapter = guard.DenyPublication()
        for operation in ("identity", "publish", "inspect", "find_candidates"):
            with self.subTest(operation=operation), self.assertRaisesRegex(RuntimeError, "platform operations are forbidden"):
                asyncio.run(getattr(adapter, operation)())


if __name__ == "__main__":
    unittest.main()
