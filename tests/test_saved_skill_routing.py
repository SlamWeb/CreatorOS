"""Real loopback/SQLite regression for a draft saved through the Web flow."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.storage import ContentRepository, CreatorPlatform
from creatoros.tools.execution import execute_tool_call
from creatoros.web.chat import ACCOUNT_TOOLS
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class SavedSkillRoutingTests(unittest.TestCase):
    def test_web_saved_draft_routes_stale_edit_to_installed_skill_without_leaking(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database, _runs, producer, app = make_fixture(root)
            try:
                ContentRepository(database).create_creator(creator_id="other", display_name="另一个账号",
                    platform=CreatorPlatform.XIAOHONGSHU)
                chat, service = app.state.chat, app.state.skill_extractions
                owner_session = chat.create("review-lab")["id"]
                other_session = chat.create("other")["id"]
                owner_context = RuntimeContext(project_root=root, studio_url="", agent_session_id=owner_session,
                    user_request_id="author-turn", creator_id="review-lab", allowed_tools=ACCOUNT_TOOLS)

                def execute(context, name, arguments):
                    return execute_tool_call(ToolCall("call", name, json.dumps(arguments, ensure_ascii=False)),
                        context=context, model_requested=True)

                with serve(app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
                    owner_context = RuntimeContext(project_root=root, studio_url=url, agent_session_id=owner_session,
                        user_request_id="author-turn", creator_id="review-lab", allowed_tools=ACCOUNT_TOOLS)
                    created = execute(owner_context, "create_skill_draft", {
                        "name": "saved-routing-example", "description": "测试已保存草稿的编辑路由",
                        "content": "保留原样的 Skill 正文。\n按给定主题写清步骤和输出格式，不补充未要求的内容。",
                    })
                    self.assertFalse(created.is_error, created.content)
                    draft = json.loads(created.content)

                    headers = {"x-creatoros-agent-session": owner_session}
                    saved_response = client.post(f"/api/skill-extractions/{draft['id']}/save", headers=headers,
                        json={"expected_digest": draft["digest"]})
                    self.assertEqual(saved_response.status_code, 200, saved_response.text)
                    saved = saved_response.json()
                    self.assertEqual(saved["status"], "saved")
                    skill_id = saved["saved_skills"][0]["id"]
                    skill_digest = saved["saved_skills"][0]["digest"]
                    before = service.catalog.read_skill_file(skill_id, "SKILL.md")["content"]

                    stale_edit = execute(owner_context, "edit_extracted_skills", {
                        "job_id": draft["id"], "expected_digest": draft["digest"],
                        "skills": [{"name": "saved-routing-example", "role": "legacy_end_to_end",
                            "skill_md": "---\nname: saved-routing-example\ndescription: updated\n---\n\n" + "新的编辑正文。" * 12}],
                    })
                    self.assertTrue(stale_edit.is_error)
                    self.assertEqual(stale_edit.error_type, "skill_draft_already_saved")
                    for receipt_text in (stale_edit.content, stale_edit.to_model_content()):
                        receipt = json.loads(receipt_text.split("\n", 1)[-1])
                        self.assertEqual(receipt["status"], "saved")
                        self.assertEqual(receipt["saved_skills"][0]["id"], skill_id)
                        self.assertEqual(receipt["saved_skills"][0]["digest"], skill_digest)
                        self.assertEqual(receipt["next_action"], "edit_installed_skill")
                    self.assertEqual(service.catalog.read_skill_file(skill_id, "SKILL.md")["content"], before)

                    other_context = RuntimeContext(project_root=root, studio_url=url,
                        agent_session_id=other_session, user_request_id="other-turn", creator_id="other",
                        allowed_tools=ACCOUNT_TOOLS)
                    cross_account = execute(other_context, "edit_extracted_skills", {
                        "job_id": draft["id"], "expected_digest": draft["digest"],
                        "skills": [{"name": "saved-routing-example", "role": "legacy_end_to_end",
                            "skill_md": "---\nname: saved-routing-example\ndescription: updated\n---\n\n" + "跨账号编辑正文。" * 12}],
                    })
                    self.assertTrue(cross_account.is_error)
                    self.assertNotIn(skill_id, cross_account.content)
                    self.assertNotIn(skill_id, cross_account.to_model_content())
                    self.assertEqual(service.catalog.read_skill_file(skill_id, "SKILL.md")["content"], before)
                    self.assertEqual(producer.calls, 0)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
