"""Real HTTP routing of the controller; no model is involved."""
import unittest

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from creatoros.evaluation.benchmark.server import prioritize_control_routes


class BenchmarkControlRoutingTests(unittest.TestCase):
    def test_control_receipts_precede_existing_spa_catchall(self):
        app = FastAPI()

        @app.get("/{path:path}")
        def spa(path):
            return HTMLResponse("<!doctype html><div>Studio</div>")

        @app.get("/__benchmark__/scenario")
        def scenario():
            return {"case_id": "B01"}

        @app.get("/__benchmark__/result")
        def result():
            return JSONResponse({"status": "running"}, status_code=202)

        prioritize_control_routes(app)
        with TestClient(app) as client:
            self.assertEqual(client.get("/__benchmark__/scenario").json(), {"case_id": "B01"})
            self.assertEqual(client.get("/__benchmark__/result").status_code, 202)
            self.assertIn("Studio", client.get("/skills").text)
