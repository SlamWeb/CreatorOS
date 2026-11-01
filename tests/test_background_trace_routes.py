"""Real routes and persisted trace files; no model or production calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from creatoros.ai.context import ModelContext
from creatoros.web.chat import AgentChatService
from creatoros.web.chat_routes import chat_routes
from creatoros.session.context_trace import trace_path
from creatoros.session.request_trace import RequestSnapshots


class BackgroundTraceRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.host = AgentChatService(Path(temporary.name))
        self.sid = self.host.create()["id"]
        self.other = self.host.create()["id"]
        self.plan_turn = "plan-" + "a" * 64
        self.request = uuid4().hex
        self.normal_turn = str(uuid4())
        doc = self.host._read(self.sid)
        doc["requests"] = [{"id": tid, "status": "idle"} for tid in [self.normal_turn, self.plan_turn]]
        self.host._save(doc)
        session_file = self.host._path(self.sid).with_name("messages.json")
        trace_path(session_file).write_text(json.dumps({"event": "finished", "turn_id": self.plan_turn,
            "request_id": self.request}) + "\n", encoding="utf-8")
        RequestSnapshots(session_file).begin(self.request, self.plan_turn,
            ModelContext.from_messages([{"role": "user", "content": "saved plan result"}], []))
        self.before = {p: p.read_bytes() for p in Path(temporary.name).rglob("*") if p.is_file()}
        app = FastAPI()
        app.include_router(chat_routes(self.host))
        self.client = TestClient(app)
        self.base = f"/api/agent/sessions/{self.sid}/turn-trace/"

    def tearDown(self):
        self.assertTrue(all(p.read_bytes() == content for p, content in self.before.items()))
        self.client.close()

    def test_background_turn_index_and_request_snapshot(self):
        response = self.client.get(self.base + self.plan_turn)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["requests"][0]["request_id"], self.request)
        snapshot = self.client.get(self.base + self.plan_turn + "/requests/" + self.request)
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        self.assertEqual(snapshot.json()["turn_id"], self.plan_turn)

    def test_normal_turn_still_works(self):
        self.assertEqual(self.client.get(self.base + self.normal_turn).status_code, 200)

    def test_foreign_turn_and_mismatched_request_are_rejected(self):
        foreign = f"/api/agent/sessions/{self.other}/turn-trace/{self.plan_turn}"
        self.assertEqual(self.client.get(foreign).status_code, 404)
        self.assertEqual(self.client.get(foreign + "/requests/" + self.request).status_code, 404)
        self.assertEqual(self.client.get(self.base + self.normal_turn + "/requests/" + self.request).status_code, 404)

    def test_unknown_or_malformed_turn_is_rejected(self):
        self.assertEqual(self.client.get(self.base + "plan-" + "b" * 64).status_code, 404)
        for invalid in ["bad.id", "a" * 129, "bad%20id"]:
            self.assertEqual(self.client.get(self.base + invalid).status_code, 422)


if __name__ == "__main__":
    unittest.main()
