"""Real loopback/SQLite checks for the chat-owned authored-draft workflow."""
import base64
from contextlib import closing
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
from sqlalchemy import select, func

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.storage import ContentRepository, CreatorPlatform, Series, Topic, ContentRun
from creatoros.tools.chat_images import create_skill_draft
from creatoros.tools.execution import execute_tool_call
from creatoros.tools.model_projection import project_tool_messages
from creatoros.web.chat import ACCOUNT_TOOLS, STUDIO_TOOLS
from creatoros.web.chat_links import tool_links
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class AuthoredSkillActionTests(unittest.TestCase):
    def test_real_chat_adapter_to_registration_and_account_binding(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database, runs, producer, app = make_fixture(root)
            try:
                ContentRepository(database).create_creator(creator_id="other", display_name="另一个账号",
                    platform=CreatorPlatform.XIAOHONGSHU)
                chat, service = app.state.chat, app.state.skill_extractions
                sid, other = chat.create("review-lab")["id"], chat.create("other")["id"]
                image = io.BytesIO()
                Image.new("RGB", (20, 30), "blue").save(image, format="PNG")
                original = image.getvalue()
                attachment = chat.attachments.put(sid, "reference.png", base64.b64encode(original).decode())
                prompt = "  保留这份已经调好的提示词。\r\n制作四格情境漫画，不添加额外标题。\n"
                arguments = dict(name="authored-comic", description="制作四格漫画", content=prompt,
                    display_name="我的漫画", attachment_ids=[attachment["id"]])
                initial_count = len(service.catalog.list())
                with serve(app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
                    context = RuntimeContext(project_root=root, studio_url=url, agent_session_id=sid,
                        user_request_id="author-turn", creator_id="review-lab", allowed_tools=ACCOUNT_TOOLS)
                    def execute(name, arguments):
                        return execute_tool_call(ToolCall("call", name, json.dumps(arguments, ensure_ascii=False)),
                                                 context=context, model_requested=True)
                    result = execute("create_skill_draft", arguments)
                    self.assertFalse(result.is_error, result.content)
                    job = json.loads(result.content)
                    self.assertEqual(job["status"], "ready")
                    self.assertIsNone(job["thread_id"])
                    self.assertNotIn("source_text", json.loads(result.model_content))
                    self.assertNotIn("skill_md", result.model_content)
                    ledger = [{"role": "assistant", "tool_calls": [{"id": "create", "function": {"name": "create_skill_draft"}}]},
                              {"role": "tool", "tool_call_id": "create", "content": result.content}]
                    self.assertEqual(project_tool_messages(ledger)[1]["content"], result.model_content)
                    self.assertEqual(ledger[1]["content"], result.content)
                    self.assertEqual(tool_links("create_skill_draft", result.content)[0]["url"], job["url"])
                    # The host derives the local route from the checked job ID; ignores arbitrary URLs.
                    self.assertEqual(tool_links("create_skill_draft", json.dumps({**job, "url": "https://evil.invalid"}))[0]["url"], job["url"])
                    self.assertEqual(len(service.catalog.list()), initial_count)
                    self.assertEqual(service.file(job["id"], "legacy_end_to_end", "assets/source.txt").read_bytes(), prompt.encode())
                    self.assertEqual(service.file(job["id"], "legacy_end_to_end", "assets/reference-01.png").read_bytes(), original)
                    repeated = execute("create_skill_draft", arguments)
                    self.assertEqual(json.loads(repeated.content)["id"], job["id"])
                    self.assertEqual(len(service.list()), 1)
                    headers = {"x-creatoros-agent-session": sid}
                    other_headers = {"x-creatoros-agent-session": other}
                    job_url = "/api/skill-extractions/" + job["id"]
                    self.assertEqual(client.get(job_url, headers=headers).status_code, 200)
                    self.assertEqual(client.get(job_url, headers=other_headers).status_code, 403)
                    self.assertEqual(client.post(job_url + "/save", headers=other_headers,
                        json={"expected_digest": job["digest"]}).status_code, 403)
                    self.assertEqual(client.post("/api/skill-extractions/author", headers=headers, json={}).status_code, 403)
                    endpoint = f"/api/agent/sessions/{sid}/image-actions/author"
                    payload = {**arguments, "request_id": "http-idempotency"}
                    created = client.post(endpoint, headers=headers, json=payload)
                    self.assertEqual(created.status_code, 201, created.text)
                    replay = client.post(endpoint, headers=headers, json=payload)
                    self.assertEqual(replay.json()["id"], created.json()["id"])
                    changed = client.post(endpoint, headers=headers, json={**payload, "content": "changed"})
                    self.assertEqual(changed.status_code, 409, changed.text)
                    self.assertEqual(client.post(endpoint, headers=other_headers, json=payload).status_code, 403)
                    self.assertEqual(client.post(f"/api/agent/sessions/{other}/image-actions/author",
                        headers=other_headers, json=payload).status_code, 404)
                    invalid = client.post(endpoint, headers=headers, json={**payload, "request_id": "bad-frontmatter",
                        "content": "---\nname: different\ndescription: other\n---\nbody"})
                    self.assertEqual(invalid.status_code, 422, invalid.text)
                    saved = execute("save_extracted_skills", dict(job_id=job["id"], expected_digest=job["digest"]))
                    self.assertFalse(saved.is_error, saved.content)
                    saved_job = json.loads(saved.content)
                    identity = saved_job["saved_skills"][0]["id"]
                    self.assertEqual(service.catalog.read_skill_file(identity, "assets/reference-01.png")["content"], original)
                    self.assertEqual(service.catalog.read_skill_file(identity, "assets/source.txt")["content"], prompt)
                    composed = execute("compose_series", dict(name="我的新栏目", skill_name=identity))
                    self.assertFalse(composed.is_error, composed.content)
                    series_id = json.loads(composed.content)["series_id"]
                    denied = execute("compose_series", dict(name="越权栏目", skill_name=identity, creator_id="other"))
                    self.assertTrue(denied.is_error)
                    self.assertEqual(denied.error_type, "agent_scope_rejected")
                    with database.session() as db:
                        series = db.get(Series, series_id)
                        self.assertEqual(series.creator_id, "review-lab")
                        self.assertEqual(series.skill_name, identity)
                        self.assertEqual(db.scalar(select(func.count()).select_from(ContentRun)), 0)
                        self.assertEqual(db.scalar(select(func.count()).select_from(Topic)), 2)
                    self.assertEqual(producer.calls, 0)
                    self.assertIsNone(service.worker)
            finally:
                database.close()

    def test_host_identity_required(self):
        arguments = dict(name="example", description="example", content="unchanged")
        self.assertEqual(create_skill_draft(**arguments).error_type, "session_required")
        context = RuntimeContext(project_root=Path.cwd(), agent_session_id="session")
        self.assertEqual(create_skill_draft(**arguments, context=context).error_type, "request_required")
        self.assertIn("create_skill_draft", ACCOUNT_TOOLS)
        self.assertIn("create_skill_draft", STUDIO_TOOLS)

    def test_overview_installs_one_skill_and_reuses_legacy_job_role(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database, _, _, app = make_fixture(root)
            # Inspect persisted submissions without starting the Git worker.
            with closing(database), patch.object(app.state.skill_installs, "_kick"), serve(app) as url:
                sid = app.state.chat.create()["id"]
                account_sid = app.state.chat.create("review-lab")["id"]
                with httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
                    context = RuntimeContext(project_root=root, studio_url=url, agent_session_id=sid,
                        user_request_id="install-turn", allowed_tools=STUDIO_TOOLS)
                    old = client.post("/api/producer-skills/install", json={
                        "github_url": "https://github.com/example/legacy-skill", "role": "mind"})
                    old.raise_for_status()
                    legacy_job = old.json()

                    def submit(github_url, ctx=context):
                        return execute_tool_call(ToolCall("install", "install_producer_skill",
                            json.dumps({"github_url": github_url})), context=ctx, model_requested=True)

                    replay = submit(legacy_job["github_url"])
                    self.assertFalse(replay.is_error, replay.content)
                    self.assertEqual(json.loads(replay.content)["id"], legacy_job["id"])
                    self.assertEqual(json.loads(replay.content)["role"], "mind")
                    submitted = submit("https://github.com/example/complete-skill")
                    self.assertFalse(submitted.is_error, submitted.content)
                    job_id = json.loads(submitted.content)["id"]
                    job = client.get(f"/api/producer-skills/jobs/{job_id}")
                    job.raise_for_status()
                    self.assertEqual(job.json()["role"], "legacy_end_to_end")
                    account_context = RuntimeContext(project_root=root, studio_url=url,
                        agent_session_id=account_sid, user_request_id="install-turn", creator_id="review-lab",
                        allowed_tools=ACCOUNT_TOOLS)
                    self.assertEqual(submit("https://github.com/example/account-skill", account_context).error_type,
                        "tool_not_exposed")


if __name__ == "__main__":
    unittest.main()
