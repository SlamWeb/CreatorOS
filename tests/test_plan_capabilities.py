"""Shared plan permissions at the schema, host and continuation interfaces."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PlanCapabilityTests(unittest.TestCase):
    def test_plan_schema_and_permissions_keep_the_existing_names_and_actions(self):
        from creatoros.plan_capabilities import PLAN_TOOL_NAMES, allows_tool_action, can_wait
        from creatoros.tools.definitions import tool_registry
        from creatoros.web.chat import ACCOUNT_TOOLS

        expected = {
            "request_review": {None},
            "start_content_run": {"start", "tasks", "fresh", "reference"},
            "manage_topics": {"get", "list", "create", "update", "remove"},
            "manage_material": {"list", "get"},
            "manage_media_collection": {"list", "get", "create", "export"},
            "manage_skill_library": {"get", "add"},
            "get_content_run": {None},
            "get_creator_tasks": {None},
            "get_producer_skill": {None},
        }
        schema = tool_registry["manage_plan"].to_schema()["function"]["parameters"]
        self.assertEqual(list(PLAN_TOOL_NAMES), list(expected))
        self.assertEqual(schema["$defs"]["PlanStepArgs"]["properties"]["tool"]["enum"], list(expected))
        self.assertTrue(set(PLAN_TOOL_NAMES) - {"request_review"} <= set(tool_registry) & ACCOUNT_TOOLS)
        for name, actions in expected.items():
            with self.subTest(tool=name):
                for action in actions:
                    self.assertTrue(allows_tool_action(name, action, scope="plan"))
                for action in ("unknown", "analyze", "publish"):
                    self.assertFalse(allows_tool_action(name, action, scope="plan"))
                self.assertEqual(can_wait(name), name == "start_content_run")

    def test_continuation_keeps_direct_reads_separate_from_persisted_edits(self):
        from creatoros.plan_capabilities import CONTINUATION_TOOLS, allows_tool_action

        direct = {
            "manage_plan": {"get", "update", "resume", "retry", "skip"},
            "manage_topics": {"get", "list"}, "manage_material": {"get", "list"},
            "manage_media_collection": {"get", "list"}, "manage_skill_library": {"get"},
            **{name: {None} for name in ("get_content_run", "get_producer_skill", "get_creator_tasks",
                "list_creators", "list_creator_series", "list_producer_skills", "read_image", "read_tool_result")},
        }
        edits = {
            "request_review": {None}, "start_content_run": {"start", "tasks", "fresh", "reference"},
            "manage_topics": {"get", "list"}, "manage_material": {"get", "list"},
            "manage_media_collection": {"get", "list", "create", "export"}, "manage_skill_library": {"get"},
            "get_content_run": {None}, "get_creator_tasks": {None}, "get_producer_skill": {None},
        }
        self.assertEqual(CONTINUATION_TOOLS, frozenset(direct))
        for scope, matrix in (("continuation", direct), ("continuation_edit", edits)):
            for name in set(direct) | set(edits):
                for action in (None, "get", "list", "create", "update", "remove", "export", "add",
                               "start", "tasks", "fresh", "reference", "resume", "retry", "skip", "unknown"):
                    with self.subTest(scope=scope, tool=name, action=action):
                        self.assertEqual(allows_tool_action(name, action, scope=scope), action in matrix.get(name, set()))

    def test_unknown_tools_actions_and_scopes_never_gain_permission(self):
        from creatoros.plan_capabilities import allows_tool_action, can_wait

        for scope in ("plan", "continuation", "continuation_edit", "unknown"):
            for name in ("unknown_tool", "update_producer_skill_file", "write_file"):
                self.assertFalse(allows_tool_action(name, None, scope=scope))
                self.assertFalse(can_wait(name))
            for action in ("unknown", {}, [], False):
                self.assertFalse(allows_tool_action("manage_topics", action, scope=scope))

    def test_registered_model_schemas_and_tool_inventory_are_unchanged(self):
        from creatoros.tools.definitions import tools, tool_registry

        def digest(value):
            encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            return hashlib.sha256(encoded).hexdigest()

        self.assertEqual((len(tool_registry), len(tools)), (57, 53))
        # The current contract deliberately adds read-only production receipt
        # lookup and clarifies that a shared release may be forked privately.
        self.assertEqual(digest(tools), "329be3ebd45c5af8ecded8945305df2df0149e2c95dca70f8282b7d6597e3403")
        original = copy.deepcopy(tools)
        topics = next(tool["function"] for tool in original if tool["function"]["name"] == "manage_topics")
        receipt_description = "；制作结果未知时get原topic_id及返回的production_request_id只读核对，回执unknown不等于未提交，勿换ID重做"
        self.assertEqual(topics["description"].count(receipt_description), 1)
        topics["description"] = topics["description"].replace(receipt_description, "")
        self.assertNotIn("production_request_id", topics["parameters"]["required"])
        self.assertEqual(topics["parameters"]["properties"].pop("production_request_id"), {
            "anyOf": [{"maxLength": 64, "minLength": 1, "type": "string"}, {"type": "null"}],
            "default": None,
            "description": "仅 get：使用未知制作结果中返回的原 request_id 只读核对该次回执。",
            "title": "Production Request Id",
        })
        skill = next(tool["function"] for tool in original if tool["function"]["name"] == "update_producer_skill_file")
        fork_description = ("账号对话中可用的非内置发布版即使原件can_edit=false，用户明确要求时也可另存当前账号私有候选；"
                            "不覆盖共享原件，不访问其他账号私有候选。只问权限或明确不写入时不调用本工具。")
        self.assertEqual(skill["description"].count(fork_description), 1)
        skill["description"] = skill["description"].replace(fork_description, "")
        self.assertEqual(digest(original), "d04835d7e8a0af253f3c8ebc820ec9b7181319d91718850f89c7f7ecd664934e")
        # Revert the explicitly introduced plan-update contract and then
        # verify the entire prior 53-tool snapshot still matches exactly.
        plan = next(tool["function"] for tool in original if tool["function"]["name"] == "manage_plan")
        updated = ("get 当前计划后 update 只传需修改或新增的完整步骤：修改用原步骤 ID，新 ID 只追加；其他步骤保留。"
                   "删除未开始或阻塞的步骤须明确填写 remove_step_ids，并同时调整引用它的依赖；已开始步骤不可删除。")
        previous = "续接后 get 当前计划，update 可只传要修改的未开始或阻塞步骤，其他步骤由宿主保留；"
        self.assertEqual(plan["description"].count(updated), 1)
        plan["description"] = plan["description"].replace(updated, previous)
        update = plan["parameters"]["$defs"]["UpdatePlanArgs"]
        self.assertEqual(update.pop("anyOf"), [
            {"required": ["steps"], "properties": {"steps": {"minItems": 1}}},
            {"required": ["remove_step_ids"], "properties": {"remove_step_ids": {"minItems": 1}}},
        ])
        self.assertEqual(update["properties"].pop("remove_step_ids"), {
            "description": "明确删除的未开始或阻塞步骤 ID；已执行步骤不可删除，不能依靠更换 ID 删除原步骤。",
            "items": {"type": "string"}, "maxItems": 100, "title": "Remove Step Ids", "type": "array",
        })
        self.assertEqual(update["properties"]["steps"].pop("description"),
                         "要修改或新增的完整步骤；修改现有步骤时保留其 ID，新 ID 只追加，未提及步骤保留。")
        update["properties"]["steps"]["minItems"] = 1
        self.assertEqual(update["required"], ["action", "plan_id", "expected_version"])
        update["required"].append("steps")
        self.assertEqual(digest(original), "6b69a56fd2705222cbedd8a7a75f5e86dda8d20ac0d6469e7ee0beda41301fc7")
        run = next(tool["function"] for tool in original if tool["function"]["name"] == "start_content_run")
        reference = run["parameters"]["$defs"]["ReferenceProductionRunArgs"]
        self.assertNotIn("skill_ids", reference["required"])
        self.assertEqual(reference["properties"].pop("skill_ids"), {
            "anyOf": [
                {"items": {"maxLength": 120, "minLength": 1,
                           "pattern": "^(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})$", "type": "string"},
                 "maxItems": 12, "minItems": 1, "type": "array"},
                {"type": "null"},
            ],
            "default": None,
            "description": "本次参考制作使用的一份或多份精确 Skill 版本 ID；省略沿用栏目默认，不改栏目绑定。",
            "title": "Skill Ids",
        })
        added_description = "，可用skill_ids选择本次一份或多份精确版本，不改栏目绑定；省略时沿用默认。同一参考请求改选Skill会冲突，先核对原记录"
        self.assertEqual(run["description"].count(added_description), 1)
        run["description"] = run["description"].replace(added_description, "")
        # Removing only the intended additions must reproduce every old schema,
        # including all other fields on this tool and all 52 other tools.
        self.assertEqual(digest(original), "4b9c15b61a476117e54714716a09d2c2c59f7fa184d9f302e8e4a6b8098cfafb")

    def test_plan_service_rejects_undeclared_tools_actions_and_waits_without_saving(self):
        from tests.studio_review_fixtures import make_fixture

        with tempfile.TemporaryDirectory(prefix="creatoros-plan-capabilities-") as directory:
            db, _, producer, app = make_fixture(Path(directory))
            service = app.state.agent_plans
            try:
                service.start_gate = lambda: False
                sid = app.state.chat.create("review-lab")["id"]
                read = {"id": "read", "title": "读取选题", "tool": "manage_topics",
                        "arguments": {"action": "list", "series_id": "agent-notes"}}
                saved = service.create(sid, "capability-read", "查看", [read], "http://127.0.0.1:18999")
                bad_steps = [
                    {**read, "tool": "unknown_tool"},
                    {**read, "tool": "get_current_time", "arguments": {}},
                    {**read, "arguments": {"action": "publish", "series_id": "agent-notes"}},
                    {**read, "arguments": {"action": "analyze", "series_id": "agent-notes"}},
                    {**read, "wait_for_completion": True},
                ]
                for index, step in enumerate(bad_steps):
                    with self.subTest(step=step), self.assertRaises(ValueError):
                        service.create(sid, f"denied-{index}", "拒绝", [step], "http://127.0.0.1:18999")
                replay = service.create(sid, "capability-read", "查看", [read], "http://127.0.0.1:18999")
                self.assertEqual(replay, saved)
                self.assertEqual([item["id"] for item in service.list(sid)["items"]], [saved["id"]])
                self.assertEqual(producer.calls, 0)
            finally:
                service.shutdown()
                app.state.chat.shutdown()
                db.close()

    def test_continuation_rejects_registered_tool_without_a_declared_grant(self):
        from creatoros.ai.types import ToolCall
        from creatoros.context import RuntimeContext
        from creatoros.tools.execution import execute_tool_call

        context = RuntimeContext(project_root=Path.cwd(), plan_continuation_id="current-plan",
                                 allowed_tools=frozenset({"get_current_time"}))
        result = execute_tool_call(ToolCall("denied", "get_current_time", "{}"), context=context, model_requested=True)
        self.assertTrue(result.is_error)
        self.assertEqual(result.error_type, "agent_scope_rejected")

    def test_continuation_edit_uses_its_own_action_grants_without_dispatch(self):
        from creatoros.ai.types import ToolCall
        from creatoros.context import RuntimeContext
        from creatoros.tools.execution import execute_tool_call
        from tests.agent_studio_support import serve
        from tests.studio_review_fixtures import make_fixture

        with tempfile.TemporaryDirectory(prefix="creatoros-plan-capability-edit-") as directory:
            root = Path(directory)
            db, _, producer, app = make_fixture(root)
            service = app.state.agent_plans
            try:
                service.start_gate = lambda: False
                sid = app.state.chat.create("review-lab")["id"]
                checkpoint = {"id": "review", "title": "确认", "tool": "request_review", "arguments": {"question": "确认后继续？"}}
                with serve(app) as url:
                    created = service.create(sid, "capability-edit", "检查", [checkpoint], url)
                    view = service.get(sid, created["id"])
                    context = RuntimeContext(project_root=root, studio_url=url, creator_id="review-lab",
                        agent_session_id=sid, plan_continuation_id=view["id"], allowed_tools=frozenset({"manage_plan"}))
                    for name, arguments in (
                        ("manage_topics", {"action": "remove", "topic_id": "review-1"}),
                        ("manage_skill_library", {"action": "add", "skill_id": "knowledge-to-carousel"}),
                        ("get_content_run", {"action": "unknown", "run_id": "not-executed"}),
                    ):
                        args = {"action": "update", "plan_id": view["id"], "expected_version": view["version"],
                            "steps": [{"id": "new", "title": "不允许", "tool": name, "arguments": arguments}]}
                        result = execute_tool_call(ToolCall("denied", "manage_plan", json.dumps(args)),
                                                   context=context, model_requested=True)
                        with self.subTest(tool=name, arguments=arguments):
                            self.assertTrue(result.is_error)
                            self.assertEqual(result.error_type, "agent_scope_rejected")
                            self.assertEqual(service.get(sid, view["id"]), view)
                    args = {"action": "update", "plan_id": view["id"], "expected_version": view["version"],
                        "steps": [{"id": "collection", "title": "整理素材", "tool": "manage_media_collection",
                                   "arguments": {"action": "create", "name": "允许计划写入"}}]}
                    result = execute_tool_call(ToolCall("allowed", "manage_plan", json.dumps(args)),
                                               context=context, model_requested=True)
                    self.assertFalse(result.is_error, result.content)
                    saved = service.get(sid, view["id"])
                    self.assertEqual([step["id"] for step in saved["steps"]], ["review", "collection"])
                    self.assertEqual([step["status"] for step in saved["steps"]], ["pending", "pending"])
                    self.assertEqual(producer.calls, 0)
            finally:
                service.shutdown()
                app.state.chat.shutdown()
                db.close()

    def test_capability_import_does_not_initialize_tools_or_agent(self):
        check = "import sys; import creatoros.plan_capabilities; assert 'creatoros.tools' not in sys.modules; assert 'creatoros.agent' not in sys.modules"
        result = subprocess.run([sys.executable, "-c", check], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
