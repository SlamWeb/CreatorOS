"""Loop fault injection and host event contract; not creative-quality scores."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.ai.types import StreamEnd, ToolCall, ToolCallDelta
from creatoros.agent.loop import run_agent
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import load_messages
from creatoros.tools.execution import execute_tool_call
from creatoros.tools.results import ToolResult
from creatoros.web.chat import AgentChatService, WebConsole


class BackgroundAgentTests(unittest.TestCase):
    def test_repeated_pending_read_yields_after_pairing_whole_batch(self):
        calls, events = [], []
        class RepeatingProvider:
            def stream(self, context):
                for index in range(2):
                    yield ToolCallDelta(index, f"call-{len(calls)}-{index}", "get_skill_extraction",
                                        json.dumps({"job_id": "a" * 64}))
                yield StreamEnd("tool_calls")
        def pending(call, **kwargs):
            calls.append(call)
            return ToolResult(json.dumps({"id": "a" * 64, "status": "running"}), details={
                "pending_task": {"id": "a" * 64, "kind": "skill_extraction", "status": "running"}})
        with TemporaryDirectory() as directory, patch("creatoros.agent.loop.execute_tool_call", side_effect=pending):
            path = Path(directory) / "messages.json"
            run_agent(RepeatingProvider(), console=WebConsole("看看提炼"), session_file=path,
                max_turns=3, runtime_context=RuntimeContext(Path(directory), archive_only_reads=True,
                    allowed_tools=frozenset({"get_skill_extraction"})), on_agent_event=events.append)
            self.assertEqual(len(calls), 1)
            ledger = load_messages(path)
            self.assertEqual(len([row for row in ledger if row["role"] == "tool"]), 2)
            self.assertEqual(ledger[-1]["role"], "assistant")
            self.assertIn("后台", ledger[-1]["content"])
            self.assertEqual([e.kind for e in events].count("background_wait"), 1)
            self.assertNotIn("guard_stop", [e.kind for e in events])

    def test_execution_and_history_projection_omit_public_progress_log(self):
        from creatoros.agent.loop import build_model_context
        from creatoros.tools.definitions import tool_registry
        raw = json.dumps({"id": "a" * 64, "status": "running", "operation": "extract",
                          "progress": [{"text": "large public activity"}]})
        tool = tool_registry["get_skill_extraction"]
        with patch.object(tool, "execute", return_value=ToolResult(raw)):
            result = execute_tool_call(ToolCall("c", "get_skill_extraction", '{"job_id":"a"}'),
                                       model_requested=True)
        self.assertIn("progress", result.content)
        self.assertNotIn("progress", result.to_model_content())
        messages = [{"role": "assistant", "tool_calls": [{"id": "c", "name": "get_skill_extraction"}]},
                    {"role": "tool", "tool_call_id": "c", "content": raw}]
        outgoing, _ = build_model_context(messages, []).to_request()
        self.assertNotIn("progress", outgoing[-1]["content"])

    def test_host_updates_one_tool_and_graceful_wait_reply(self):
        from creatoros.events import AgentEvent
        with TemporaryDirectory() as directory:
            host = AgentChatService(Path(directory))
            doc = host.create()
            host._emit(doc, AgentEvent("tool_call", {"name": "extract_skill_from_images", "turn_id": "t"}), "http://localhost")
            for status in ("queued", "running", "ready"):
                host._emit(doc, AgentEvent("background_progress", {
                    "kind": "skill_extraction", "id": "a" * 64, "status": status,
                    "url": "/skills?extraction=" + "a" * 64}), "http://localhost")
            tool = [row for row in doc["entries"] if row["kind"] == "tool"]
            self.assertEqual(len(tool), 1)
            self.assertEqual(tool[0]["background"]["status"], "ready")
            host._emit(doc, AgentEvent("background_wait", {"turn_id": "t", "message": "任务仍在后台进行。"}))
            self.assertTrue(doc["entries"][-1]["complete"])
            self.assertEqual(doc["entries"][-1]["text"], "任务仍在后台进行。")


if __name__ == "__main__":
    unittest.main()
