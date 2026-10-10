"""Host schemas and raw/model views; live model behavior is verified separately."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx

from creatoros.ai.types import ToolCall
from creatoros.agent.loop import build_model_context
from creatoros.context import RuntimeContext
from creatoros.evaluation.fixture import E01Fixture
from creatoros.session.request_trace import RequestSnapshots
from creatoros.tools import execute_tool_call, tools
from creatoros.tools.host_contract import bind_account_arguments, model_tool_schemas, AccountArgumentRejected
from creatoros.tools.model_projection import project_tool_messages
from creatoros.tools.results import ToolResult
from creatoros.web.chat import ACCOUNT_TOOLS
from tests.agent_studio_support import serve


class HostContractTests(unittest.TestCase):
    def test_schema_is_exact_host_view_not_global_mutation(self):
        original = deepcopy(tools)
        context = RuntimeContext(Path.cwd(), creator_id="a", allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True)
        by_name = {t["function"]["name"]: t["function"] for t in model_tool_schemas(tools, context)}
        self.assertEqual(set(by_name), ACCOUNT_TOOLS)
        self.assertEqual(len(by_name), 20)
        for name in ("list_creator_series", "get_creator_tasks", "compose_series"):
            self.assertNotIn("creator_id", by_name[name]["parameters"]["properties"])
            self.assertNotIn("creator_id", by_name[name]["parameters"]["required"])
        self.assertIn("当前会话", by_name["read_file"]["description"])
        self.assertEqual(tools, original)
        cli = {t["function"]["name"]: t["function"] for t in model_tool_schemas(tools, RuntimeContext(Path.cwd()))}
        self.assertIn("creator_id", cli["list_creator_series"]["parameters"]["properties"])

    def test_binding_rejects_explicit_override_not_silent_rewrite(self):
        context = RuntimeContext(Path.cwd(), creator_id="a")
        self.assertEqual(json.loads(bind_account_arguments("compose_series", '{"name":"词汇"}', context)),
                         {"name": "词汇", "creator_id": "a"})
        for value in ("b", None):
            with self.assertRaises(AccountArgumentRejected):
                bind_account_arguments("get_creator_tasks", json.dumps({"creator_id": value}), context)

    def test_execution_uses_real_scoped_http_and_leaves_database_unchanged(self):
        with TemporaryDirectory() as directory:
            fixture = E01Fixture(Path(directory) / "world", lambda: None, case_id="E02")
            try:
                before = fixture.state()
                with serve(fixture.app) as url, httpx.Client(base_url=url, trust_env=False) as client:
                    sid = client.post("/api/agent/sessions", json={"creator_id": fixture.creator_a}).json()["id"]
                    context = RuntimeContext(fixture.root, studio_url=url, creator_id=fixture.creator_a,
                        agent_session_id=sid, allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True)
                    for name in ("list_creator_series", "get_creator_tasks"):
                        result = execute_tool_call(ToolCall("c", name, "{}"), context, model_requested=True)
                        self.assertFalse(result.is_error, result.content)
                        self.assertNotIn(fixture.creator_b, result.content)
                        rejected = execute_tool_call(ToolCall("d", name,
                            json.dumps({"creator_id": fixture.creator_b})), context, model_requested=True)
                        self.assertEqual(rejected.error_type, "agent_scope_rejected")
                self.assertEqual(before, fixture.state())
                self.assertEqual(fixture.external_attempts, [])
            finally:
                fixture.close()

    def test_original_ledger_and_trace_are_separate_from_model_projection(self):
        raw = json.dumps({"items": [{"id": "a", "title": "词汇", "cover_url": "/cover"}],
                          "page": {"offset": 0, "limit": 20, "total": 1}}, ensure_ascii=False)
        messages = [{"role": "assistant", "content": None, "tool_calls": [{"id": "call", "function": {
            "name": "list_series_topics", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call", "content": raw}]
        original = deepcopy(messages)
        context = build_model_context(messages, [])
        outgoing, _ = context.to_request()
        self.assertNotIn("cover_url", outgoing[-1]["content"])
        self.assertEqual(messages, original)
        result = ToolResult(raw, model_content=outgoing[-1]["content"])
        with TemporaryDirectory() as directory:
            snapshots = RequestSnapshots(Path(directory) / "messages.json")
            rid = uuid4().hex
            snapshots.begin(rid, "turn", context)
            snapshots.tool_result(rid, ToolCall("call", "list_series_topics", "{}"), result)
            recorded = snapshots.read(rid)["tool_results"][0]
            self.assertEqual(recorded["content"], outgoing[-1]["content"])
            self.assertEqual(recorded["raw_content"], raw)
        self.assertEqual(result.to_raw_content(), raw)

    def test_reused_call_id_is_paired_in_order_and_error_prefix_survives(self):
        messages = []
        for name, data in (("get_content_run", {"run_id": "r", "thread_id": "hidden"}),
                           ("get_producer_skill", {"content": "完整正文", "digest": "d", "local_path": "hidden"})):
            messages += [{"role": "assistant", "tool_calls": [{"id": "reused", "function": {"name": name}}]},
                {"role": "tool", "tool_call_id": "reused", "content": json.dumps(data)}]
        projected = project_tool_messages(messages)
        self.assertEqual(json.loads(projected[1]["content"]), {"run_id": "r"})
        self.assertEqual(json.loads(projected[3]["content"]), {"content": "完整正文", "digest": "d"})
        messages[-1]["content"] = '[tool_error type=skill_digest_conflict]\n{"error":"conflict","current_digest":"d","debug":"hidden"}'
        self.assertEqual(project_tool_messages(messages)[-1]["content"],
                         '[tool_error type=skill_digest_conflict]\n{"error": "conflict", "current_digest": "d"}')

    def test_projection_fault_cannot_claim_successful_tool_failed(self):
        # Pure formatting fault injection, not a substitute for HTTP/model E2E.
        tool = MagicMock()
        with patch.dict("creatoros.tools.execution.tool_registry", {"queue_topics": tool}), \
                patch("creatoros.tools.model_projection.project_model_content", side_effect=ValueError("controlled")):
            tool.expose_to_model = True
            tool.parse_arguments.return_value = {}
            tool.execute.return_value = ToolResult('{"status":"completed"}')
            result = execute_tool_call(ToolCall("c", "queue_topics", "{}"), model_requested=True)
            self.assertFalse(result.is_error)
            self.assertEqual(result.details["projection_error"], "ValueError")


if __name__ == "__main__":
    unittest.main()
