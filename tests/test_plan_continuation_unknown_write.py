"""Unknown plan writes stop only the current continuation, without replay."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

import httpx
from copy import deepcopy
from sqlalchemy import func, select

from creatoros.agent.plans import PlanService
from creatoros.ai.types import StreamEnd, TextDelta, ToolCall, ToolCallDelta
from creatoros.context import RuntimeContext
from creatoros.integrations.studio import StudioClient, StudioClientError
from creatoros.session.snapshot import load_messages
from creatoros.storage import Base, ContentRepository, CreatorPlatform, Database
from creatoros.storage.models import AgentPlan, AgentPlanStep
from creatoros.tools.execution import execute_tool_call
from creatoros.tools.plans import _valid_write_receipt
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
                        owner.plans.edit(owner.sid, owner.pid, current["version"], payload["steps"],
                                         payload.get("mode", "replace"), payload.get("remove_step_ids"))
                    owner.committed += 1
                if owner.mode == "commit_success":
                    return owner.plans.get(owner.sid, owner.pid)
                if owner.mode == "commit_500":
                    raise StudioClientError("response after commit", "http_error", status_code=500)
                if owner.mode == "commit_503_unavailable":
                    raise StudioClientError("response after commit", "studio_unavailable", status_code=503)
                if owner.mode == "commit_invalid":
                    raise StudioClientError("invalid response after commit", "studio_invalid_response")
                if owner.mode == "commit_409":
                    raise StudioClientError("ambiguous response after commit", "conflict", status_code=409)
                if owner.mode == "commit_422":
                    raise StudioClientError("ambiguous validation after commit", "invalid_request", status_code=422)
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

    def test_write_response_500_invalid_json_or_ambiguous_409_and_422_is_unknown(self):
        for mode in ("commit_500", "commit_503_unavailable", "commit_invalid", "commit_409", "commit_422"):
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

    def test_update_generic_422_after_commit_is_still_unknown(self):
        self.mode = "commit_422"
        changed = {**self.steps[1], "title": "changed once"}
        update = self.plan_call("update", expected_version=self.version, steps=[changed])
        doc = self.start_continuation([[update], [update], []])
        self.assertEqual([request[0] for request in self.requests], ["GET", "PATCH"])
        self.assert_unknown_and_no_more_calls(doc, 2)
        self.assertEqual(self.committed, 1)
        self.assertEqual(self.plans.get(self.sid, self.pid)["steps"][1]["title"], "changed once")

    def test_read_failure_is_not_an_unknown_write(self):
        self.mode = "read_failed"
        doc = self.start_continuation([[self.plan_call("get")], []])
        self.assertEqual(self.plans.get(self.sid, self.pid)["continuation_state"], "done_checkpoint")
        self.assertEqual(doc["requests"][0]["continuation_outcome"], "done")
        self.assertEqual(self.providers[0].calls, 2)

    def test_malformed_update_preflight_read_never_counts_as_unknown_write(self):
        methods = []
        def transport(request):
            methods.append(request.method)
            return httpx.Response(200, json={})
        def client(url, **kwargs):
            return StudioClient(url, client=httpx.Client(transport=httpx.MockTransport(transport)), **kwargs)
        update = self.plan_call("update", expected_version=self.version, steps=self.steps)
        with patch("creatoros.tools.studio.StudioClient", side_effect=client):
            doc = self.start_continuation([[update], []])
        self.assertEqual(methods, ["GET"])
        self.assertEqual(self.plans.get(self.sid, self.pid)["continuation_state"], "done_checkpoint")
        self.assertEqual(doc["requests"][0]["continuation_outcome"], "done")
        self.assertEqual(self.providers[0].calls, 2)
        self.assertEqual([e["status"] for e in doc["entries"] if e["kind"] == "tool"], ["failed"])

    def test_plan_write_receipt_checks_identity_owner_version_and_shape(self):
        valid = self.plans.get(self.sid, self.pid)
        expected = dict(plan_id=self.pid, session_id=self.sid, creator_id="isolated",
                        expected_version=valid["version"] - 1)
        self.assertTrue(_valid_write_receipt(valid, **expected))
        self.assertTrue(_valid_write_receipt({**valid, "version": valid["version"] + 3}, **expected))
        for change in ({"id": "other"}, {"session_id": "other"}, {"creator_id": "other"},
                       {"creator_id": ""}, {"version": valid["version"] - 1}, {"version": True},
                       {"status": "invented"}, {"status": []}, {"steps": None}):
            with self.subTest(change=change):
                self.assertFalse(_valid_write_receipt({**valid, **change}, **expected))
        for field in ("id", "session_id", "creator_id", "version", "status", "steps"):
            with self.subTest(missing=field):
                missing = dict(valid)
                del missing[field]
                self.assertFalse(_valid_write_receipt(missing, **expected))

    def test_all_continuation_writes_stop_after_committed_but_invalid_2xx_receipt(self):
        for action in ("update", "resume", "retry", "skip"):
            with self.subTest(action=action), ContinuationUnknownWriteTests.isolated_case() as case:
                if action in {"retry", "skip"}:
                    with case.db.session() as session:
                        row = session.get(AgentPlanStep, case.pid + ":image")
                        row.tool = "get_producer_skill"
                        row.arguments_json = {"skill_id": "missing--0000000000000000"}
                        row.status, row.error = "failed", "controlled read failure"
                        row.result_json = {"_error_type": "studio_read_failed"}
                extras = {"expected_version": case.version}
                if action == "update":
                    extras["steps"] = case.steps
                if action in {"retry", "skip"}:
                    extras["step_id"] = "image"
                call = case.plan_call(action, **extras)

                def transport(request):
                    method, payload = request.method, json.loads(request.content or b"{}")
                    case.requests.append((method, request.url.path, payload))
                    if method == "GET":
                        return httpx.Response(200, json=case.plans.get(case.sid, case.pid))
                    if method == "PATCH":
                        case.plans.edit(case.sid, case.pid, payload["expected_version"], payload["steps"],
                                        payload.get("mode", "replace"), payload.get("remove_step_ids"))
                    else:
                        case.plans.action(case.sid, case.pid, payload["expected_version"],
                                          payload["action"], payload.get("step_id"))
                    case.committed += 1
                    return httpx.Response(200, json={"unexpected": "not a plan receipt"})

                def client(url, **kwargs):
                    return StudioClient(url, client=httpx.Client(transport=httpx.MockTransport(transport)), **kwargs)

                with patch("creatoros.tools.studio.StudioClient", side_effect=client):
                    doc = case.start_continuation([[call, call], [call], []])
                self.assertEqual(case.committed, 1)
                self.assertEqual(case.providers[0].calls, 1)
                self.assertEqual(case.plans.get(case.sid, case.pid)["continuation_state"], "unknown")
                self.assertEqual(doc["requests"][0]["continuation_outcome"], "unknown")
                self.assertEqual([e["status"] for e in doc["entries"] if e["kind"] == "tool"],
                                 ["unknown", "not_dispatched"])
                previous = deepcopy(case.plans.get(case.sid, case.pid)["steps"])
                case.chat._resume_plans()
                case.plans.advance()
                self.assertEqual(case.plans.get(case.sid, case.pid)["steps"], previous)
                self.assertEqual(case.committed, 1)

    def test_ordinary_chat_plan_writes_show_invalid_receipt_as_unknown(self):
        for action in ("create", "resume"):
            with self.subTest(action=action), ContinuationUnknownWriteTests.isolated_case() as case:
                captured = []
                def transport(request):
                    payload = json.loads(request.content)
                    if action == "create":
                        result = case.plans.create(case.sid, payload["request_id"], payload["title"],
                                                   payload["steps"], "http://127.0.0.1:1")
                    else:
                        result = case.plans.action(case.sid, case.pid, payload["expected_version"], "resume")
                    captured.append(result)
                    return httpx.Response(200, json={})
                def client(url, **kwargs):
                    return StudioClient(url, client=httpx.Client(transport=httpx.MockTransport(transport)), **kwargs)
                arguments = ({"action": "create", "title": "ordinary plan", "steps": [case.steps[0]]}
                             if action == "create" else {"action": "resume", "plan_id": case.pid,
                                                         "expected_version": case.version})
                case.responses = [[("manage_plan", arguments)], []]
                with patch("creatoros.tools.studio.StudioClient", side_effect=client):
                    view = case.chat.get(case.sid)
                    case.chat.submit(case.sid, "ordinary-invalid", "继续", view["version"], "http://127.0.0.1:1")
                    thread = case.chat.threads.get(case.sid)
                    if thread is not None:
                        thread.join(20)
                doc = case.chat._read(case.sid)
                self.assertEqual(len(captured), 1)
                self.assertEqual(case.providers[0].calls, 2)  # ordinary chat retains its existing flow
                self.assertEqual([e["status"] for e in doc["entries"] if e["kind"] == "tool"], ["unknown"])
                messages = load_messages(case.chat._path(case.sid).with_name("messages.json"))
                result = next(message for message in messages if message.get("role") == "tool")
                self.assertIn("studio_outcome_unknown", result["content"])
                with case.db.session() as session:
                    self.assertEqual(session.scalar(select(func.count()).select_from(AgentPlan)),
                                     2 if action == "create" else 1)

    def test_ordinary_create_and_resume_accept_actual_plan_receipts(self):
        for action in ("create", "resume"):
            with self.subTest(action=action), ContinuationUnknownWriteTests.isolated_case() as case:
                def transport(request):
                    payload = json.loads(request.content)
                    if action == "create":
                        result = case.plans.create(case.sid, payload["request_id"], payload["title"],
                                                   payload["steps"], "http://127.0.0.1:1")
                    else:
                        result = case.plans.action(case.sid, case.pid, payload["expected_version"], "resume")
                    return httpx.Response(200, json=result)
                def client(url, **kwargs):
                    return StudioClient(url, client=httpx.Client(transport=httpx.MockTransport(transport)), **kwargs)
                arguments = ({"action": "create", "title": "ordinary plan", "steps": [case.steps[0]]}
                             if action == "create" else {"action": "resume", "plan_id": case.pid,
                                                         "expected_version": case.version})
                case.responses = [[("manage_plan", arguments)], []]
                with patch("creatoros.tools.studio.StudioClient", side_effect=client):
                    view = case.chat.get(case.sid)
                    case.chat.submit(case.sid, "ordinary-valid", "继续", view["version"], "http://127.0.0.1:1")
                    thread = case.chat.threads.get(case.sid)
                    if thread is not None:
                        thread.join(20)
                doc = case.chat._read(case.sid)
                self.assertEqual([e["status"] for e in doc["entries"] if e["kind"] == "tool"], ["done"])

    def test_ordinary_remaining_plan_writes_report_invalid_receipt_after_commit(self):
        for action in ("update", "pause", "retry", "skip"):
            with self.subTest(action=action), ContinuationUnknownWriteTests.isolated_case() as case:
                if action == "pause":
                    case.plans.action(case.sid, case.pid, case.version, "resume")
                    case.version = case.plans.get(case.sid, case.pid)["version"]
                if action in {"retry", "skip"}:
                    with case.db.session() as session:
                        row = session.get(AgentPlanStep, case.pid + ":image")
                        row.tool = "get_producer_skill"
                        row.arguments_json = {"skill_id": "missing--0000000000000000"}
                        row.status, row.error = "failed", "controlled read failure"
                        row.result_json = {"_error_type": "studio_read_failed"}
                arguments = {"action": action, "plan_id": case.pid, "expected_version": case.version}
                if action == "update":
                    arguments["steps"] = case.steps
                if action in {"retry", "skip"}:
                    arguments["step_id"] = "image"
                committed = []
                def transport(request):
                    payload = json.loads(request.content)
                    if request.method == "PATCH":
                        result = case.plans.edit(case.sid, case.pid, payload["expected_version"], payload["steps"],
                                                 payload.get("mode", "replace"), payload.get("remove_step_ids"))
                    else:
                        result = case.plans.action(case.sid, case.pid, payload["expected_version"],
                                                   payload["action"], payload.get("step_id"))
                    committed.append(result)
                    return httpx.Response(200, json={})
                def client(url, **kwargs):
                    return StudioClient(url, client=httpx.Client(transport=httpx.MockTransport(transport)), **kwargs)
                context = RuntimeContext(project_root=case.root, studio_url="http://127.0.0.1:1",
                    creator_id="isolated", agent_session_id=case.sid)
                with patch("creatoros.tools.studio.StudioClient", side_effect=client):
                    result = execute_tool_call(ToolCall("ordinary-write", "manage_plan", json.dumps(arguments)),
                                               context=context, model_requested=True)
                self.assertEqual(len(committed), 1)
                self.assertGreater(committed[0]["version"], case.version)
                self.assertTrue(result.is_error)
                self.assertEqual(result.error_type, "studio_outcome_unknown")
                self.assertTrue(result.details["plan_write_outcome_unknown"])

    def test_ordinary_lost_or_malformed_write_receipts_keep_error_and_show_unknown(self):
        for mode in ("lost", "bad_json", "array", "http_500", "non_http_exception"):
            with self.subTest(mode=mode), ContinuationUnknownWriteTests.isolated_case() as case:
                committed = []
                def transport(request):
                    payload = json.loads(request.content)
                    committed.append(case.plans.action(case.sid, case.pid, payload["expected_version"], "resume"))
                    if mode == "lost":
                        raise httpx.ReadTimeout("controlled lost response", request=request)
                    if mode == "non_http_exception":
                        raise OSError("controlled write failure after commit")
                    if mode == "bad_json":
                        return httpx.Response(200, content=b"invalid json")
                    if mode == "array":
                        return httpx.Response(200, json=[])
                    return httpx.Response(500, json={"error": {"code": "controlled_server_failure",
                                                                "message": "controlled failure"}})
                def client(url, **kwargs):
                    return StudioClient(url, client=httpx.Client(transport=httpx.MockTransport(transport)), **kwargs)
                case.responses = [[case.plan_call("resume", expected_version=case.version)], []]
                with patch("creatoros.tools.studio.StudioClient", side_effect=client):
                    view = case.chat.get(case.sid)
                    case.chat.submit(case.sid, "ordinary-transport", "继续", view["version"], "http://127.0.0.1:1")
                    thread = case.chat.threads.get(case.sid)
                    if thread is not None:
                        thread.join(20)
                self.assertEqual(len(committed), 1)
                self.assertEqual(case.providers[0].calls, 2)
                doc = case.chat._read(case.sid)
                self.assertEqual([e["status"] for e in doc["entries"] if e["kind"] == "tool"], ["unknown"])
                messages = load_messages(case.chat._path(case.sid).with_name("messages.json"))
                result = next(message for message in messages if message.get("role") == "tool")
                expected = ("studio_outcome_unknown" if mode == "lost" else
                            "studio_invalid_response" if mode in {"bad_json", "array"} else
                            "tool_exception" if mode == "non_http_exception" else "controlled_server_failure")
                self.assertIn(expected, result["content"])

    def test_ordinary_read_or_proven_rejection_is_not_marked_unknown(self):
        for mode in ("read_failed", "rejected"):
            with self.subTest(mode=mode), ContinuationUnknownWriteTests.isolated_case() as case:
                def transport(request):
                    if mode == "read_failed":
                        raise httpx.ReadTimeout("controlled read timeout", request=request)
                    return httpx.Response(403, json={"error": {"code": "agent_scope_rejected", "message": "denied"}})
                def client(url, **kwargs):
                    return StudioClient(url, client=httpx.Client(transport=httpx.MockTransport(transport)), **kwargs)
                call = case.plan_call("get") if mode == "read_failed" else case.plan_call("resume", expected_version=case.version)
                case.responses = [[call], []]
                with patch("creatoros.tools.studio.StudioClient", side_effect=client):
                    view = case.chat.get(case.sid)
                    case.chat.submit(case.sid, "ordinary-known", "继续", view["version"], "http://127.0.0.1:1")
                    thread = case.chat.threads.get(case.sid)
                    if thread is not None:
                        thread.join(20)
                doc = case.chat._read(case.sid)
                self.assertEqual([e["status"] for e in doc["entries"] if e["kind"] == "tool"], ["failed"])
                messages = load_messages(case.chat._path(case.sid).with_name("messages.json"))
                result = next(message for message in messages if message.get("role") == "tool")
                self.assertIn("studio_read_failed" if mode == "read_failed" else "agent_scope_rejected", result["content"])

    def test_ordinary_unknown_result_survives_projection_failure(self):
        from creatoros.tools.model_projection import project_model_content
        self.mode = "ordinary_unknown"
        self.responses = [[self.plan_call("resume", expected_version=self.version)], []]
        calls = []
        def fail_once(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("controlled projection failure")
            return project_model_content(*args, **kwargs)
        with patch("creatoros.tools.model_projection.project_model_content", side_effect=fail_once):
            view = self.chat.get(self.sid)
            self.chat.submit(self.sid, "ordinary-projection", "继续", view["version"], "http://127.0.0.1:1")
            thread = self.chat.threads.get(self.sid)
            if thread is not None:
                thread.join(20)
        doc = self.chat._read(self.sid)
        self.assertEqual(self.providers[0].calls, 2)
        self.assertEqual([e["status"] for e in doc["entries"] if e["kind"] == "tool"], ["unknown"])
        messages = load_messages(self.chat._path(self.sid).with_name("messages.json"))
        result = next(message for message in messages if message.get("role") == "tool")
        self.assertIn("studio_outcome_unknown", result["content"])
        self.assertIn("write response lost", result["content"])

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
        self.assertEqual([entry["status"] for entry in doc["entries"] if entry["kind"] == "tool"], ["unknown"])


if __name__ == "__main__":
    unittest.main()
