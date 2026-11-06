import json
from io import StringIO
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.agent.loop import run_agent
from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.ai.context import ModelContext
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import load_messages
from creatoros.session.context_trace import detailed_breakdown
from creatoros.terminal import Console
from creatoros.tools.results import ToolResult
from creatoros.tools.discovery_gateway import scoped_gateway_source


class ScriptedProvider:
    context_window = 1_000_000
    reserve_output_tokens = 4096

    def __init__(self, batches):
        self.batches = iter(batches)
        self.contexts = []

    def stream(self, context):
        self.contexts.append(context)
        batch = next(self.batches)
        if batch is None:
            yield TextDelta("已查看。")
            yield StreamEnd("stop")
        else:
            for index, (call_id, name, args) in enumerate(batch):
                yield ToolCallDelta(index, call_id, name, json.dumps(args, ensure_ascii=False))
            yield StreamEnd("tool_calls")


class GatewayLoopTests(unittest.TestCase):
    def test_discover_dispatch_projection_trace_and_restart(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"CREATOROS_TOOL_PROTOCOL": "discovery", "CREATOROS_TOOL_FASTPATH": "0"}):
            root = Path(directory)
            path = root / "messages.json"
            context = RuntimeContext(root, creator_id="account", archive_only_reads=True,
                                     allowed_tools=frozenset({"manage_topics"}))
            provider = ScriptedProvider([
                [("find", "discover_tools", {"names": ["manage_topics.get"]})],
                [("read", "call_discovered_tool", {"name": "manage_topics.get", "arguments": {"topic_id": "topic-1"}})],
                None,
            ])
            calls, events = [], []
            def execute(call, **kwargs):
                calls.append((call.name, json.loads(call.arguments)))
                return ToolResult(json.dumps({"action": "get", "topic": {"id": "topic-1", "title": "测试",
                                  "debug_server_only": "discard-me"}}, ensure_ascii=False))
            inputs = iter(["看看选题", "/exit"])
            with patch("creatoros.agent.loop.execute_tool_call", side_effect=execute):
                run_agent(provider, session_file=path, runtime_context=context,
                          console=Console(input_fn=lambda _: next(inputs), output=StringIO()),
                          on_agent_event=events.append, capture_request_trace=True)
            self.assertEqual(calls, [("manage_topics", {"action": "get", "topic_id": "topic-1"})])
            ledger = load_messages(path)
            response = next(m for m in ledger if m.get("role") == "assistant" and any(
                call.get("name") == "call_discovered_tool" for call in m.get("tool_calls", [])))
            self.assertEqual(response["tool_calls"][0]["id"], "read")
            tool_result = next(m for m in ledger if m.get("tool_call_id") == "read")
            self.assertEqual(tool_result["creatoros_source"], scoped_gateway_source(replace(context, session_file=path), "manage_topics.get"))
            self.assertIn("debug_server_only", tool_result["content"])
            current = next(m for m in provider.contexts[-1].messages if m.get("tool_call_id") == "read")
            self.assertNotIn("debug_server_only", current["content"])
            detail = detailed_breakdown(provider.contexts[-1], kind="main")
            self.assertGreater(detail["tool_results_by_tool"].get("manage_topics", 0), 0)
            self.assertTrue(any(e.kind == "tool_call" and e.data.get("name") == "manage_topics" for e in events))

            # A later human turn in the same ledger can reuse a still-visible
            # host-issued discovery without silently changing the model call.
            again = ScriptedProvider([[("read2", "call_discovered_tool", {"name": "manage_topics.get", "arguments": {"topic_id": "topic-1"}})], None])
            inputs = iter(["再看一次", "/exit"])
            with patch("creatoros.agent.loop.execute_tool_call", side_effect=execute):
                run_agent(again, session_file=path, runtime_context=context,
                          console=Console(input_fn=lambda _: next(inputs), output=StringIO()))
            self.assertEqual(len(calls), 2)

    def test_undiscovered_direct_canonical_and_same_batch_are_blocked(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"CREATOROS_TOOL_PROTOCOL": "discovery", "CREATOROS_TOOL_FASTPATH": "0"}):
            root = Path(directory)
            provider = ScriptedProvider([[
                ("a", "discover_tools", {"names": ["manage_topics.get"]}),
                ("b", "call_discovered_tool", {"name": "manage_topics.get", "arguments": {"topic_id": "topic-1"}}),
                ("c", "manage_topics", {"action": "get", "topic_id": "topic-1"}),
            ], None])
            inputs = iter(["看看", "/exit"])
            with patch("creatoros.agent.loop.execute_tool_call") as execute:
                run_agent(provider, session_file=root / "messages.json",
                          runtime_context=RuntimeContext(root, creator_id="account", archive_only_reads=True,
                                                         allowed_tools=frozenset({"manage_topics"})),
                          console=Console(input_fn=lambda _: next(inputs), output=StringIO()))
            execute.assert_not_called()
            rows = load_messages(root / "messages.json")
            self.assertIn("tool_not_discovered", next(m["content"] for m in rows if m.get("tool_call_id") == "b"))
            self.assertIn("tool_not_exposed", next(m["content"] for m in rows if m.get("tool_call_id") == "c"))

    def test_fast_read_is_fixed_to_read_action_and_projected(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"CREATOROS_TOOL_PROTOCOL": "discovery", "CREATOROS_TOOL_FASTPATH": "1"}):
            root = Path(directory)
            provider = ScriptedProvider([[("read", "read_topic", {"topic_id": "topic-1"})], None])
            calls = []
            def execute(call, **kwargs):
                calls.append((call.name, json.loads(call.arguments)))
                return ToolResult(json.dumps({"action": "get", "topic": {"id": "topic-1", "debug_only": "hidden"}}))
            inputs = iter(["看看", "/exit"])
            with patch("creatoros.agent.loop.execute_tool_call", side_effect=execute):
                run_agent(provider, session_file=root / "messages.json",
                          runtime_context=RuntimeContext(root, creator_id="account", archive_only_reads=True,
                                                         allowed_tools=frozenset({"manage_topics"})),
                          console=Console(input_fn=lambda _: next(inputs), output=StringIO()))
            self.assertEqual(calls, [("manage_topics", {"topic_id": "topic-1", "action": "get"})])
            messages = load_messages(root / "messages.json")
            self.assertEqual(next(m for m in messages if m.get("tool_call_id") == "read")["creatoros_source"],
                             scoped_gateway_source(RuntimeContext(root, creator_id="account", session_file=root / "messages.json"), "manage_topics.get"))
            result = next(m for m in provider.contexts[-1].messages if m.get("tool_call_id") == "read")
            self.assertNotIn("debug_only", result["content"])

    def test_attribution_requires_matching_gateway_call_and_host_metadata(self):
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "a", "name": "call_discovered_tool",
                "arguments": json.dumps({"name": "manage_topics.get", "arguments": {"topic_id": "x"}})}]},
            {"role": "tool", "tool_call_id": "a", "creatoros_source": "gateway_result:manage_topics.get", "content": "{}"},
            {"role": "assistant", "tool_calls": [{"id": "b", "name": "call_discovered_tool",
                "arguments": json.dumps({"name": "manage_topics.get", "arguments": {"topic_id": "x"}})}]},
            {"role": "tool", "tool_call_id": "b", "creatoros_source": "gateway_result:manage_plan.get", "content": "{}"},
        ]
        tools = [{"type": "function", "function": {"name": "call_discovered_tool", "parameters": {}}}]
        groups = detailed_breakdown(ModelContext.from_messages(messages, tools), kind="main")["tool_results_by_tool"]
        self.assertGreater(groups.get("manage_topics", 0), 0)
        self.assertGreater(groups.get("unknown", 0), 0)

    def test_externalized_discovery_cannot_authorize_unseen_contract(self):
        from creatoros.agent.loop import build_model_context
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"CREATOROS_TOOL_PROTOCOL": "discovery", "CREATOROS_TOOL_FASTPATH": "0"}):
            root = Path(directory)
            provider = ScriptedProvider([
                [("find", "discover_tools", {"names": ["manage_topics.get"]})],
                [("read", "call_discovered_tool", {"name": "manage_topics.get", "arguments": {"topic_id": "topic-1"}})],
                None,
            ])
            def externalized(*args, **kwargs):
                context = build_model_context(*args, **kwargs)
                messages, schemas = context.to_request()
                for message in messages:
                    if message.get("creatoros_source") == "gateway_discovery":
                        message["content"] = "[资料已外置，当前视图没有工具定义]"
                return ModelContext.from_messages(messages, schemas)
            inputs = iter(["看看", "/exit"])
            with patch("creatoros.agent.loop.build_model_context", side_effect=externalized), \
                 patch("creatoros.agent.loop.execute_tool_call") as execute:
                run_agent(provider, session_file=root / "messages.json",
                          runtime_context=RuntimeContext(root, creator_id="account", archive_only_reads=True,
                                                         allowed_tools=frozenset({"manage_topics"})),
                          console=Console(input_fn=lambda _: next(inputs), output=StringIO()))
            execute.assert_not_called()
            ledger = load_messages(root / "messages.json")
            self.assertIn("tool_not_discovered", next(m["content"] for m in ledger if m.get("tool_call_id") == "read"))

    def test_unknown_topic_write_is_not_repeated_after_restart(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"CREATOROS_TOOL_PROTOCOL": "discovery", "CREATOROS_TOOL_FASTPATH": "0"}):
            root = Path(directory)
            path = root / "messages.json"
            scope = RuntimeContext(root, creator_id="account", archive_only_reads=True,
                                   allowed_tools=frozenset({"manage_topics"}))
            arguments = {"topic_id": "topic-1", "changes": {"title": "新标题"}, "expected_digest": "a" * 64}
            tool_args = {"name": "manage_topics.update", "arguments": arguments}
            provider = ScriptedProvider([
                [("find", "discover_tools", {"names": ["manage_topics.update"]})],
                [("write-1", "call_discovered_tool", tool_args)],
                [("write-2", "call_discovered_tool", tool_args)],
                None,
            ])
            calls, events = [], []
            def execute(call, **kwargs):
                calls.append(call)
                return ToolResult('{"status":"unknown"}', True, "studio_outcome_unknown")
            inputs = iter(["修改标题", "/exit"])
            with patch("creatoros.agent.loop.execute_tool_call", side_effect=execute):
                run_agent(provider, session_file=path, runtime_context=scope,
                          console=Console(input_fn=lambda _: next(inputs), output=StringIO()), on_agent_event=events.append)
            self.assertEqual(len(calls), 1)
            self.assertTrue(next(event.data["outcome_unknown"] for event in events
                                 if event.kind == "tool_result" and event.data.get("tool_call_id") == "write-1"))
            ledger = load_messages(path)
            self.assertIn("write_outcome_pending", next(m["content"] for m in ledger if m.get("tool_call_id") == "write-2"))
            again = ScriptedProvider([[("write-3", "call_discovered_tool", tool_args)], None])
            inputs = iter(["再试一次", "/exit"])
            with patch("creatoros.agent.loop.execute_tool_call", side_effect=execute):
                run_agent(again, session_file=path, runtime_context=scope,
                          console=Console(input_fn=lambda _: next(inputs), output=StringIO()))
            self.assertEqual(len(calls), 1)
            self.assertIn("write_outcome_pending", next(m["content"] for m in load_messages(path) if m.get("tool_call_id") == "write-3"))

    def test_topic_write_binds_visible_read_digest_and_traces_actual_arguments(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"CREATOROS_TOOL_PROTOCOL": "discovery", "CREATOROS_TOOL_FASTPATH": "1"}):
            root = Path(directory)
            path = root / "messages.json"
            provider = ScriptedProvider([
                [("read", "read_topic", {"topic_id": "topic-1"})],
                [("find", "discover_tools", {"names": ["manage_topics.update"]})],
                [("write", "call_discovered_tool", {"name": "manage_topics.update", "arguments": {
                    "topic_id": "topic-1", "changes": {"title": "新标题"}}})], None,
            ])
            calls = []
            def execute(call, **kwargs):
                args = json.loads(call.arguments)
                calls.append(args)
                if args["action"] == "get":
                    return ToolResult(json.dumps({"action": "get", "topic": {"id": "topic-1", "edit_digest": "a" * 64}}))
                from creatoros.agent.unknown_write_guard import _known_request_id
                request_id = _known_request_id("topic_update", args, kwargs["context"])
                return ToolResult(json.dumps({"action": "update", "ok": True, "topic": {"id": "topic-1"},
                                              "request_id": request_id, "creator_id": "account"}))
            inputs = iter(["修改这个选题", "/exit"])
            with patch("creatoros.agent.loop.execute_tool_call", side_effect=execute):
                run_agent(provider, session_file=path,
                          runtime_context=RuntimeContext(root, creator_id="account", archive_only_reads=True,
                                                         allowed_tools=frozenset({"manage_topics"})),
                          console=Console(input_fn=lambda _: next(inputs), output=StringIO()), capture_request_trace=True)
            self.assertEqual(calls[1]["expected_digest"], "a" * 64)
            ledger = load_messages(path)
            self.assertNotIn("outcome_unknown", next(m["content"] for m in ledger if m.get("tool_call_id") == "write"))
            model_call = next(m for m in ledger if any(c.get("id") == "write" for c in m.get("tool_calls", [])))
            self.assertNotIn("expected_digest", json.loads(model_call["tool_calls"][0]["arguments"])["arguments"])
            snapshots = [json.loads(p.read_text(encoding="utf-8")) for p in path.with_suffix(".request-trace").glob("*.json")]
            receipt = next(row for snapshot in snapshots for row in snapshot["tool_results"] if row["tool_call_id"] == "write")
            self.assertEqual(json.loads(receipt["arguments"])["expected_digest"], "a" * 64)

    def test_topic_write_without_visible_read_is_rejected_before_dispatch(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"CREATOROS_TOOL_PROTOCOL": "discovery", "CREATOROS_TOOL_FASTPATH": "0"}):
            root = Path(directory)
            provider = ScriptedProvider([
                [("find", "discover_tools", {"names": ["manage_topics.update"]})],
                [("write", "call_discovered_tool", {"name": "manage_topics.update", "arguments": {
                    "topic_id": "topic-1", "changes": {"title": "新标题"}}})], None,
            ])
            inputs = iter(["修改", "/exit"])
            with patch("creatoros.agent.loop.execute_tool_call") as execute:
                run_agent(provider, session_file=root / "messages.json",
                          runtime_context=RuntimeContext(root, creator_id="account", archive_only_reads=True,
                                                         allowed_tools=frozenset({"manage_topics"})),
                          console=Console(input_fn=lambda _: next(inputs), output=StringIO()))
            execute.assert_not_called()
            result = next(m for m in load_messages(root / "messages.json") if m.get("tool_call_id") == "write")
            self.assertIn("topic_base_unavailable", result["content"])


if __name__ == "__main__":
    unittest.main()
