"""Model-view contracts: concise indexes, complete details, unchanged evidence."""
from copy import deepcopy
import json
import unittest

from creatoros.tools.model_projection import project_model_data, project_tool_messages


class ToolIndexContextTests(unittest.TestCase):
    def test_topic_index_keeps_all_identities_paging_and_explicit_preview(self):
        rows = [{"id": f"t-{n}", "series_id": "s-1", "title": f"词组{n}",
                 "selection_state": "queued", "status": "queued", "brief": "完整场景" * 150,
                 "sources": [{"title": "词典", "url": "https://example.com/source"}],
                 "edit_digest": "a" * 64, "management_actions": ["update", "remove"],
                 "available_actions": ["start"], "production_task_count": 0} for n in range(36)]
        raw = {"action": "list", "series_id": "s-1", "items": rows,
               "page": {"offset": 0, "limit": 100, "total": 36, "next_offset": None}}
        before = deepcopy(raw)
        for name in ("manage_topics", "list_series_topics"):
            view = project_model_data(name, raw)
            self.assertEqual([r["id"] for r in view["items"]], [r["id"] for r in rows])
            self.assertEqual(view["page"], raw["page"])
            row = view["items"][0]
            self.assertEqual(row["brief_preview"], rows[0]["brief"][:160])
            self.assertTrue(row["brief_truncated"])
            self.assertEqual(row["available_actions"], ["start"])
            self.assertEqual(row["management_actions"], ["update", "remove"])
            self.assertEqual(row["production_task_count"], 0)
            for field in ("brief", "sources", "edit_digest"):
                self.assertNotIn(field, row)
            self.assertEqual(project_model_data(name, view), view)
        self.assertEqual(raw, before)

    def test_explicit_topic_detail_keeps_full_brief_sources_and_edit_digest(self):
        topic = {"id": "t-1", "title": "词组", "brief": "完整要求" * 500,
                 "status": "queued", "sources": [{"title": "词典", "url": "https://example.com"}],
                 "edit_digest": "b" * 64}
        self.assertEqual(project_model_data("manage_topics", {"action": "get", "topic": topic})["topic"], topic)

    def test_pending_candidates_keep_the_only_available_selection_evidence(self):
        candidate = {"id": "candidate", "selection_state": "pending", "batch_id": "b" * 32,
                     "candidate_id": "c-1", "reference_id": "ref-1", "title": "候选",
                     "angle": "完整切入点" * 100, "rationale": "依据",
                     "sources": [{"title": "词典", "url": "https://example.com"}]}
        view = project_model_data("manage_topics", {"action": "list", "items": [candidate]})
        self.assertEqual(view["items"], [candidate])

    def test_short_preview_does_not_claim_truncation_and_empty_list_stays_empty(self):
        view = project_model_data("manage_topics", {"action": "list", "items": [
            {"id": "t", "status": "queued", "brief": "完整短场景"}]})
        self.assertEqual(view["items"][0]["brief_preview"], "完整短场景")
        self.assertNotIn("brief_truncated", view["items"][0])
        self.assertEqual(project_model_data("manage_topics", {"action": "list", "items": []})["items"], [])

    def test_plan_writes_omit_argument_echo_but_get_keeps_replanning_inputs(self):
        raw = {"id": "plan-1", "version": 4, "status": "needs_attention", "steps": [{
            "id": "images", "title": "生图", "tool": "start_content_run",
            "arguments": {"topic_id": "t", "tasks": [{"brief": "完整场景" * 100}]},
            "depends_on": [], "wait_for_completion": True, "status": "failed",
            "error": "需要核对", "retryable": False, "skippable": False,
            "result": {"run_id": "r-1", "status": "unknown"}}]}
        for action in ("create", "update", "get", "resume"):
            messages = [{"role": "assistant", "content": "", "tool_calls": [{"id": "c",
                "name": "manage_plan", "arguments": json.dumps({"action": action, "plan_id": "plan-1"})}]},
                {"role": "tool", "tool_call_id": "c", "content": json.dumps(raw)}]
            before = deepcopy(messages)
            view = json.loads(project_tool_messages(messages)[-1]["content"])
            step = view["steps"][0]
            self.assertEqual("arguments" in step, action not in {"create", "update"})
            self.assertEqual(step["result"], {"run_id": "r-1", "status": "unknown"})
            self.assertFalse(step["retryable"])
            self.assertFalse(step["skippable"])
            self.assertEqual(messages, before)

    def test_batch_receipt_factors_identical_skill_and_parent_without_losing_tasks(self):
        raw = {"topic_id": "t", "collection_id": "col", "deduplicated": False, "items": [{
            "id": f"task-{n}", "topic_id": "t", "title": f"画面{n}", "run_id": f"r-{n}",
            "run_version": 1, "status": "queued", "position": n, "skill_summary": "共同风格 v3",
            "last_error": None, "depends_on": [], "url": f"/runs/r-{n}"} for n in range(25)]}
        before = deepcopy(raw)
        view = project_model_data("start_content_run", raw)
        self.assertEqual(view["skill_summary"], "共同风格 v3")
        self.assertEqual(len(view["items"]), 25)
        self.assertFalse(view["deduplicated"])
        for row in view["items"]:
            self.assertNotIn("topic_id", row)
            self.assertNotIn("skill_summary", row)
            self.assertNotIn("last_error", row)
            self.assertIn("run_id", row)
            self.assertIn("url", row)
        self.assertEqual(project_model_data("start_content_run", view), view)
        self.assertEqual(raw, before)
        raw["items"][0]["skill_summary"] = "独立版本 v2"
        mixed = project_model_data("start_content_run", raw)
        self.assertNotIn("skill_summary", mixed)
        self.assertEqual(mixed["items"][0]["skill_summary"], "独立版本 v2")

    def test_task_list_drops_only_exact_aliases_and_preserves_failures_and_time(self):
        raw = {"items": [{"id": "r-1", "run_id": "r-1", "kind": "production", "status": "failed",
                          "title": "失败任务", "last_activity_at": None, "updated_at": "now",
                          "last_error": "读取失败", "series_id": "s"},
                         {"id": "job-2", "run_id": "r-2", "status": "running", "last_activity_at": "later"}],
               "summary": {"active": 1, "failed": 1}, "as_of": "now", "filter": {"series_id": "s"}}
        view = project_model_data("get_creator_tasks", raw)
        self.assertNotIn("id", view["items"][0])
        self.assertNotIn("last_activity_at", view["items"][0])
        self.assertEqual(view["items"][0]["last_error"], "读取失败")
        self.assertEqual(view["items"][1]["id"], "job-2")
        self.assertEqual(view["as_of"], "now")
        self.assertEqual(view["summary"], raw["summary"])

    def test_reuse_guidance_reaches_actual_account_schema(self):
        from creatoros.context import RuntimeContext
        from pathlib import Path
        from creatoros.tools import tools
        from creatoros.tools.host_contract import model_tool_schemas
        schemas = model_tool_schemas(tools, RuntimeContext(Path.cwd(), creator_id="a"))
        descriptions = {s["function"]["name"]: s["function"]["description"] for s in schemas}
        self.assertIn("完整列表", descriptions["manage_topics"])
        self.assertIn("brief_preview", descriptions["manage_topics"])
        self.assertIn("复用", descriptions["list_creator_series"])
        self.assertIn("栏目", descriptions["get_creator_tasks"])

    def test_real_http_plan_receipt_is_slim_but_raw_and_get_keep_arguments(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from creatoros.ai.types import ToolCall
        from creatoros.context import RuntimeContext
        from creatoros.tools import execute_tool_call
        from creatoros.web.chat import ACCOUNT_TOOLS
        from tests.agent_studio_support import serve
        from tests.studio_review_fixtures import make_fixture
        with TemporaryDirectory() as directory:
            db, _, producer, app = make_fixture(Path(directory))
            app.state.agent_plans.set_continuation_callback(None)
            try:
                sid = app.state.chat.create("review-lab")["id"]
                with serve(app) as url:
                    context = RuntimeContext(Path(directory), studio_url=url, creator_id="review-lab",
                        allowed_tools=ACCOUNT_TOOLS, agent_session_id=sid, user_request_id="create-plan")
                    arguments = {"action": "create", "title": "只读计划", "steps": [{"id": "read",
                        "title": "等待人工判断", "tool": "request_review", "arguments": {"question": "继续吗？"}}]}
                    receipt = execute_tool_call(ToolCall("create", "manage_plan", json.dumps(arguments)),
                                                context, model_requested=True)
                    self.assertFalse(receipt.is_error, receipt.content)
                    raw = json.loads(receipt.to_raw_content())
                    slim = json.loads(receipt.to_model_content())
                    self.assertEqual(raw["steps"][0]["arguments"], {"question": "继续吗？"})
                    self.assertNotIn("arguments", slim["steps"][0])
                    read = execute_tool_call(ToolCall("get", "manage_plan", json.dumps({
                        "action": "get", "plan_id": raw["id"]})), context, model_requested=True)
                    self.assertFalse(read.is_error, read.content)
                    self.assertEqual(json.loads(read.to_model_content())["steps"][0]["arguments"],
                                     raw["steps"][0]["arguments"])
                    self.assertEqual(producer.calls, 0)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
