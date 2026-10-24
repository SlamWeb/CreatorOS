"""Host contracts only; paid model production is verified by a separate live run."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from creatoros.context import RuntimeContext
from creatoros.tools.model_projection import project_model_data
from creatoros.tools.results import ToolResult
from creatoros.tools.skill_iterations import ManageSkillIterationArgs, manage_skill_iteration
from creatoros.web.chat import ACCOUNT_INSTRUCTIONS, ACCOUNT_TOOLS, AgentChatService, STUDIO_TOOLS
from creatoros.web.chat_links import tool_links
from creatoros.web.chat_routes import ChatTurnRequest
from creatoros.web.skill_iteration_routes import skill_iteration_routes


class SkillIterationAgentTests(unittest.TestCase):
    def test_mode_is_strict_and_part_of_persisted_replay_identity(self):
        with self.assertRaises(ValidationError):
            ChatTurnRequest(request_id=str(uuid4()), expected_version=0, iteration_mode="true")
        with TemporaryDirectory() as directory:
            provider = SimpleNamespace(client=SimpleNamespace(close=lambda: None))
            host = AgentChatService(Path(directory), provider_factory=lambda: provider)
            session = host.create()
            request_id = str(uuid4())
            with patch("creatoros.web.chat.Thread") as thread:
                host.submit(session["id"], request_id, "优化这个Skill", 0, "http://127.0.0.1:1", iteration_mode=True)
                # The target also follows iteration_mode; assert by signature,
                # not by its historical last positional index.
                from inspect import signature
                call = signature(host._run).bind(*thread.call_args.kwargs["args"])
                self.assertTrue(call.arguments["iteration_mode"])
                host.submit(session["id"], request_id, "优化这个Skill", 0, "http://127.0.0.1:1", iteration_mode=True)
                self.assertEqual(thread.call_count, 1)
                with self.assertRaises(HTTPException) as conflict:
                    host.submit(session["id"], request_id, "优化这个Skill", 0, "http://127.0.0.1:1", iteration_mode=False)
                self.assertEqual(conflict.exception.status_code, 409)
            stored = json.loads(host._path(session["id"]).read_text(encoding="utf-8"))
            self.assertTrue(stored["requests"][-1]["iteration_mode"])
            self.assertTrue(host.get(session["id"])["entries"][-1]["iteration_mode"])

    def test_legacy_replay_defaults_mode_to_false_without_new_execution(self):
        with TemporaryDirectory() as directory:
            host = AgentChatService(Path(directory), provider_factory=lambda: self.fail("no provider on replay"))
            session = host.create()
            doc = host._read(session["id"])
            request_id = str(uuid4())
            doc["requests"].append({"id": request_id, "text": "old", "status": "idle"})
            host._save(doc)
            before = host._path(session["id"]).read_bytes()
            host.submit(session["id"], request_id, "old", 0, "http://127.0.0.1:1")
            self.assertEqual(host._path(session["id"]).read_bytes(), before)
            with self.assertRaises(HTTPException):
                host.submit(session["id"], request_id, "old", 0, "http://127.0.0.1:1", iteration_mode=True)

    def test_tool_identity_is_host_owned_and_stable_for_replay(self):
        context = RuntimeContext(Path("."), agent_session_id=str(uuid4()), user_request_id=str(uuid4()))
        calls = []
        class Client:
            def request(self, method, path, payload=None):
                calls.append((method, path, payload))
                return {"id": "a" * 64, "status": "queued"}
        def call(action, supplied):
            self.assertIs(supplied, context)
            return ToolResult(json.dumps(action(Client())))
        with patch("creatoros.tools.skill_iterations._call", side_effect=call):
            for _ in range(2):
                manage_skill_iteration("start", series_id="series", skill_id="skill", topic_id="topic", context=context)
            manage_skill_iteration("start", series_id="series", skill_id="skill", topic_id="topic",
                                   instructions="rephrased", context=context)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[0][2]["request_id"], calls[2][2]["request_id"])
        self.assertEqual(len(calls[0][2]["request_id"]), 64)
        self.assertNotIn("origin_session_id", calls[0][2])
        self.assertTrue(manage_skill_iteration("start", series_id="series", skill_id="skill", topic_id="topic").is_error)

    def test_action_validation_and_bounded_budget(self):
        for payload in ({"action": "start"}, {"action": "continue", "iteration_id": "a" * 64},
                        {"action": "get", "iteration_id": "a" * 64, "instructions": "change"},
                        {"action": "start", "series_id": "s", "skill_id": "x", "topic_id": "t", "max_rounds": 4}):
            with self.assertRaises(ValidationError):
                ManageSkillIterationArgs(**payload)

    def test_projection_keeps_task_summary_not_full_private_history(self):
        raw = {"id": "b" * 64, "status": "reviewing", "revision": 2, "round_count": 1,
               "judge": {"version": 2, "threshold": 80, "criteria": ["long"]},
               "latest_feedback": {"summary": "too bright", "score": 50, "raw": "private"},
               "trace": "full log", "rounds": [{"full": "history"}], "local_path": "secret"}
        projected = project_model_data("manage_skill_iteration", raw)
        self.assertEqual(projected["judge"], {"version": 2, "threshold": 80})
        self.assertEqual(projected["latest_feedback"], {"summary": "too bright", "score": 50})
        self.assertNotIn("trace", projected)
        self.assertNotIn("rounds", projected)
        self.assertNotIn("local_path", projected)
        self.assertEqual(tool_links("manage_skill_iteration", json.dumps(raw))[0]["url"], "/skills?iteration=" + "b" * 64)
        versions = project_model_data("get_producer_skill", {"family_id": "family", "items": [
            {"id": "exact-v2", "version_status": "candidate", "digest": "f" * 64, "local_path": "secret"}]})
        self.assertEqual(versions["items"][0]["digest"], "f" * 64)
        self.assertNotIn("local_path", versions["items"][0])

    def test_one_capability_is_exposed_and_prompt_does_not_require_column_switch(self):
        self.assertIn("manage_skill_iteration", ACCOUNT_TOOLS)
        self.assertIn("manage_skill_iteration", STUDIO_TOOLS)
        self.assertIn("include_versions=true", ACCOUNT_INSTRUCTIONS)
        self.assertIn("仅作用本次", ACCOUNT_INSTRUCTIONS)
        self.assertNotIn("候选需要用户在网页试用", ACCOUNT_INSTRUCTIONS)

    def test_route_resolves_origin_from_host_session_and_rejects_model_supplied_session(self):
        calls = []
        class Service:
            def create(self, **payload):
                calls.append(payload)
                return {"id": "c" * 64, "status": "queued"}
            def list(self, **payload):
                calls.append(payload)
                return {"items": []}
        chat = SimpleNamespace(get=lambda sid: {"scope_kind": "creator", "creator_id": "owned"})
        app = FastAPI()
        app.include_router(skill_iteration_routes(Service(), chat))
        with TestClient(app) as client:
            payload = {"series_id": "s", "skill_id": "skill", "topic_id": "topic", "request_id": "request"}
            response = client.post("/api/skill-iterations", json=payload,
                                   headers={"x-creatoros-agent-session": "bound-session"})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(calls[-1]["origin_session_id"], "bound-session")
            rejected = client.post("/api/skill-iterations", json={**payload, "origin_session_id": "foreign"})
            self.assertEqual(rejected.status_code, 422)
            rejected = client.post("/api/skill-iterations", json={**payload, "attachment_ids": ["a"]})
            self.assertEqual(rejected.status_code, 422)
            client.get("/api/skill-iterations?creator_id=other", headers={"x-creatoros-agent-session": "bound-session"})
            self.assertEqual(calls[-1]["creator_id"], "owned")


if __name__ == "__main__":
    unittest.main()
