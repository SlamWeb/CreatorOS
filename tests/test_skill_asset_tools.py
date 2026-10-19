"""Actual Agent tool -> scoped loopback HTTP -> Skill bytes, with no model calls."""
import base64
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select, func

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.integrations.producer_skills import freeze_skill, _digest
from creatoros.storage import ContentRepository, CreatorPlatform, Series, ContentRun
from creatoros.tools.execution import execute_tool_call
from creatoros.tools.studio import UpdateProducerSkillFileArgs, update_producer_skill_file
from creatoros.web.chat import ACCOUNT_TOOLS
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


def png(color):
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), color).save(buffer, format="PNG")
    return buffer.getvalue()


class SkillAssetToolTests(unittest.TestCase):
    def test_schema_rejects_mixed_or_missing_content(self):
        common = dict(skill_id="comic--" + "a" * 16, path="assets/ref.png", expected_digest="0" * 64)
        for extra in ({}, {"content": "text", "attachment_id": "image"},
                      {"operation": "replace_image"},
                      {"operation": "add_image", "attachment_id": "image", "content": "text"},
                      {"operation": "delete_image", "attachment_id": "image"}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                UpdateProducerSkillFileArgs(**common, **extra)
        self.assertEqual(UpdateProducerSkillFileArgs(**common, content="").operation, "write_text")
        for operation in ("add_image", "replace_image"):
            self.assertEqual(UpdateProducerSkillFileArgs(**common, operation=operation, attachment_id="image").operation, operation)
        self.assertEqual(UpdateProducerSkillFileArgs(**common, operation="delete_image").operation, "delete_image")

    def test_images_need_host_conversation_and_request(self):
        common = dict(skill_id="comic--" + "a" * 16, path="assets/ref.png", expected_digest="0" * 64,
                      operation="replace_image", attachment_id="image")
        self.assertEqual(update_producer_skill_file(**common).error_type, "session_required")
        context = RuntimeContext(project_root=Path.cwd(), agent_session_id="session")
        self.assertEqual(update_producer_skill_file(**common, context=context).error_type, "request_required")

    def test_real_account_adapter_replaces_edits_and_preserves_frozen(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            database, runs, producer, app = make_fixture(root)
            try:
                catalog, chat = app.state.skill_installs.catalog, app.state.chat
                ContentRepository(database).create_creator(creator_id="other", display_name="其他账号",
                    platform=CreatorPlatform.XIAOHONGSHU)
                source = root / "original"
                (source / "assets").mkdir(parents=True)
                original = "---\nname: comic\ndescription: test\ncreatoros-output: social-content-pack.image-carousel\n---\n[参考](assets/ref.png)\n"
                (source / "SKILL.md").write_text(original, encoding="utf-8")
                (source / "assets/ref.png").write_bytes(png("blue"))
                skill = catalog.register_local(source, role="legacy_end_to_end")
                identity = skill["id"]
                working = catalog.locate(identity)
                frozen = root / "frozen"
                freeze_skill(working, frozen, _digest(working))
                frozen_digest = _digest(frozen)
                with database.session() as db:
                    db.get(Series, "agent-notes").skill_name = identity
                sid, other = chat.create("review-lab")["id"], chat.create("other")["id"]
                attachment = chat.attachments.put(sid, "new.png", base64.b64encode(png("red")).decode())
                with serve(app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
                    context = RuntimeContext(project_root=root, studio_url=url, creator_id="review-lab",
                        agent_session_id=sid, user_request_id="edit-turn", allowed_tools=ACCOUNT_TOOLS)
                    def execute(arguments):
                        return execute_tool_call(ToolCall("edit", "update_producer_skill_file", json.dumps(arguments)),
                            context=context, model_requested=True)
                    digest = catalog.list_skill_files(identity)["digest"]
                    request = dict(skill_id=identity, path="assets/ref.png", operation="replace_image",
                        attachment_id=attachment["id"], expected_digest=digest)
                    result = execute(request)
                    self.assertFalse(result.is_error, result.content)
                    updated = json.loads(result.content)
                    self.assertNotEqual(updated["digest"], digest)
                    self.assertNotIn("base64", result.content)
                    self.assertEqual(catalog.read_skill_file(identity, "assets/ref.png")["content"], png("red"))
                    self.assertEqual(json.loads(result.model_content)["operation"], "replace")
                    stale = execute(request)
                    self.assertTrue(stale.is_error)
                    self.assertEqual(stale.error_type, "skill_digest_conflict")
                    endpoint = f"/api/agent/sessions/{sid}/image-actions/skill-asset"
                    payload = {**request, "expected_digest": updated["digest"], "request_id": "direct"}
                    self.assertEqual(client.post(endpoint, headers={"x-creatoros-agent-session": other}, json=payload).status_code, 403)
                    self.assertEqual(client.post(f"/api/agent/sessions/{other}/image-actions/skill-asset",
                        headers={"x-creatoros-agent-session": other}, json=payload).status_code, 403)
                    foreign_image = chat.attachments.put(other, "other.png", base64.b64encode(png("green")).decode())
                    bad_attachment = client.post(endpoint, headers={"x-creatoros-agent-session": sid},
                        json={**payload, "attachment_id": foreign_image["id"]})
                    self.assertEqual(bad_attachment.status_code, 404, bad_attachment.text)
                    escaped = execute({**request, "path": "../outside.png", "expected_digest": updated["digest"]})
                    self.assertTrue(escaped.is_error)
                    self.assertFalse((root / "outside.png").exists())
                    added = execute({**request, "operation": "add_image", "path": "assets/new.png", "expected_digest": updated["digest"]})
                    self.assertFalse(added.is_error, added.content)
                    added_digest = json.loads(added.content)["digest"]
                    removed = execute(dict(skill_id=identity, path="assets/new.png", operation="delete_image", expected_digest=added_digest))
                    self.assertFalse(removed.is_error, removed.content)
                    blocked = execute(dict(skill_id=identity, path="assets/ref.png", operation="delete_image", expected_digest=json.loads(removed.content)["digest"]))
                    self.assertTrue(blocked.is_error)
                    self.assertEqual(blocked.error_type, "skill_asset_referenced")
                    self.assertIn("SKILL.md", json.loads(blocked.model_content)["referencing_files"])
                    changed = original.replace("[参考](assets/ref.png)", "严格保持四格，不额外加标题。")
                    text_result = execute(dict(skill_id=identity, path="SKILL.md", content=changed,
                        expected_digest=json.loads(removed.content)["digest"]))
                    self.assertFalse(text_result.is_error, text_result.content)
                    removed_ref = execute(dict(skill_id=identity, path="assets/ref.png", operation="delete_image",
                        expected_digest=json.loads(text_result.content)["digest"]))
                    self.assertFalse(removed_ref.is_error, removed_ref.content)
                    self.assertEqual(catalog.read_skill_file(identity, "SKILL.md")["content"], changed)
                    self.assertEqual(_digest(frozen), frozen_digest)
                    self.assertEqual((frozen / "assets/ref.png").read_bytes(), png("blue"))
                    self.assertEqual((source / "assets/ref.png").read_bytes(), png("blue"))
                    with database.session() as db:
                        self.assertEqual(db.scalar(select(func.count()).select_from(ContentRun)), 0)
                    self.assertEqual(producer.calls, 0)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
