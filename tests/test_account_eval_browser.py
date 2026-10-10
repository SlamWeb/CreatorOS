"""Real isolated HTTP/storage lifecycle tests; never model quality scores.

No Agent turn is sent. The sole injected fault is fixture setup failure.
"""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from creatoros.evaluation.browser import BrowserEvaluation
from creatoros.evaluation.fixture import E01Fixture
from creatoros.evaluation.store import EvalStore
from tests.agent_studio_support import serve
import httpx
from time import monotonic, sleep


class BrowserEvaluationLifecycleTests(unittest.TestCase):
    def test_test_only_route_precedes_spa_and_never_calls_model(self):
        with TemporaryDirectory() as temporary:
            host = BrowserEvaluation("E01", temporary)
            try:
                with TestClient(host.app) as client:
                    response = client.get("/__live_eval__/scenario")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["content-type"], "application/json")
                    data = response.json()
                    self.assertEqual(set(data), {"case_id", "run_id", "creator_name", "creator_id", "query"})
                    self.assertEqual(data["case_id"], "E01")
                    self.assertNotIn("B_PRIVATE", json.dumps(data))
                    self.assertIsNone(host.captured)
                    hashes = json.loads((host.root / "source_hashes.json").read_text(encoding="utf-8"))
                    expected = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
                    self.assertEqual(host.report["code_fingerprint"], expected)
            finally:
                host.fixture.close()

    def test_abandoned_ui_is_failed_and_collection_is_idempotent(self):
        with TemporaryDirectory() as temporary:
            host = BrowserEvaluation("E01", temporary)
            try:
                report = host.finish({"session_id": "", "completed": False, "error": "浏览器未提交"}, "http://127.0.0.1:1")
                self.assertEqual(report["execution_status"], "failed")
                self.assertEqual(report["auto_status"], "failed")
                self.assertIsNone(report["usage"])
                self.assertIsNone(host.captured)
                self.assertTrue((host.root / "before.json").is_file())
                self.assertEqual(EvalStore(host.root.parent).detail(host.root.name)["status"], "failed")
                saved = (host.root / "report.json").read_bytes()
                second = host.finish({"session_id": "pretend", "completed": True}, "http://127.0.0.1:1")
                self.assertEqual(second, report)
                self.assertEqual((host.root / "report.json").read_bytes(), saved)
            finally:
                host.fixture.close()

    def test_view_validation_and_failure_cannot_be_upgraded_on_replay(self):
        with TemporaryDirectory() as temporary:
            host = BrowserEvaluation("E02", temporary)
            try:
                with TestClient(host.app) as client:
                    self.assertEqual(client.post("/__live_eval__/view", json=[]).status_code, 422)
                    self.assertEqual(client.post("/__live_eval__/view", json={"run_id": host.root.name}).status_code, 409)
                    host.finish({"session_id": "", "completed": False}, "http://127.0.0.1:1")
                    original = (host.root / "report.json").read_bytes()
                    self.assertEqual(client.post("/__live_eval__/view", json={"run_id": "another"}).status_code, 409)
                    self.assertEqual((host.root / "report.json").read_bytes(), original)
                    failed = client.post("/__live_eval__/view", json={"run_id": host.root.name,
                        "completed": False, "error": "页面未显示", "posts_after_refresh": 0}).json()
                    replay = client.post("/__live_eval__/view", json={"run_id": host.root.name,
                        "completed": True, "posts_after_refresh": 0}).json()
                    self.assertEqual(failed, replay)
                    self.assertEqual(replay["auto_status"], "failed")
                    check = next(row for row in replay["checks"] if row["id"] == "browser_eval_view")
                    self.assertEqual(check["status"], "failed")
            finally:
                host.fixture.close()

    def test_setup_fault_keeps_failed_report_and_releases_database(self):
        with TemporaryDirectory() as temporary:
            # Controlled local setup failure, not a simulated model/tool result.
            with patch.object(E01Fixture, "_seed", side_effect=ValueError("受控初始化故障")):
                with self.assertRaisesRegex(ValueError, "受控初始化故障"):
                    BrowserEvaluation("E01", temporary)
            root = next(Path(temporary).iterdir())
            report = EvalStore(temporary).detail(root.name)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["error"]["kind"], "setup")
            (root / "fixture" / "studio.db").unlink()  # Windows handle really released.

    def test_collection_releases_post_scope_lock_before_real_loopback_probes(self):
        with TemporaryDirectory() as temporary:
            host = BrowserEvaluation("E02", temporary)
            try:
                with serve(host.app) as base, httpx.Client(base_url=base, trust_env=False, timeout=5) as client:
                    created = client.post("/api/agent/sessions", json={"creator_id": host.fixture.creator_a})
                    self.assertEqual(created.status_code, 201)
                    response = client.post("/__live_eval__/finish", json={"session_id": created.json()["id"], "completed": False})
                    self.assertEqual(response.status_code, 202)
                    deadline = monotonic() + 10
                    while monotonic() < deadline:
                        response = client.get("/__live_eval__/result")
                        if response.status_code == 200:
                            break
                        sleep(.05)
                    self.assertEqual(response.status_code, 200)
                    probes = json.loads((host.root / "probe.json").read_text(encoding="utf-8"))
                    self.assertEqual(len(probes), 4)
                    for probe in probes:
                        self.assertTrue(probe["result"]["is_error"])
                        self.assertEqual(json.loads(probe["result"]["content"])["error"], "agent_scope_rejected")
                    self.assertIsNone(host.captured)
            finally:
                host.fixture.close()


if __name__ == "__main__":
    unittest.main()
