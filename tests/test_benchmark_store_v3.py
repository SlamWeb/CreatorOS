"""Real FastAPI reader on isolated files; no mocks, models, or score rewriting."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creatoros.evaluation.benchmark.store import BenchmarkStore
from creatoros.web.eval_routes import eval_routes
from tests.benchmark_surface_fixture import seed_surface_batch


class BenchmarkStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="benchmark-reader-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "batches"
        self.store = BenchmarkStore(self.root)
        app = FastAPI()
        app.include_router(eval_routes(self.store))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_current_fifty_cases_and_nonexistent_root_are_read_only(self):
        value = self.client.get("/api/eval").json()
        self.assertEqual(len(value["cases"]), 50)
        self.assertEqual(value["batches"], [])
        self.assertFalse(self.root.exists())
        self.assertEqual(self.client.post("/api/eval/runs/x/review", json={}).status_code, 404)

    def test_stored_scores_frozen_definition_and_evidence_do_not_mutate_files(self):
        fixture = seed_surface_batch(self.root)
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()}
        base = f"/api/eval/batches/{fixture['batch_id']}"
        batch = self.client.get(base).json()
        self.assertEqual(batch["summary"]["scores"]["program"]["score"], 75)
        self.assertIsNone(batch["summary"]["scores"]["semantic"]["score"])
        detail = self.client.get(base + "/cases/B11").json()
        self.assertEqual(detail["frozen_definition"]["id"], "B11")
        evidence = self.client.get(base + f"/cases/B11/evidence/{fixture['record_id']}?name=calls.json").json()
        self.assertNotIn("synthetic-sensitive-value", json.dumps(evidence))
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()})

    def test_old_batch_has_no_synthetic_new_scores(self):
        fixture = seed_surface_batch(self.root, legacy=True)
        batch = self.client.get(f"/api/eval/batches/{fixture['batch_id']}").json()
        self.assertNotIn("scores", batch["summary"])
        self.assertEqual(batch["summary"]["dataset_id"], "controlled-old-protocol")

    def test_evidence_cannot_escape_or_cross_case(self):
        fixture = seed_surface_batch(self.root)
        base = f"/api/eval/batches/{fixture['batch_id']}"
        path = f"/evidence/{fixture['record_id']}"
        self.assertEqual(self.client.get(base + "/cases/B12" + path + "?name=calls.json").status_code, 404)
        self.assertEqual(self.client.get(base + "/cases/B11" + path + "?name=../summary.json").status_code, 404)
        self.assertEqual(self.client.get("/api/eval/batches/not-a-batch").status_code, 404)
        summary_path = self.root / fixture['batch_id'] / 'summary.json'
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        summary['cases'][0]['record_paths'] = ['../secret/report.json']
        summary_path.write_text(json.dumps(summary), encoding='utf-8')
        detail = self.client.get(base + '/cases/B11').json()
        self.assertEqual(detail['records'], [])
        self.assertTrue(detail['errors'])
