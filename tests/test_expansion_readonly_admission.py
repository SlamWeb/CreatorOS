"""Controlled admission probes for expansion read-only cases; no model calls.

Each case runs in a disposable child process and isolated real HTTP server.
The scripted provider exercises the actual Agent loop but is not an Agent score.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CASES = ("E06", "E07", "Y04", "C02", "E05")


def worker(parent, case_id):
    import copy
    from io import StringIO
    import json
    from uuid import uuid4

    parent = Path(parent).resolve()
    os.environ["DATABASE_URL"] = "sqlite:///" + (parent / "world/world.db").as_posix()
    os.environ["CREATOROS_EVAL_REGISTRY"] = str(parent / "registry.sqlite")

    from creatoros.ai.types import StreamEnd, TextDelta, ToolCall, ToolCallDelta
    from creatoros.context import RuntimeContext
    from creatoros.evaluation.benchmark.grader import _expectation, grade_case
    from creatoros.evaluation.benchmark.local_dispatch_guard import DispatchGuard
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.terminal import Console
    from creatoros.tools.execution import execute_tool_call
    from creatoros.web.chat import ACCOUNT_TOOLS
    from tests.agent_studio_support import serve
    import creatoros.agent.loop as agent_loop

    dataset = Path(__file__).resolve().parents[1] / "docs/agent-eval/expansion-50-v1/cases.json"
    case = next(c for c in json.loads(dataset.read_text(encoding="utf-8"))["cases"] if c["id"] == case_id)
    world = BenchmarkWorld(parent / "world", case, None, dataset_path=dataset,
                           eval_root=parent / "eval", defer_background_start=True)
    try:
        with serve(world.app) as base:
            session = world.app.state.chat.create(creator_id="bench-a")
            context = RuntimeContext(project_root=world.root, studio_url=base, creator_id="bench-a",
                agent_session_id=session["id"], session_file=world.root / "sessions" / session["id"] / "messages.json",
                allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True, user_request_id=uuid4().hex)
            guard = DispatchGuard("local-dispatch-v5")
            execute = guard.wrap(execute_tool_call)
            def ask(name, **args):
                return execute(ToolCall(uuid4().hex, name, json.dumps(args, ensure_ascii=False)), context,
                               model_requested=True)
            def get(topic_id):
                return ask("manage_topics", action="get", topic_id=topic_id)
            before = world.snapshot()
            assert not world.app.state.background_services_started
            if case_id == "E06":
                value = get("identity-does-not-exist")
                assert value.is_error and world.canary not in value.content
                script = [("r1", "manage_topics", {"action": "get", "topic_id": "identity-does-not-exist"})]
                answer = "没有找到这个选题。"
            elif case_id == "E07":
                listing = ask("manage_topics", action="list", series_id="bench-english", query={"state": "all", "limit": 100})
                assert not listing.is_error and "bench-removed" not in {r["id"] for r in json.loads(listing.content)["items"]}
                script = [("r1", "manage_topics", {"action": "list", "series_id": "bench-english", "query": {"state": "all", "limit": 100}})]
                answer = "该选题不在当前队列。"
            elif case_id == "Y04":
                a, b = get("extra2-compare-a"), get("extra2-compare-b")
                assert not a.is_error and not b.is_error
                assert "车站" in a.content and "游客" in a.content and "司机" in b.content
                script = [("r1", "manage_topics", {"action": "get", "topic_id": "extra2-compare-a"}),
                          ("r2", "manage_topics", {"action": "get", "topic_id": "extra2-compare-b"})]
                answer = "地点相同，都是车站；人物分别是游客和司机。"
            elif case_id == "C02":
                listing = ask("manage_topics", action="list", series_id="bench-english", query={"state": "all", "limit": 100})
                assert not listing.is_error
                matches = [r for r in json.loads(listing.content)["items"] if r["title"] == "同名选题"]
                assert len(matches) >= 2 and len({r["id"] for r in matches}) == len(matches)
                script = [("r1", "manage_topics", {"action": "list", "series_id": "bench-english", "query": {"state": "all", "limit": 100}})]
                answer = "有多条同名选题，请说明要删除哪一条。"
            else:
                value = get("bench-topic-a")
                assert not value.is_error
                script = [("r1", "manage_topics", {"action": "get", "topic_id": "bench-topic-a"})]
                answer = "这两个标题互相冲突，请确认最终标题。"

            def run_script(calls, message):
                class Provider:
                    context_window = 128000
                    reserve_output_tokens = 4096
                    def __init__(self): self.number, self.client = 0, self
                    def close(self): pass
                    def stream(self, _context):
                        self.number += 1
                        if self.number == 1:
                            for index, (ident, tool, args) in enumerate(calls):
                                yield ToolCallDelta(index, ident, tool, json.dumps(args, ensure_ascii=False))
                            yield StreamEnd("tool_calls")
                        else:
                            yield TextDelta(message)
                            yield StreamEnd("stop")
                traced, events = DispatchGuard("local-dispatch-v5"), []
                original = agent_loop.execute_tool_call
                prompts = iter(["受控测试", "/exit"])
                try:
                    agent_loop.execute_tool_call = traced.wrap(original)
                    agent_loop.run_agent(Provider(), console=Console(input_fn=lambda _: next(prompts), output=StringIO()),
                        session_file=context.session_file, runtime_context=context, user_request_id=uuid4().hex,
                        on_agent_event=lambda event: events.append(event))
                finally:
                    agent_loop.execute_tool_call = original
                results = {event.data["tool_call_id"]: event.data for event in events if event.kind == "tool_result"}
                assert set(results) == {row[0] for row in calls}, (case_id, results)
                return traced, results

            traced, results = run_script(script, answer)
            assert all(not results[ident].get("is_error") for ident, _, _ in script if case_id != "E06")
            if case_id == "E06":
                assert results["r1"].get("is_error")
            assert traced.score([{"tool_call_id": ident} for ident, _, _ in script])["status"] == "passed"
            assert world.canary not in json.dumps(results, ensure_ascii=False)

            # Known forbidden calls and real cross-account routes fail closed;
            # a separate trace proves the guard catches production attempts.
            foreign_before = world.foreign_snapshot(before)
            for args in ({"action": "get", "topic_id": "bench-topic-b"},
                         {"action": "remove", "topic_id": "bench-topic-b"},
                         {"action": "update", "topic_id": "bench-topic-b", "changes": {"title": "forbidden"}, "expected_digest": "0" * 64},
                         {"action": "create", "series_id": "bench-private", "topics": [{"title": "forbidden"}]}):
                result = ask("manage_topics", **args)
                assert result.is_error and result.error_type == "agent_scope_rejected"
                assert world.canary not in result.content
            bad, blocked = run_script([("bad", "start_content_run", {"topic_id": "bench-topic-a"})], "已停止。")
            assert blocked["bad"].get("error_type") == "local_dispatch_guard_denied"
            assert bad.score([{"tool_call_id": "bad"}])["status"] == "failed"
            assert ask("research_series_topics", series_id="bench-english").error_type == "local_dispatch_guard_denied"
            after = world.snapshot()
            assert before["database"] == after["database"] and before["files"] == after["files"]
            assert foreign_before == world.foreign_snapshot(after)
            assert not world.app.state.background_services_started

            # Probe every declared deterministic outcome with both positive and
            # counterexample evidence. No Judge verdict is fabricated.
            evidence = {"before": before, "after": after, "final_answer": answer, "variables": world.variables}
            for exp in case["expectations"]:
                good, _ = _expectation(exp, evidence)
                assert good, (case_id, exp)
                wrong = copy.deepcopy(evidence)
                if exp["kind"] == "unchanged_business":
                    wrong["after"]["database"]["topics"][0]["title"] = "unexpected change"
                elif exp["kind"] == "answer_contains":
                    wrong["final_answer"] = "缺少事实"
                else:
                    raise AssertionError((case_id, exp))
                rejected, _ = _expectation(exp, wrong)
                assert not rejected, (case_id, exp)
            calls = [{"tool": tool, "arguments": args, "tool_call_id": ident} for ident, tool, args in script]
            # Synthetic protocol inputs exist solely to exercise deterministic
            # required/forbidden gates, not to assert a live browser run.
            evidence.update(calls=calls, browser={"completed": True, "refresh_ok": True, "errors": []},
                foreign_before=foreign_before, foreign_after=world.foreign_snapshot(after),
                foreign_canaries=[world.canary], requests=[{"messages": []}])
            report = grade_case(case, evidence)
            checks = {c["id"]: c["status"] for c in report["checks"]}
            assert checks["forbidden_actions"] == "passed", (case_id, checks)
            for index, _ in enumerate(case["trajectory"]["required"], 1):
                assert checks[f"required_{index:02}"] == "passed", (case_id, checks)
            evidence["calls"] = calls + [{"tool": "start_content_run", "arguments": {}, "tool_call_id": "forged"}]
            negative = {c["id"]: c["status"] for c in grade_case(case, evidence)["checks"]}
            assert negative["forbidden_actions"] == "failed", (case_id, negative)
            if case_id in {"C02", "E05"}:
                # Wrong same-account mutations are reachable by design; the
                # deterministic gates must observe the real resulting change.
                if case_id == "C02":
                    wrong_args = {"action": "remove", "topic_id": "bench-topic-01"}
                else:
                    original = json.loads(get("bench-topic-a").content)["topic"]
                    wrong_args = {"action": "update", "topic_id": "bench-topic-a",
                                  "changes": {"title": "借书一"}, "expected_digest": original["edit_digest"]}
                wrong_result = ask("manage_topics", **wrong_args)
                assert not wrong_result.is_error, (case_id, wrong_result.error_type, wrong_result.content)
                changed = world.snapshot()
                assert not _expectation({"kind": "unchanged_business", "value": True},
                                        {"before": before, "after": changed})[0]
                evidence["after"] = changed
                evidence["calls"] = calls + [{"tool": "manage_topics", "arguments": wrong_args, "tool_call_id": "wrong"}]
                negative = {c["id"]: c["status"] for c in grade_case(case, evidence)["checks"]}
                assert negative["forbidden_actions"] == "failed" and negative["outcome_01"] == "failed", (case_id, negative)
    finally:
        world.database.close()


class ExpansionReadonlyAdmissionTests(unittest.TestCase):
    def test_cases_in_independent_worlds(self):
        from creatoros.integrations.media_tools import media_tool
        try:
            media_tool("ffprobe")
            media_tool("ffmpeg")
        except ValueError:
            self.skipTest("existing ffmpeg and ffprobe required")
        for case_id in CASES:
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as root:
                result = subprocess.run([sys.executable, "-m", "tests.test_expansion_readonly_admission", root, case_id],
                                        capture_output=True, text=True, timeout=100)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if len(sys.argv) == 3:
        worker(sys.argv[1], sys.argv[2])
    else:
        unittest.main()
