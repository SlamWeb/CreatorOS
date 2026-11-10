"""Real local HTTP and database checks; no real model or paid production."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def worker(root):
    import json
    from io import StringIO
    from uuid import uuid4
    root = Path(root).resolve()
    os.environ["DATABASE_URL"] = "sqlite:///" + (root / "world/world.db").as_posix()
    os.environ["CREATOROS_EVAL_REGISTRY"] = str(root / "outside-registry.sqlite")
    from creatoros.ai.types import ToolCall, ToolCallDelta, StreamEnd, TextDelta
    from creatoros.context import RuntimeContext
    from creatoros.evaluation.benchmark.local_dispatch_guard import DispatchGuard
    from creatoros.evaluation.benchmark.local_dispatch_guard import apply_gate
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.tools.execution import execute_tool_call
    from creatoros.web.chat import ACCOUNT_TOOLS
    from tests.agent_studio_support import serve
    repo = Path(__file__).resolve().parents[1]
    dataset = repo / "docs/agent-eval/benchmark-v2/cases.json"
    cases = json.loads(dataset.read_text(encoding="utf-8"))["cases"]
    case = next(item for item in cases if item["id"] == "B31")
    world = BenchmarkWorld(root / "world", case, None, dataset_path=dataset, eval_root=root / "eval",
                           defer_background_start=True)
    with serve(world.app) as base:
        assert not world.app.state.background_services_started
        doc = world.app.state.chat.create(creator_id="bench-a")
        context = RuntimeContext(project_root=world.root, studio_url=base, creator_id="bench-a",
            agent_session_id=doc["id"], session_file=world.root / "sessions" / doc["id"] / "messages.json",
            allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True, user_request_id=uuid4().hex)
        guard = DispatchGuard()
        call = guard.wrap(execute_tool_call)
        def ask(name, args):
            return call(ToolCall(uuid4().hex, name, json.dumps(args)), context, model_requested=True)
        own = ask("manage_topics", {"action": "get", "topic_id": "bench-topic-a"})
        foreign = ask("manage_topics", {"action": "get", "topic_id": "bench-topic-b"})
        before_title = world.content.get_topic("bench-topic-b").title
        foreign_update = ask("manage_topics", {"action": "update", "topic_id": "bench-topic-b",
            "changes": {"title": "unauthorized change"}, "expected_digest": "0" * 64})
        blocked = ask("start_content_run", {"action": "start", "topic_ids": ["bench-topic-a"]})
        assert not own.is_error, own.content
        assert foreign.is_error and foreign.error_type == "agent_scope_rejected", (foreign.error_type, foreign.content)
        assert foreign_update.is_error and foreign_update.error_type == "agent_scope_rejected", (foreign_update.error_type, foreign_update.content)
        assert world.content.get_topic("bench-topic-b").title == before_title
        assert world.canary not in foreign.content
        assert blocked.is_error and blocked.error_type == "local_dispatch_guard_denied"
        assert guard.score(None)["status"] == "failed"
        assert not world.app.state.background_services_started

        # Real local Studio GETs reach the account-bound product path. Writes
        # never reach the API; foreign ownership is still checked by the host.
        collection_id = world.variables["collection_a"]
        read_guard = DispatchGuard()
        read = read_guard.wrap(execute_tool_call)
        def collection(args):
            return read(ToolCall(uuid4().hex, "manage_media_collection", json.dumps(args)), context, model_requested=True)
        before_collection = world.snapshot()["database"]["media_collections"]
        assert not collection({"action": "list", "include_sources": True}).is_error
        assert not collection({"action": "get", "collection_id": collection_id}).is_error
        foreign_collection = collection({"action": "get", "creator_id": "bench-b", "collection_id": world.variables["foreign_collection"]})
        assert foreign_collection.is_error and foreign_collection.error_type == "agent_scope_rejected"
        for action in ("export", "create", "update", "remove", "unknown"):
            result = collection({"action": action, "collection_id": collection_id})
            assert result.is_error and result.error_type == "local_dispatch_guard_denied"
        assert read_guard.score(None)["status"] == "failed"
        assert before_collection == world.snapshot()["database"]["media_collections"]

        # Exercise the real Agent loop: the controlled provider attempts both
        # production and network research. Neither tool reaches its executor.
        import creatoros.agent.loop as agent_loop
        from creatoros.terminal import Console
        class Provider:
            context_window = 128000
            reserve_output_tokens = 4096
            def __init__(self): self.number, self.client = 0, self
            def close(self): pass
            def stream(self, _context):
                self.number += 1
                if self.number == 1:
                    yield ToolCallDelta(0, "blocked-production", "start_content_run", '{"action":"start","topic_ids":["bench-topic-a"]}')
                    yield ToolCallDelta(1, "blocked-research", "research_series_topics", '{"series_id":"bench-english"}')
                    yield StreamEnd("tool_calls")
                else:
                    yield TextDelta("已停止。")
                    yield StreamEnd("stop")
        guarded = DispatchGuard()
        previous = agent_loop.execute_tool_call
        events = []
        before = world.snapshot()
        prompts = iter(["受控测试", "/exit"])
        try:
            agent_loop.execute_tool_call = guarded.wrap(previous)
            agent_loop.run_agent(Provider(), console=Console(input_fn=lambda _prompt: next(prompts), output=StringIO()),
                session_file=context.session_file, runtime_context=context, user_request_id=uuid4().hex,
                on_agent_event=lambda event: events.append(event))
        finally:
            agent_loop.execute_tool_call = previous
        denied_events = [event for event in events if event.kind == "tool_result"]
        assert len(denied_events) == 2 and all(e.data.get("error_type") == "local_dispatch_guard_denied" for e in denied_events)
        gate = guarded.score([{"tool_call_id": e.data["tool_call_id"]} for e in denied_events])
        scores = apply_gate({"critical_checks": [], "critical_status": "passed"}, gate)
        assert scores["critical_status"] == "failed"
        after = world.snapshot()
        assert before["database"]["content_runs"] == after["database"]["content_runs"]
        assert before["database"]["topic_research_queue_items"] == after["database"]["topic_research_queue_items"]
        assert not world.app.state.background_services_started

        # The actual agent loop also preserves successful reads in its trace,
        # while recording an export attempt as a mandatory failure.
        class CollectionProvider(Provider):
            def stream(self, _context):
                self.number += 1
                if self.number == 1:
                    for index, (call_id, args) in enumerate((
                        ("list-read", {"action": "list"}),
                        ("get-read", {"action": "get", "collection_id": collection_id}),
                        ("export-denied", {"action": "export", "collection_id": collection_id}))):
                        yield ToolCallDelta(index, call_id, "manage_media_collection", json.dumps(args))
                    yield StreamEnd("tool_calls")
                else:
                    yield TextDelta("已停止。")
                    yield StreamEnd("stop")
        collection_guard, events = DispatchGuard(), []
        prompts = iter(["受控素材测试", "/exit"])
        try:
            agent_loop.execute_tool_call = collection_guard.wrap(previous)
            agent_loop.run_agent(CollectionProvider(), console=Console(input_fn=lambda _prompt: next(prompts), output=StringIO()),
                session_file=context.session_file, runtime_context=context, user_request_id=uuid4().hex,
                on_agent_event=lambda event: events.append(event))
        finally:
            agent_loop.execute_tool_call = previous
        results = {e.data["tool_call_id"]: e.data for e in events if e.kind == "tool_result"}
        assert set(results) == {"list-read", "get-read", "export-denied"}, results
        assert not results["list-read"].get("is_error") and not results["get-read"].get("is_error")
        assert results["export-denied"].get("error_type") == "local_dispatch_guard_denied"
        assert collection_guard.score([{"tool_call_id": key} for key in results])["status"] == "failed"
        assert before_collection == world.snapshot()["database"]["media_collections"]

        # Also use the real async chat HTTP endpoint with all service workers
        # deferred. This catches incorrect assumptions about startup/lifespan.
        import httpx
        from tests.agent_studio_support import send_turn
        doc2 = world.app.state.chat.create(creator_id="bench-a")
        http_guard = DispatchGuard()
        previous = agent_loop.execute_tool_call
        before = world.snapshot()
        try:
            agent_loop.execute_tool_call = http_guard.wrap(previous)
            world.app.state.chat.provider_factory = Provider
            with httpx.Client(base_url=base, timeout=20, trust_env=False) as client:
                finished = send_turn(client, doc2, "受控测试")
        finally:
            agent_loop.execute_tool_call = previous
        assert finished["status"] != "running", finished
        assert http_guard.score(None)["status"] == "failed"
        after = world.snapshot()
        assert before["database"]["content_runs"] == after["database"]["content_runs"]
        assert before["database"]["topic_research_queue_items"] == after["database"]["topic_research_queue_items"]
        assert not world.app.state.background_services_started
    world.database.close()


class LocalGuardIntegrationTests(unittest.TestCase):
    def test_local_guard_preserves_actual_account_scope_without_starting_workers(self):
        from creatoros.integrations.media_tools import media_tool
        try:
            media_tool("ffprobe")
            media_tool("ffmpeg")
        except ValueError:
            self.skipTest("existing ffmpeg and ffprobe required")
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, "-m", "tests.test_local_guard_integration", "worker", root],
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "worker":
        worker(sys.argv[2])
    else:
        unittest.main()
