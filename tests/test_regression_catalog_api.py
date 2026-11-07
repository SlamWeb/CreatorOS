import tempfile
import unittest
import copy
import sqlite3
import json
import shutil
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creatoros.evaluation.registry import EvalRegistry
from creatoros.evaluation.regression_catalog import ROOT, build_catalog, catalog
from creatoros.evaluation.benchmark.natural_suite import canonical_digest
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
                self.assertEqual([s["id"] for s in suites], ["regular-v5", "full-v5", "regular-v4", "full-v4", "regular-v3", "full-v3", "regular-v2", "full-v2", "regular-v1", "full-v1"])
                self.assertEqual([s["historical"] for s in suites], [False, False, True, True, True, True, True, True, True, True])
                full = next(s for s in suites if s["id"] == "full-v5")
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
            registry.register_run({"id": "run4", "round_id": "round1", "suite_id": "tool",
                                   "suite_version": "v1", "planned_cases": ["B05"],
                                   "metadata": {"catalog_binding": {**binding, "catalog_suite_id": "regular-v2", "catalog_suite_version": "2"}}})
            app = FastAPI()
            app.include_router(eval_registry_routes(registry))
            with TestClient(app) as client:
                items = client.get("/api/eval/v1/suites/regular-v1/runs").json()["items"]
                self.assertEqual(len(items), 2)
                by_id = {row["run_id"]: row for row in items}
                self.assertEqual((by_id["run1"]["planned_count"], by_id["run1"]["selected_count"]), (2, 1))
                self.assertEqual(by_id["run1"]["selection_kind"], "partial")
                self.assertEqual(by_id["run3"]["selection_kind"], "unknown")
                new_items = client.get("/api/eval/v1/suites/regular-v2/runs").json()["items"]
                self.assertEqual([row["run_id"] for row in new_items], ["run4"])
                self.assertEqual(client.get("/api/eval/v1/suites/full-v1/runs").json(), {"items": []})

    def test_bad_catalog_is_error_not_empty_success(self):
        app = FastAPI()
        app.include_router(eval_registry_routes(EvalRegistry(Path(tempfile.gettempdir()) / "not-used-eval.sqlite")))
        with patch("creatoros.web.eval_registry_routes.catalog", side_effect=ValueError("bad manifest")):
            with TestClient(app) as client:
                response = client.get("/api/eval/v1/suites")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], "catalog_unavailable")

    def test_one_broken_historical_version_does_not_hide_the_other(self):
        current = catalog()
        v1 = catalog(ROOT / "docs/agent-eval/regression-catalog-v1/catalog.json")
        v3 = catalog(ROOT / "docs/agent-eval/regression-catalog-v3/catalog.json")
        v4 = catalog(ROOT / "docs/agent-eval/regression-catalog-v4/catalog.json")
        def load(path=None):
            if path is None:
                return current
            if "regression-catalog-v2" in str(path):
                raise ValueError("damaged v2")
            if "regression-catalog-v3" in str(path):
                return v3
            if "regression-catalog-v4" in str(path):
                return v4
            return v1
        app = FastAPI()
        app.include_router(eval_registry_routes(EvalRegistry(Path(tempfile.gettempdir()) / "not-used-eval.sqlite")))
        with patch("creatoros.web.eval_registry_routes.catalog", side_effect=load):
            with TestClient(app) as client:
                response = client.get("/api/eval/v1/suites")
                self.assertEqual(response.status_code, 200)
                self.assertEqual([row["id"] for row in response.json()["suites"]],
                                 ["regular-v5", "full-v5", "regular-v4", "full-v4", "regular-v3", "full-v3", "regular-v1", "full-v1"])
                self.assertIn("v2", response.json()["historical_error"])

    def test_stale_historical_source_does_not_block_current_or_change_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = EvalRegistry(Path(tmp) / "registry.sqlite")
            registry.register_round({"id": "old-round", "title": "old", "code_version": "v1"})
            binding = {"catalog_suite_id": "regular-v1", "catalog_suite_version": "1",
                       "planned_instance_ids": ["base:B05"], "selected_instance_ids": ["base:B05"],
                       "selection_kind": "partial", "frozen_catalog": {"evidence": "original"}}
            registry.register_run({"id": "old-run", "round_id": "old-round", "suite_id": "tool",
                                   "suite_version": "v1", "planned_cases": ["B05"],
                                   "metadata": {"catalog_binding": binding}})
            before = registry.get_run("old-run")
            root = Path(tmp) / "repo"
            for relative in ("docs/agent-eval/benchmark-v2/cases.json", "docs/agent-eval/natural-v2/cases.json",
                             "docs/agent-eval/expansion-50-v1/cases.json", "docs/agent-eval/tool-calling-v1/cases.json"):
                dest = root / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, dest)
            adapter = "docs/agent-eval/source-snapshots-v1/72f4ac13ac545ff1d681e143819d521fcf035db304129b0890c1bb01bb588c9e.json"
            (root / adapter).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / adapter, root / adapter)
            source = root / "docs/agent-eval/expansion-50-v1/cases.json"
            changed = json.loads(source.read_text(encoding="utf-8"))
            changed["cases"][0]["title"] += " (updated)"
            source.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
            updated = json.loads((ROOT / "docs/agent-eval/regression-catalog-v2/catalog.json").read_text(encoding="utf-8"))
            updated["sources"][2]["canonical_sha256"] = canonical_digest(changed)
            current = build_catalog(updated, repo_root=root)
            current = copy.deepcopy(current)
            for suite in current["suites"]:
                suite["id"] = suite["id"].replace("-v2", "-v5")
            stale = json.loads((ROOT / "docs/agent-eval/regression-catalog-v1/catalog.json").read_text(encoding="utf-8"))
            def load(path=None):
                if path is None:
                    return current
                if "regression-catalog-v2" in str(path):
                    return build_catalog(updated, repo_root=root)
                if "regression-catalog-v3" in str(path):
                    raise ValueError("v3 unavailable in isolated fixture")
                if "regression-catalog-v4" in str(path):
                    raise ValueError("v4 unavailable in isolated fixture")
                return build_catalog(stale, repo_root=root)
            app = FastAPI()
            app.include_router(eval_registry_routes(registry))
            with patch("creatoros.web.eval_registry_routes.catalog", side_effect=load):
                with TestClient(app) as client:
                    data = client.get("/api/eval/v1/suites").json()
                    self.assertEqual([row["id"] for row in data["suites"]],
                                     ["regular-v5", "full-v5", "regular-v2", "full-v2"])
                    self.assertIn("历史目录暂不可用", data["historical_error"])
                    self.assertEqual(client.get("/api/eval/v1/suites/regular-v1/runs").json()["items"][0]["run_id"], "old-run")
            self.assertEqual(registry.get_run("old-run"), before)

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
