"""Guard wiring checks; no server, credentials, external process or model."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
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

    def test_receipt_checks_full_policy_case_and_wiring(self):
        selected = ["B41"]
        manifest = {"selected_case_ids": selected, "model_configuration": {"benchmark_guard": guard.policy(selected)}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(guard.evidence(manifest, "B41", root)["status"], "failed")
            import json
            receipt = {"policy": guard.policy(selected), "case_id": "B41", "video_dispatch_installed": True,
                       "publication_client_installed": True, "network_sandbox": False}
            (root / "guard.json").write_text(json.dumps(receipt), encoding="utf-8")
            self.assertEqual(guard.evidence(manifest, "B41", root)["status"], "passed")
            receipt["publication_client_installed"] = False
            (root / "guard.json").write_text(json.dumps(receipt), encoding="utf-8")
            self.assertEqual(guard.evidence(manifest, "B41", root)["status"], "failed")
            self.assertIsNone(guard.evidence({"model_configuration": {}}, "B41", root))

    def test_fallback_single_report_includes_guard_and_remains_failed(self):
        import json
        from creatoros.evaluation.benchmark.run import fallback_report, DATASET
        from tests.test_benchmark_report_v2 import PRICING
        case = next(row for row in json.loads(DATASET.read_text(encoding="utf-8"))["cases"] if row["id"] == "B41")
        manifest = {"selected_case_ids": ["B41"], "dataset_id": "test", "git": {"sha": "test"},
                    "pricing": PRICING, "model_configuration": {"benchmark_guard": guard.policy(["B41"])}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fallback_report(root, case, manifest, RuntimeError("test"), "server_start")
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["execution_status"], "failed")
            self.assertEqual(report["benchmark_guard"]["status"], "failed")
            self.assertEqual(report["benchmark_guard"]["comparability"], guard.COMPARABILITY)


if __name__ == "__main__":
    unittest.main()
