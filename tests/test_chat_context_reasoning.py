"""Local transport, persistence and concurrency checks; live API is separate."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from creatoros.ai.context import ModelContext
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.ai.types import TextDelta, ModelUsage, ModelResponse
from creatoros.agent.streaming import stream_llm
from creatoros.events import AgentEvent
from creatoros.session.context_trace import trace_path
from creatoros.session.request_trace import RequestSnapshots
from creatoros.web.chat import AgentChatService
from creatoros.web.chat_routes import ChatTurnRequest


class ChatContextReasoningTests(unittest.TestCase):
    def test_request_validation_and_provider_mapping(self):
        expected = {"none": "none", "low": "low", "medium": "high", "high": "high",
                    "xhigh": "high", "ultra": "max", "max": "max"}
        for effort, actual in expected.items():
            payload = ChatTurnRequest(request_id=str(uuid4()), expected_version=0, reasoning_effort=effort)
            provider = DeepSeekProvider("not-a-real-key", reasoning_effort=payload.reasoning_effort)
            options = provider._thinking_options()
            self.assertEqual(options["extra_body"]["thinking"]["type"],
                             "disabled" if effort == "none" else "enabled")
            self.assertEqual(options.get("reasoning_effort"), None if actual == "none" else actual)
            self.assertEqual(provider.actual_reasoning_effort, actual)
            self.assertEqual(provider.reasoning_effort, effort)
            provider.client.close()
        with self.assertRaises(ValidationError):
            ChatTurnRequest(request_id=str(uuid4()), expected_version=0, reasoning_effort="invalid")
        self.assertEqual(ChatTurnRequest(request_id=str(uuid4()), expected_version=0).reasoning_effort, "high")

    def test_stream_keeps_reasoning_for_tool_roundtrip_without_text_leak(self):
        provider = DeepSeekProvider("not-a-real-key", reasoning_effort="high")
        chunks = [SimpleNamespace(usage=None, choices=[SimpleNamespace(finish_reason=None,
                      delta=SimpleNamespace(content=None, reasoning_content="private provider reasoning", tool_calls=[]))]),
                  SimpleNamespace(usage=None, choices=[SimpleNamespace(finish_reason="stop",
                      delta=SimpleNamespace(content="answer", reasoning_content=None, tool_calls=[]))])]
        events = []
        with patch.object(provider.client.chat.completions, "create", return_value=iter(chunks)):
            answer = stream_llm(provider, ModelContext((), (), ()), on_event=events.append)
        self.assertEqual(answer.content, "answer")
        self.assertEqual([e.content for e in events if isinstance(e, TextDelta)], ["answer"])
        self.assertEqual(answer.to_message()["reasoning_content"], "private provider reasoning")
        outgoing = provider._to_openai_messages([answer.to_message(), {"role": "assistant", "content": "old"}])
        self.assertEqual(outgoing[0]["reasoning_content"], "private provider reasoning")
        self.assertEqual(outgoing[1]["reasoning_content"], "")
        provider.client.close()

    def test_session_request_options_are_idempotent_and_isolated(self):
        with TemporaryDirectory() as temporary:
            providers = []
            def factory():
                provider = DeepSeekProvider("not-a-real-key")
                providers.append(provider)
                return provider
            host = AgentChatService(Path(temporary), factory)
            a, b = host.create(), host.create()
            with patch("creatoros.web.chat.Thread"):
                request_id = str(uuid4())
                host.submit(a["id"], request_id, "hello", 0, "http://127.0.0.1:1", reasoning_effort="high")
                host.submit(a["id"], request_id, "hello", 0, "http://127.0.0.1:1", reasoning_effort="high")
                with self.assertRaises(HTTPException) as conflict:
                    host.submit(a["id"], request_id, "hello", 0, "http://127.0.0.1:1", reasoning_effort="low")
                self.assertEqual(conflict.exception.status_code, 409)
                # The requested UI setting is frozen even when two settings
                # normalize to the same DeepSeek wire effort.
                with self.assertRaises(HTTPException) as same_actual:
                    host.submit(a["id"], request_id, "hello", 0, "http://127.0.0.1:1", reasoning_effort="medium")
                self.assertEqual(same_actual.exception.status_code, 409)
                host.submit(b["id"], str(uuid4()), "hello", 0, "http://127.0.0.1:1", reasoning_effort="none")
                self.assertEqual([p.reasoning_effort for p in providers], ["high", "none"])
                self.assertEqual(host.get(a["id"])["reasoning_effort"], "high")
                self.assertEqual(host.get(b["id"])["reasoning_effort"], "none")
            for provider in providers:
                provider.client.close()

    def test_five_visible_levels_preserve_legacy_pending_and_new_default(self):
        with TemporaryDirectory() as temporary:
            providers = []
            def factory():
                provider = DeepSeekProvider("not-a-real-key")
                providers.append(provider)
                return provider
            host = AgentChatService(Path(temporary), factory)
            session = host.create()
            self.assertEqual(session["reasoning_effort"], "high")
            self.assertEqual(session["reasoning_options"], ["low", "medium", "high", "xhigh", "ultra"])
            self.assertEqual(session["reasoning_effort_map"],
                             {"low": "low", "medium": "high", "high": "high", "xhigh": "high", "ultra": "max"})
            doc = host._read(session["id"])
            doc["reasoning_effort"] = "none"
            for effort in ("none", "max", None):
                request = {"id": str(uuid4()), "text": "old", "status": "idle"}
                if effort is not None:
                    request["reasoning_effort"] = effort
                doc["requests"].append(request)
                host._save(doc)
                before = host._path(session["id"]).read_bytes()
                host.submit(session["id"], request["id"], "old", 0, "http://127.0.0.1:1", reasoning_effort=effort)
                self.assertEqual(host._path(session["id"]).read_bytes(), before)
                with self.assertRaises(HTTPException) as conflict:
                    host.submit(session["id"], request["id"], "old", 0, "http://127.0.0.1:1", reasoning_effort="high")
                self.assertEqual(conflict.exception.status_code, 409)
            self.assertEqual(providers, [])
            self.assertEqual(host.get(session["id"])["reasoning_effort"], "none")
            with patch("creatoros.web.chat.Thread"):
                view = host.submit(session["id"], str(uuid4()), "new", 0, "http://127.0.0.1:1")
                self.assertEqual(view["reasoning_effort"], "high")
                self.assertEqual(providers[-1].actual_reasoning_effort, "high")
            for provider in providers:
                provider.client.close()

    def test_occupancy_is_last_request_not_cumulative_and_survives_reopen(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            host = AgentChatService(root)
            session = host.create()
            doc = host._read(session["id"])
            doc["requests"].append({"id": str(uuid4()), "text": "hello"})
            host.providers[session["id"]] = SimpleNamespace(context_window=1_000_000)
            host._emit(doc, AgentEvent("model_usage", ModelUsage(900, 4, 904).to_dict()))
            host._emit(doc, AgentEvent("model_usage", ModelUsage(1200, 5, 1205).to_dict()))
            view = AgentChatService(root).get(session["id"])
            self.assertEqual(view["context_usage"]["input_tokens"], 1200)
            self.assertEqual(sum(e["input_tokens"] for e in view["entries"] if e["kind"] == "usage"), 2100)
            self.assertEqual(view["context_usage"]["timestamp_source"], "usage_received_at")

    def test_legacy_occupancy_projection_is_read_only(self):
        with TemporaryDirectory() as temporary:
            host = AgentChatService(Path(temporary))
            session = host.create()
            path = host._path(session["id"])
            doc = json.loads(path.read_text(encoding="utf-8"))
            del doc["context_usage"]
            path.write_text(json.dumps(doc), encoding="utf-8")
            original = path.read_bytes()
            trace_path(path.with_name("messages.json")).write_text(json.dumps({"event": "finished",
                "request_kind": "main", "usage": {"input_tokens": 345}, "context_window": 1_000_000,
                "turn_id": "old-turn", "started_at": "2026-10-20T01:00:00Z"}) + "\n", encoding="utf-8")
            view = host.get(session["id"])
            self.assertEqual(view["context_usage"]["input_tokens"], 345)
            self.assertEqual(view["context_usage"]["observed_at"], "2026-10-20T01:00:00Z")
            self.assertEqual(path.read_bytes(), original)

    def test_public_trace_omits_provider_reasoning_but_local_ledger_can_reuse_it(self):
        with TemporaryDirectory() as temporary:
            host = AgentChatService(Path(temporary))
            session = host.create()
            turn_id, request_id = str(uuid4()), uuid4().hex
            doc = host._read(session["id"])
            doc["requests"].append({"id": turn_id, "status": "idle"})
            host._save(doc)
            session_file = host._path(session["id"]).with_name("messages.json")
            trace_path(session_file).write_text(json.dumps({"event": "finished",
                "turn_id": turn_id, "request_id": request_id}) + "\n", encoding="utf-8")
            snapshots = RequestSnapshots(session_file)
            snapshots.begin(request_id, turn_id, ModelContext.from_messages([
                {"role": "assistant", "content": "prior answer", "reasoning_content": "provider private text"}], []))
            snapshots.response(request_id, ModelResponse("answer", [], reasoning_content="more provider private text"))
            self.assertIn("reasoning_content", json.dumps(snapshots.read(request_id)))
            public = json.dumps(host.request_snapshot(session["id"], turn_id, request_id))
            self.assertNotIn("reasoning_content", public)
            self.assertNotIn("provider private text", public)

    def test_structured_business_calls_keep_nonthinking_mode(self):
        provider = DeepSeekProvider("not-a-real-key", reasoning_effort="max")
        response = SimpleNamespace(output_text="{}", usage=None)
        with patch.object(provider.client.responses, "create", return_value=response) as create:
            provider.complete_structured(instructions="JSON", input_text="hi", schema_name="object", schema={"type": "object"})
        self.assertEqual(create.call_args.kwargs["reasoning"], {"effort": "none"})
        provider.client.close()


if __name__ == "__main__":
    unittest.main()
