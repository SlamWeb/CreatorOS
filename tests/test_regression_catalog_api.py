import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creatoros.evaluation.registry import EvalRegistry
from creatoros.web.eval_registry_routes import eval_registry_routes


class RegressionCatalogApiTests(unittest.TestCase):
    def test_real_catalog_is_read_only_and_preserves_unready_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "unused.sqlite3"
            app = FastAPI()
            app.include_router(eval_registry_routes(EvalRegistry(db_path)))
            with TestClient(app) as client:
                response = client.get("/api/eval/v1/suites")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                suites = response.json()["suites"]
                full = next(s for s in suites if s["id"] == "full-v1")
                self.assertEqual(full["counts"]["defined"], 120)
                self.assertEqual(full["scope_status"], "partial")
                self.assertTrue(any(c["execution_readiness"] == "requires_review" for c in full["cases"]))
                self.assertEqual(client.post("/api/eval/v1/suites").status_code, 405)
            self.assertFalse(db_path.exists())

    def test_suite_runs_only_show_explicit_binding_not_guessed_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = EvalRegistry(Path(tmp) / "registry.sqlite")
            registry.register_round({"id": "round1", "title": "test", "code_version": "v1"})
            binding = {"catalog_suite_id": "regular-v1", "catalog_suite_version": "1",
                       "planned_instance_ids": ["base:B05", "base:B06"],
                       "selected_instance_ids": ["base:B05"], "selection_kind": "partial"}
            registry.register_run({"id": "run1", "round_id": "round1", "suite_id": "tool",
                                   "suite_version": "v1", "planned_cases": ["B05"],
                                   "metadata": {"catalog_binding": binding}})
            registry.register_run({"id": "run2", "round_id": "round1", "suite_id": "other",
                                   "suite_version": "v1", "planned_cases": ["B05"], "metadata": {}})
            registry.register_run({"id": "run3", "round_id": "round1", "suite_id": "tool",
                                   "suite_version": "v1", "planned_cases": ["B05"],
                                   "metadata": {"catalog_binding": {**binding, "selection_kind": "complete"}}})
            app = FastAPI()
            app.include_router(eval_registry_routes(registry))
            with TestClient(app) as client:
                items = client.get("/api/eval/v1/suites/regular-v1/runs").json()["items"]
                self.assertEqual(len(items), 2)
                by_id = {row["run_id"]: row for row in items}
                self.assertEqual((by_id["run1"]["planned_count"], by_id["run1"]["selected_count"]), (2, 1))
                self.assertEqual(by_id["run1"]["selection_kind"], "partial")
                self.assertEqual(by_id["run3"]["selection_kind"], "unknown")
                self.assertEqual(client.get("/api/eval/v1/suites/full-v1/runs").json(), {"items": []})

    def test_bad_catalog_is_error_not_empty_success(self):
        app = FastAPI()
        app.include_router(eval_registry_routes(EvalRegistry(Path(tempfile.gettempdir()) / "not-used-eval.sqlite")))
        with patch("creatoros.web.eval_registry_routes.catalog", side_effect=ValueError("bad manifest")):
            with TestClient(app) as client:
                response = client.get("/api/eval/v1/suites")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], "catalog_unavailable")

    def test_corrupted_run_record_is_reported_not_hidden_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.sqlite"
            registry = EvalRegistry(path)
            registry.register_round({"id": "round1", "title": "test", "code_version": "v1"})
            registry.register_run({"id": "run1", "round_id": "round1", "suite_id": "tool",
                                   "suite_version": "v1", "planned_cases": ["B05"]})
            db = sqlite3.connect(path)
            try:
                db.execute("UPDATE eval_runs SET payload=? WHERE id=?", ("not-json", "run1"))
                db.commit()
            finally:
                db.close()
            app = FastAPI()
            app.include_router(eval_registry_routes(registry))
            with TestClient(app) as client:
                response = client.get("/api/eval/v1/suites/regular-v1/runs")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], "registry_unavailable")


if __name__ == "__main__":
    unittest.main()
