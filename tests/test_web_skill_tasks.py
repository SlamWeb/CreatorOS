"""Account authorization and immutable delivery through the Studio tool and HTTP interfaces."""
import base64
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

import httpx
from PIL import Image

from creatoros.ai.types import ToolCall
from creatoros.tools.definitions import tools
from creatoros.tools.execution import execute_tool_call
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.web.chat import ACCOUNT_TOOLS, ChatRuntimeContext
from creatoros.web.chat_links import tool_links
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class WebSkillTaskTests(unittest.TestCase):
    def test_task_scope_and_immutable_delivery(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db, _, producer, app = make_fixture(root)
            try:
                chat = app.state.chat
                session = chat.create("review-lab")["id"]
                other_session = chat.create("review-lab")["id"]
                turn = str(uuid4())
                data = io.BytesIO()
                Image.new("RGB", (8, 9), (19, 51, 76)).save(data, format="PNG")
                attachment = chat.attachments.put(session, "参考图.png", base64.b64encode(data.getvalue()).decode())
                task = chat.skill_tasks.for_turn(session, "review-lab", turn, [attachment], [])
                ordinary = ChatRuntimeContext(project_root=root, allowed_tools=ACCOUNT_TOOLS)
                authorized = ChatRuntimeContext(project_root=root, creator_id="review-lab",
                    allowed_tools=ACCOUNT_TOOLS | {"manage_skill_task"}, skill_task=task)

                def call(operation, context=authorized, **fields):
                    return execute_tool_call(ToolCall(str(uuid4()), "manage_skill_task",
                        json.dumps({"operation": operation, **fields})), context, model_requested=True)

                def result(operation, **fields):
                    value = call(operation, **fields)
                    self.assertFalse(value.is_error, value.content)
                    return json.loads(value.content)

                self.assertNotIn("manage_skill_task", {s["function"]["name"] for s in model_tool_schemas(tools, ordinary)})
                self.assertIn("manage_skill_task", {s["function"]["name"] for s in model_tool_schemas(tools, authorized)})
                self.assertEqual(call("prepare", context=ordinary).error_type, "tool_not_exposed")
                self.assertTrue(call("write", path="out/early.txt", content="early").is_error)
                self.assertTrue(call("prepare", skills=["invented-id"]).is_error)
                prepared = result("prepare", skills=["knowledge-to-carousel"])
                self.assertEqual(prepared["task_id"], turn)
                self.assertEqual(prepared["inputs"][0]["sha256"], attachment["id"])
                self.assertTrue(any(item["path"].endswith("/SKILL.md") for item in prepared["files"]))
                self.assertTrue(call("prepare", skills=[]).is_error)
                self.assertTrue(call("write", path="inputs/image-01.png", content="bad").is_error)
                self.assertTrue(call("write", path="work/../../escape.txt", content="bad").is_error)
                result("write", path="out/口播.txt", content="第一次交付")
                published = result("publish", paths=["out/口播.txt"])
                result("write", path="out/口播.txt", content="后续修改")
                self.assertEqual(result("read", path="out/口播.txt")["content"], "后续修改")
                link, = tool_links("manage_skill_task", json.dumps(published))
                self.assertEqual(link["kind"], "download")
                with serve(app) as base, httpx.Client(base_url=base, timeout=15, trust_env=False) as client:
                    self.assertEqual(client.get(link["url"]).status_code, 404)
                    doc = chat._read(session)
                    doc["requests"].append({"id": turn, "skill_workspace": True})
                    chat._save(doc)
                    download = client.get(link["url"])
                    self.assertEqual(download.status_code, 200)
                    self.assertEqual(download.content, "第一次交付".encode())
                    self.assertIn("attachment", download.headers["content-disposition"])
                    self.assertEqual(client.get(link["url"].replace(session, other_session)).status_code, 404)
                    self.assertEqual(client.get(link["url"], headers={"x-creatoros-agent-session": other_session}).status_code, 403)
                self.assertEqual(producer.calls, 0)
            finally:
                db.close()

    def test_opt_in_rejected_for_overview_and_rsi(self):
        with TemporaryDirectory() as temp:
            db, _, _, app = make_fixture(Path(temp))
            try:
                with serve(app) as base, httpx.Client(base_url=base, timeout=15, trust_env=False) as client:
                    for creator, rsi in [(None, False), ("review-lab", True)]:
                        chat = client.post("/api/agent/sessions", json={"creator_id": creator}).json()
                        response = client.post(f"/api/agent/sessions/{chat['id']}/turns", json={
                            "text": "试用", "request_id": str(uuid4()), "expected_version": chat["version"],
                            "iteration_mode": rsi, "skill_workspace": True,
                        })
                        self.assertEqual(response.status_code, 422)
            finally:
                db.close()
