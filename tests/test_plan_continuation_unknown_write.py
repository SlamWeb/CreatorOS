"""Unknown plan writes stop only the current continuation, without replay."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

import httpx

from creatoros.agent.plans import PlanService
from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.integrations.studio import StudioClient, StudioClientError
from creatoros.session.snapshot import load_messages
from creatoros.storage import Base, ContentRepository, CreatorPlatform, Database
from creatoros.tools.execution import execute_tool_call
from creatoros.tools.results import ToolResult
from creatoros.web.chat import AgentChatService


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0
        self.client = SimpleNamespace(close=lambda: None)

    def stream(self, context):
        response = self.responses[self.calls]
        self.calls += 1
        if not response:
            yield TextDelta("已完成答复。")
            yield StreamEnd("stop")
            return
        for index, (name, arguments) in enumerate(response):
            yield ToolCallDelta(index, f"call-{self.calls}-{index}", name, json.dumps(arguments))
        yield StreamEnd("tool_calls")


class ContinuationUnknownWriteTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="continuation-unknown-write-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = Database("sqlite:///" + (self.root / "isolated.db").as_posix())
        self.addCleanup(self.db.close)
        Base.metadata.create_all(self.db.engine)
        ContentRepository(self.db).create_creator(creator_id="isolated", display_name="isolated",
                                                  platform=CreatorPlatform.XIAOHONGSHU)
        self.responses = []
        self.providers = []
        self.requests = []
        self.committed = 0
        self.mode = "commit_unknown"

        def provider():
            instance = ScriptedProvider(self.responses)
            self.providers.append(instance)
            return instance

        self.chat = AgentChatService(self.root / "chats", provider_factory=provider,
                                     creator_lookup=lambda _: SimpleNamespace(is_active=True))
        self.plans = PlanService(self.db, self.chat)
        self.chat.bind_plan_service(self.plans)
        self.sid = self.chat.create("isolated")["id"]
        self.steps = [
            {"id": "review", "title": "review", "tool": "request_review", "arguments": {"question": "check"}},
            {"id": "image", "title": "image", "tool": "start_content_run", "depends_on": ["review"],
             "arguments": {"action": "tasks", "topic_id": "isolated-topic", "tasks": [
                 {"kind": "image", "title": "image", "brief": "draw"}]}},
        ]
        plan = self.plans.create(self.sid, "request", "plan", self.steps, "http://127.0.0.1:1")
        self.pid = plan["id"]
        self.plans.advance()
        self.version = self.plans.get(self.sid, self.pid)["version"]

        owner = self

        class IsolatedClient:
            def __init__(self, base_url, **kwargs):
                self.base_url = base_url

            def close(self):
                pass

            def request(self, method, path, *, payload=None, **kwargs):
                owner.requests.append((method, path, payload))
                if method == "GET":
                    if owner.mode == "read_invalid":
                        raise StudioClientError("controlled invalid read response", "studio_invalid_response")
                    if owner.mode == "read_failed":
                        raise StudioClientError("controlled read failure", "studio_read_failed")
                    return owner.plans.get(owner.sid, owner.pid)
                if owner.mode == "pre_route_rejection":
                    raise StudioClientError("scope rejected before route", "agent_scope_rejected", status_code=403)
                if owner.mode == "connection_refused":
                    raise StudioClientError("connection refused", "studio_unavailable")
                if owner.mode != "ordinary_unknown":
                    current = owner.plans.get(owner.sid, owner.pid)
                    if method == "POST":
                        owner.plans.action(owner.sid, owner.pid, current["version"], payload["action"],
                                           payload.get("step_id"))
                    else:
                        owner.plans.edit(owner.sid, owner.pid, current["version"], payload["steps"])
                    owner.committed += 1
                if owner.mode == "commit_success":
                    return {"id": owner.pid}
                if owner.mode == "commit_500":
                    raise StudioClientError("response after commit", "http_error", status_code=500)
                if owner.mode == "commit_503_unavailable":
                    raise StudioClientError("response after commit", "studio_unavailable", status_code=503)
                if owner.mode == "commit_invalid":
                    raise StudioClientError("invalid response after commit", "studio_invalid_response")
                if owner.mode == "commit_409":
                    raise StudioClientError("ambiguous response after commit", "conflict", status_code=409)
                raise StudioClientError("write response lost", "studio_outcome_unknown")

        client_patch = patch("creatoros.tools.studio.StudioClient", IsolatedClient)
        client_patch.start()
        self.addCleanup(client_patch.stop)

    def plan_call(self, action, **extra):
        return ("manage_plan", {"action": action, "plan_id": self.pid, **extra})

    def start_continuation(self, responses):
        self.responses = responses
        self.chat._resume_plans()
        thread = self.chat.threads.get(self.sid)
        if thread is not None:
            thread.join(20)
        self.assertNotIn(self.sid, self.chat.active)
        return self.chat._read(self.sid)

    def assert_unknown_and_no_more_calls(self, doc, expected_requests):
        self.assertEqual(self.plans.get(self.sid, self.pid)["continuation_state"], "unknown")
        self.assertEqual(doc["requests"][0]["continuation_outcome"], "unknown")
        self.assertEqual(len(self.requests), expected_requests)
        self.assertEqual(self.providers[0].calls, 1)
        self.chat._resume_plans()
        self.assertEqual(len(self.providers), 1)
        self.plans.advance()
        self.assertEqual(self.plans.get(self.sid, self.pid)["steps"][1]["status"], "pending")

    def test_same_batch_preserves_results_and_pairs_without_dispatching_later_calls(self):
        resume = self.plan_call("resume", expected_version=self.version)
        calls = [self.plan_call("get"), resume, resume, self.plan_call("get")]
        doc = self.start_continuation([calls, [resume], []])
        self.assert_unknown_and_no_more_calls(doc, 2)  # initial GET plus one committed POST
        self.assertEqual(self.committed, 1)
        entries = [entry for entry in doc["entries"] if entry["kind"] == "tool"]
        self.assertEqual([entry["status"] for entry in entries],
                         ["done", "unknown", "not_dispatched", "not_dispatched"])

        messages = load_messages(self.chat._path(self.sid).with_name("messages.json"))
        index = next(i for i, message in enumerate(messages) if len(message.get("tool_calls", [])) == 4)
        call_ids = [call["id"] for call in messages[index]["tool_calls"]]
        results = messages[index + 1:index + 5]
        self.assertEqual([message["role"] for message in results], ["tool"] * 4)
        self.assertEqual([message["tool_call_id"] for message in results], call_ids)
        self.assertIn("studio_outcome_unknown", results[1]["content"])
        self.assertIn("本工具未执行", results[2]["content"])
        self.assertIn("本工具未执行", results[3]["content"])

    def test_unknown_write_stops_before_a_second_model_turn_can_repeat_it(self):
        resume = self.plan_call("resume", expected_version=self.version)
        doc = self.start_continuation([[resume], [resume], []])
        self.assert_unknown_and_no_more_calls(doc, 1)
        self.assertEqual(self.committed, 1)

    def test_successful_read_images_are_preserved_before_unknown_write_stops_turn(self):
        resume = self.plan_call("resume", expected_version=self.version)

        def execute(call, **kwargs):
            if call.name == "read_image":
                return ToolResult(content="image read", details={"image_attachments": [{"id": "isolated-image"}]})
            return execute_tool_call(call, **kwargs)

        with patch("creatoros.agent.loop.execute_tool_call", side_effect=execute):
            doc = self.start_continuation([[('read_image', {'attachment_id': 'isolated-image'}), resume], []])
        self.assert_unknown_and_no_more_calls(doc, 1)
        messages = load_messages(self.chat._path(self.sid).with_name("messages.json"))
        images = next(message for message in messages if message.get("creatoros_source") == "tool_images")
        self.assertEqual(images["content"][1]["attachment"], {"id": "isolated-image"})

    def test_write_response_500_invalid_json_or_ambiguous_409_is_unknown(self):
        for mode in ("commit_500", "commit_503_unavailable", "commit_invalid", "commit_409"):
            with self.subTest(mode=mode):
                # Each subtest needs its own pending continuation and account.
                with ContinuationUnknownWriteTests.isolated_case() as case:
                    case.mode = mode
                    resume = case.plan_call("resume", expected_version=case.version)
                    doc = case.start_continuation([[resume], []])
                    case.assert_unknown_and_no_more_calls(doc, 1)
                    case.assertEqual(case.committed, 1)

    def test_projection_exception_after_committed_write_preserves_dispatch_evidence(self):
        self.mode = "commit_success"
        resume = self.plan_call("resume", expected_version=self.version)
        with patch("creatoros.tools.model_projection.project_model_content", side_effect=RuntimeError("projection failed")):
            doc = self.start_continuation([[resume], [resume], []])
        self.assert_unknown_and_no_more_calls(doc, 1)
        self.assertEqual(self.committed, 1)

    def test_http_error_preserves_actual_response_status(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(
            403, json={"error": {"code": "agent_scope_rejected", "message": "scope denied"}}))
        client = httpx.Client(transport=transport)
        studio = StudioClient("http://127.0.0.1:1", client=client)
        try:
            with self.assertRaises(StudioClientError) as raised:
                studio.request("POST", "/controlled", payload={})
            self.assertEqual((raised.exception.code, raised.exception.status_code), ("agent_scope_rejected", 403))
        finally:
            studio.close()

    @classmethod
    def isolated_case(cls):
        from contextlib import contextmanager

        @contextmanager
        def make_case():
            case = cls(methodName="runTest")
            case.setUp()
            try:
                yield case
            finally:
                case.doCleanups()
        return make_case()

    def test_update_preflight_invalid_response_does_not_imply_a_write(self):
        self.mode = "read_invalid"
        update = self.plan_call("update", expected_version=self.version, steps=self.steps)
        doc = self.start_continuation([[update], []])
        self.assertEqual([request[0] for request in self.requests], ["GET"])
        self.assertEqual(self.plans.get(self.sid, self.pid)["continuation_state"], "done_checkpoint")
        self.assertEqual(doc["requests"][0]["continuation_outcome"], "done")
        self.assertEqual(self.providers[0].calls, 2)

    def test_update_lost_response_after_patch_is_unknown(self):
        update = self.plan_call("update", expected_version=self.version, steps=self.steps)
        doc = self.start_continuation([[update], [update], []])
        self.assertEqual([request[0] for request in self.requests], ["GET", "PATCH"])
        self.assert_unknown_and_no_more_calls(doc, 2)
        self.assertEqual(self.committed, 1)

    def test_read_failure_is_not_an_unknown_write(self):
        self.mode = "read_failed"
        doc = self.start_continuation([[self.plan_call("get")], []])
        self.assertEqual(self.plans.get(self.sid, self.pid)["continuation_state"], "done_checkpoint")
        self.assertEqual(doc["requests"][0]["continuation_outcome"], "done")
        self.assertEqual(self.providers[0].calls, 2)

    def test_proven_middleware_rejection_and_connection_refusal_are_not_unknown(self):
        for mode in ("pre_route_rejection", "connection_refused"):
            with self.subTest(mode=mode):
                with ContinuationUnknownWriteTests.isolated_case() as case:
                    case.mode = mode
                    resume = case.plan_call("resume", expected_version=case.version)
                    doc = case.start_continuation([[resume], []])
                    case.assertEqual(case.plans.get(case.sid, case.pid)["continuation_state"], "done_checkpoint")
                    case.assertEqual(doc["requests"][0]["continuation_outcome"], "done")
                    case.assertEqual(case.committed, 0)
                    case.assertEqual(case.providers[0].calls, 2)

    def test_ordinary_chat_keeps_its_existing_tool_error_flow(self):
        self.mode = "ordinary_unknown"
        resume = self.plan_call("resume", expected_version=self.version)
        self.responses = [[resume], []]
        view = self.chat.get(self.sid)
        self.chat.submit(self.sid, "ordinary", "继续", view["version"], "http://127.0.0.1:1")
        thread = self.chat.threads.get(self.sid)
        if thread is not None:
            thread.join(20)
        self.assertNotIn(self.sid, self.chat.active)
        doc = self.chat._read(self.sid)
        self.assertEqual(self.providers[0].calls, 2)
        self.assertEqual(doc["status"], "idle")
        self.assertEqual([entry["status"] for entry in doc["entries"] if entry["kind"] == "tool"], ["failed"])


if __name__ == "__main__":
    unittest.main()
