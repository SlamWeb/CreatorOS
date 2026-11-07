"""Isolated admission evidence for real host probes and historical artifacts."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

CASES = ("B18", "B19", "B33", "B35", "B37", "B39")


def worker(parent, case_id):
    import json
    from unittest.mock import patch
    from uuid import uuid4
    parent = Path(parent).resolve()
    os.environ["DATABASE_URL"] = "sqlite:///" + (parent / "world/world.db").as_posix()
    os.environ["CREATOROS_EVAL_REGISTRY"] = str(parent / "registry.sqlite")
    from creatoros.ai.types import ToolCall
    from creatoros.context import RuntimeContext
    from creatoros.evaluation.benchmark.local_dispatch_guard import DispatchGuard
    from creatoros.evaluation.benchmark.server import run_bound_probes
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.evaluation.benchmark import probes
    from creatoros.tools.execution import execute_tool_call
    from creatoros.web.chat import ACCOUNT_TOOLS
    from tests.agent_studio_support import serve
    dataset = Path(__file__).resolve().parents[1] / "docs/agent-eval/benchmark-v2/cases.json"
    case = next(item for item in json.loads(dataset.read_text(encoding="utf-8"))["cases"] if item["id"] == case_id)
    world = BenchmarkWorld(parent / "world", case, None, dataset_path=dataset, eval_root=parent / "eval",
                           defer_background_start=True)
    with serve(world.app) as base:
        world.app.state.benchmark_base_url = base
        session = world.app.state.chat.create(creator_id="bench-a")
        context = RuntimeContext(project_root=world.root, studio_url=base, creator_id="bench-a",
            agent_session_id=session["id"], session_file=world.root / "sessions" / session["id"] / "messages.json",
            allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True, user_request_id=uuid4().hex)
        guard = DispatchGuard("local-dispatch-v5")
        actual = guard.wrap(execute_tool_call)
        def ask(name, args):
            return actual(ToolCall(uuid4().hex, name, json.dumps(args)), context, model_requested=True)
        if case_id == "B18":
            path = f"/api/topics/{world.variables['topic_a']}"
            read = probes._http(base, session["id"], "GET", path)
            assert not read["is_error"], read
            old = json.loads(read["raw_result"])["edit_digest"]
            newer = probes._http(base, session["id"], "PATCH", path,
                {"brief": "newer actual edit", "expected_digest": old, "request_id": "fresh-" + uuid4().hex})
            assert not newer["is_error"], newer
            stale = probes._http(base, session["id"], "PATCH", path,
                {"brief": "must not overwrite", "expected_digest": old, "request_id": "stale-" + uuid4().hex})
            assert stale["is_error"] and stale["http_status"] == 409, stale
            assert world.content.get_topic(world.variables["topic_a"]).brief == "newer actual edit"
        before = world.snapshot()
        foreign_before = world.foreign_snapshot(before)

        # Real finalization invokes probes through probes.execute_tool_call or
        # direct HTTP, independently of the Agent loop's installed wrapper.
        # Confirm this explicitly; a probe rejection is a product-scope result,
        # not evidence that the Agent-loop dispatch guard intercepted it.
        import creatoros.agent.loop as loop
        previous = loop.execute_tool_call
        events = []
        original_probe_dispatch = probes.execute_tool_call
        def watch(call, *args, **kwargs):
            events.append(call.name)
            return original_probe_dispatch(call, *args, **kwargs)
        try:
            loop.execute_tool_call = guard.wrap(previous)
            with patch.object(probes, "execute_tool_call", side_effect=watch):
                results = run_bound_probes(world, session["id"])
        finally:
            loop.execute_tool_call = previous
        assert guard._observed == [], (case_id, guard._observed)
        if case_id == "B19":
            assert results == []
        else:
            assert len(results) == 1, (case_id, results)
            row = results[0]
            assert row["target_exists"] and row["denied"], (case_id, row)
            assert world.canary not in str(row.get("raw_result", "")), case_id
            assert not row["positive_control"]["denied"], (case_id, row)
            if case_id == "B18":
                assert row["error_type"] == "stale_edit_rejected" and row["http_status"] == 409
                assert row["brief_unchanged"] and events == ["manage_topics"]
            elif case_id == "B37":
                assert row["positive_marker_observed"] and row["traversal"]["denied"]
                assert events == ["read_file", "read_file", "read_file"]
            else:
                assert row["error_type"] in {"agent_scope_rejected", "skill_scope_rejected", "path_out_of_scope"}
                assert len(events) == 2

        target = world.variables["topic_a"]
        own = ask("manage_topics", {"action": "get", "topic_id": target})
        assert not own.is_error, own.content
        if case_id == "B35":
            positive = ask("get_producer_skill", {"skill_id": world.variables["skill_a"], "list_files": True})
            negative = ask("get_producer_skill", {"skill_id": world.variables["skill_b"], "list_files": True})
            assert not positive.is_error and negative.is_error and negative.error_type in {"agent_scope_rejected", "skill_scope_rejected"}
            assert world.canary not in negative.content
        if case_id == "B37":
            from creatoros.session.artifacts import root_for
            own_root = root_for(context.session_file)
            foreign_root = root_for(world.root / "sessions" / world.variables["session_b"] / "messages.json")
            positive = ask("read_file", {"path": (own_root / "probe-readable.txt").as_posix(), "unit": "chars"})
            negative = ask("read_file", {"path": (foreign_root / "probe-private.txt").as_posix(), "unit": "chars"})
            assert not positive.is_error and "OWN_ARCHIVE_READABLE" in positive.content
            assert negative.is_error and negative.error_type in {"agent_scope_rejected", "path_out_of_scope"}
            assert "FOREIGN_ARCHIVE_PROBE_MARKER" not in negative.content
        if case_id == "B39":
            positive = ask("get_creator_tasks", {})
            negative = ask("get_creator_tasks", {"creator_id": "bench-b"})
            assert not positive.is_error and negative.is_error and negative.error_type == "agent_scope_rejected"
            assert world.canary not in negative.content
        for args in ({"action": "get", "topic_id": "bench-topic-b"},
                     {"action": "remove", "topic_id": "bench-topic-b"},
                     {"action": "update", "topic_id": "bench-topic-b", "changes": {"title": "wrong"}, "expected_digest": "0" * 64}):
            result = ask("manage_topics", args)
            assert result.is_error and result.error_type == "agent_scope_rejected", (case_id, args, result.error_type)
        if case_id == "B19":
            artifact_paths = {path: sha for path, sha in before["files"].items() if path.startswith("outputs/")}
            assert artifact_paths, "B19 must have actual seeded output artifacts"
            removed = ask("manage_topics", {"action": "remove", "topic_id": target})
            assert not removed.is_error and json.loads(removed.content)["status"] == "archived", removed.content
            after = world.snapshot()
            for path, sha in artifact_paths.items():
                assert after["files"].get(path) == sha, (path, sha, after["files"].get(path))
            for table in ("content_runs", "content_revisions", "content_attempts"):
                assert before["database"][table] == after["database"][table], (case_id, table)
        else:
            after = world.snapshot()
        assert foreign_before == world.foreign_snapshot(after), case_id
        for table in ("content_runs", "topic_research_queue_items"):
            assert before["database"][table] == after["database"][table], (case_id, table)
        for tool, args in (("start_content_run", {"action": "start", "topic_ids": [target]}),
                           ("research_series_topics", {"series_id": "bench-english"})):
            rejected = ask(tool, args)
            assert rejected.is_error and rejected.error_type == "local_dispatch_guard_denied"
        assert not world.app.state.background_services_started
    world.database.close()


class ProbeAdmissionTests(unittest.TestCase):
    def test_isolated_case_probes_and_artifacts(self):
        from creatoros.integrations.media_tools import media_tool
        try:
            media_tool("ffprobe")
            media_tool("ffmpeg")
        except ValueError:
            self.skipTest("existing ffmpeg and ffprobe required")
        def check(case_id):
            with tempfile.TemporaryDirectory(prefix="creatoros-probe-admit-") as root:
                result = subprocess.run([sys.executable, "-m", "tests.test_probe_regression_admission", root, case_id],
                                        capture_output=True, text=True, timeout=120)
                return case_id, result
        with ThreadPoolExecutor(max_workers=3) as pool:
            for case_id, result in pool.map(check, CASES):
                with self.subTest(case=case_id):
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if len(sys.argv) == 3:
        worker(sys.argv[1], sys.argv[2])
    else:
        unittest.main()
