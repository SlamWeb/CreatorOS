"""Isolated HTTP lifecycle/collector checks, not a model or browser success score."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from tests.live_agent_contract import LiveAgentContract, MANUAL_TOPICS


class LiveAgentContractTests(unittest.TestCase):
    def test_original_spa_and_test_gets_do_not_start_model_or_queue(self):
        calls = []
        def unexpected_provider():
            calls.append("unexpected")
            raise AssertionError("GET must never start a Provider")
        with TemporaryDirectory() as temporary:
            host = LiveAgentContract(temporary, provider_factory=unexpected_provider)
            try:
                with TestClient(host.app) as client:
                    response = client.get("/__contract__/scenario")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["content-type"], "application/json")
                    scenario = response.json()
                    self.assertEqual(scenario["manual_topics"], MANUAL_TOPICS)
                    page = client.get(f"/api/series/{scenario['series_id']}/topic-library?state=all").json()
                    self.assertEqual(len(page["items"]), 1)
                    self.assertEqual(page["items"][0]["selection_state"], "pending")
                    self.assertEqual(page["items"][0]["title"], "contract-pending：remember / remind")
                    self.assertEqual(client.get("/").status_code, 200)
                    state = client.get("/__contract__/state").json()
                    self.assertEqual(state["captured_turns"], 0)
                    self.assertEqual(state["before"], state["current"])
                    self.assertEqual(calls, [])
                    self.assertTrue(host.root.is_relative_to(Path(temporary).resolve()))
                    response = client.post("/__contract__/finish", json={"note": "No user turn was sent."})
                    self.assertEqual(response.status_code, 200)
                    report = response.json()
                    self.assertEqual(report["execution_status"], "failed")
                    self.assertEqual(report["automatic_task_grade"], "not_run")
                    self.assertEqual(report["semantic_assessment"], "not_run")
                    saved = (host.root / "report.json").read_bytes()
                    client.post("/__contract__/finish", json={"pretend_success": True})
                    self.assertEqual(saved, (host.root / "report.json").read_bytes())
            finally:
                host.close()

    def test_each_host_has_unique_root_and_shutdown_preserves_abandoned_run(self):
        with TemporaryDirectory() as temporary:
            hosts = [LiveAgentContract(temporary) for _ in range(2)]
            self.assertNotEqual(hosts[0].root, hosts[1].root)
            for host in hosts:
                host.close()
                report = json.loads((host.root / "report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["execution_status"], "failed")
                self.assertIsNone(report["usage"])
                self.assertTrue((host.root / "after.json").is_file())


if __name__ == "__main__":
    unittest.main()
