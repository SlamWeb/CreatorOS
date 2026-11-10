"""Per-case isolated execution admission, using real guarded tools and local HTTP."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CASES = ("B08", "B09", "B11", "B12", "B13", "B14", "B15", "B16", "B17", "B20")


def worker(parent, case_id):
    import json
    from dataclasses import replace
    from uuid import uuid4
    from creatoros.ai.types import ToolCall
    from creatoros.context import RuntimeContext
    from creatoros.evaluation.benchmark.local_dispatch_guard import DispatchGuard
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.tools.execution import execute_tool_call
    from creatoros.web.chat import ACCOUNT_TOOLS
    from tests.agent_studio_support import serve

    parent = Path(parent).resolve()
    os.environ["DATABASE_URL"] = "sqlite:///" + (parent / "world/world.db").as_posix()
    os.environ["CREATOROS_EVAL_REGISTRY"] = str(parent / "registry.sqlite")
    dataset = Path(__file__).resolve().parents[1] / "docs/agent-eval/benchmark-v2/cases.json"
    case = next(row for row in json.loads(dataset.read_text(encoding="utf-8"))["cases"] if row["id"] == case_id)
    world = BenchmarkWorld(parent / "world", case, None, dataset_path=dataset, eval_root=parent / "eval",
                           defer_background_start=True)
    with serve(world.app) as base:
        doc = world.app.state.chat.create(creator_id="bench-a")
        context = RuntimeContext(project_root=world.root, studio_url=base, creator_id="bench-a",
            agent_session_id=doc["id"], session_file=world.root / "sessions" / doc["id"] / "messages.json",
            allowed_tools=ACCOUNT_TOOLS, archive_only_reads=True, user_request_id=uuid4().hex)
        guard = DispatchGuard("local-dispatch-v3")
        execute = guard.wrap(execute_tool_call)
        def ask(name, **args):
            return execute(ToolCall(uuid4().hex, name, json.dumps(args, ensure_ascii=False)), context,
                           model_requested=True)
        def topic(**args):
            result = ask("manage_topics", **args)
            assert not result.is_error, (case_id, args, result.error_type, result.content)
            return json.loads(result.content)
        before = world.snapshot()
        assert not world.app.state.background_services_started
        if case_id == "B08":
            assert topic(action="get", topic_id="bench-topic-a")["topic"]["title"] == world.content.get_topic("bench-topic-a").title
        elif case_id in {"B09", "B20"}:
            listing = topic(action="list", series_id="bench-english", query={"state": "all", "limit": 100})
            ids = {item["id"] for item in listing["items"]}
            removed = {r["topic_id"] for r in before["database"]["topic_removals"]}
            assert not ids.intersection(removed), (case_id, ids, removed)
            if case_id == "B20":
                assert all(item.get("production_state") in {"unproduced", "produced"} for item in listing["items"])
        elif case_id in {"B11", "B12", "B16"}:
            names = {"B11": ["borrow与lend"], "B12": ["rise与raise", "accept与except", "affect与effect"],
                     "B16": ["图书馆里的borrow与lend"]}[case_id]
            created = topic(action="create", series_id="bench-english", topics=[{"title": name} for name in names])
            ids = created["topic_ids"]
            assert len(ids) == len(names) and len(set(ids)) == len(names)
            context = replace(context, user_request_id=uuid4().hex)
            listing = topic(action="list", series_id="bench-english", query={"state": "all", "limit": 100})
            assert all(sum(item["id"] == identity for item in listing["items"]) == 1 for identity in ids)
        elif case_id in {"B13", "B14", "B17"}:
            original = topic(action="get", topic_id="bench-topic-a")["topic"]
            changes = {"B13": {"brief": "在图书馆借书，用同一场景解释借入和借出。"},
                       "B14": {"title": "借书到底用borrow还是lend"}, "B17": {"brief": ""}}[case_id]
            topic(action="update", topic_id="bench-topic-a", changes=changes, expected_digest=original["edit_digest"])
            current = topic(action="get", topic_id="bench-topic-a")["topic"]
            assert current["id"] == original["id"]
            if case_id == "B14":
                assert current["title"] == changes["title"] and current["brief"] == original["brief"]
            else:
                assert current["title"] == original["title"] and current["brief"] == (changes["brief"] or None)
        elif case_id == "B15":
            result = topic(action="remove", topic_id="bench-topic-a")
            assert result["status"] in {"archived", "deleted"}
            ids = {item["id"] for item in topic(action="list", series_id="bench-english")["items"]}
            assert "bench-topic-a" not in ids
        else:
            raise AssertionError(case_id)

        # All new cases go through the same actual account-scope path. The
        # isolated foreign record must stay unchanged after write attempts.
        foreign_before = world.content.get_topic("bench-topic-b").title
        for args in ({"action": "get", "topic_id": "bench-topic-b"},
                     {"action": "remove", "topic_id": "bench-topic-b"},
                     {"action": "update", "topic_id": "bench-topic-b", "changes": {"title": "forbidden"}, "expected_digest": "0" * 64},
                     {"action": "create", "series_id": "bench-private", "topics": [{"title": "forbidden"}]}):
            denied = ask("manage_topics", **args)
            assert denied.is_error and denied.error_type == "agent_scope_rejected", (case_id, args, denied.error_type)
        assert world.content.get_topic("bench-topic-b").title == foreign_before
        for tool, args in (("start_content_run", {"action": "start", "topic_ids": ["bench-topic-a"]}),
                           ("research_series_topics", {"series_id": "bench-english"})):
            denied = ask(tool, **args)
            assert denied.is_error and denied.error_type == "local_dispatch_guard_denied"
        after = world.snapshot()
        for table in ("content_runs", "topic_research_queue_items"):
            assert before["database"][table] == after["database"][table], (case_id, table)
        assert not world.app.state.background_services_started
    world.database.close()


class TopicAdmissionTests(unittest.TestCase):
    def test_each_case_in_its_own_isolated_world(self):
        from creatoros.integrations.media_tools import media_tool
        try:
            media_tool("ffprobe")
            media_tool("ffmpeg")
        except ValueError:
            self.skipTest("existing ffmpeg and ffprobe required")
        for case_id in CASES:
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as root:
                result = subprocess.run([sys.executable, "-m", "tests.test_topic_regression_admission", root, case_id],
                                        capture_output=True, text=True, timeout=100)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if len(sys.argv) == 3:
        worker(sys.argv[1], sys.argv[2])
    else:
        unittest.main()
