import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creatoros.evaluation.comparison import CONTRACT_FIELDS
from creatoros.evaluation.registry import EvalRegistry
from creatoros.web.eval_registry_routes import eval_registry_routes


class ComparisonApiTests(unittest.TestCase):
    def test_separate_runs_compare_without_touching_historical_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = EvalRegistry(Path(tmp) / "registry.sqlite")
            metadata = {key: "same-" + key for key in CONTRACT_FIELDS}
            for round_id, run_id, status, tokens in (("R1", "T1", "failed", 10), ("R2", "T2", "passed", 8)):
                registry.register_round({"id": round_id, "title": round_id, "code_version": round_id})
                registry.register_run({"id": run_id, "round_id": round_id, "suite_id": "TOOLS",
                                       "suite_version": "v1", "planned_cases": ["A", "B"], "metadata": metadata})
                registry.transition(run_id, "freezing")
                registry.transition(run_id, "running")
                registry.add_case_result(run_id, {"case_id": "A", "status": status,
                    "case_definition_hash": "sha256-a", "metrics": {"input_tokens": tokens,
                    "usage_scope": "provider_actual_agent"}, "evidence": {"execution_status": "completed"}})
                registry.transition(run_id, "completed")
            app = FastAPI()
            app.include_router(eval_registry_routes(registry))
            with TestClient(app) as client:
                response = client.get("/api/eval/v1/compare", params={"baseline_run": "T1", "candidate_run": "T2"})
                self.assertEqual(response.status_code, 200)
                result = response.json()
                self.assertEqual(result["outcomes"]["paired_decidable"], 1)
                self.assertEqual(result["metrics"]["input_tokens"]["paired_cases"], 1)
                self.assertEqual(result["cases"][1]["reason"], "missing_result")
                self.assertEqual(client.get("/api/eval/v1/compare", params={"baseline_run": "MISSING", "candidate_run": "T2"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
