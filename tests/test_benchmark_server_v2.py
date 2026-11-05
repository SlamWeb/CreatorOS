"""Real HTTP routing of the controller; no model is involved."""
import unittest

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from creatoros.evaluation.benchmark.server import prioritize_control_routes, research_inventory_for_judge


class BenchmarkControlRoutingTests(unittest.TestCase):
    def test_research_inventory_is_bounded_and_remains_partial(self):
        evidence = {"status": "needs_review", "source_inventory": [{"id": "s1", "url": "https://example.test/a",
                    "title": "x" * 700, "agent_open": "attempted_no_body", "host_fetch": "not_attempted",
                    "search_snippets": [{"text": "a" * 1200, "event_line": 1}, {"text": "b", "event_line": 2},
                                        {"text": "c", "event_line": 3}]}]}
        item = research_inventory_for_judge(evidence)[0]
        self.assertEqual(item["agent_open"], "attempted_no_body")
        self.assertEqual(len(item["title"]), 500)
        self.assertEqual(len(item["search_snippets"]), 2)
        self.assertEqual(len(item["search_snippets"][0]["text"]), 1000)
        self.assertTrue(item["search_snippets"][0]["excerpt_may_be_truncated"])
        self.assertEqual(len(evidence["source_inventory"][0]["search_snippets"]), 3)

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
