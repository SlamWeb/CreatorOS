"""Bounded multi-edit contract and integrated Skill editing behavior."""
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
from pydantic import ValidationError

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.tools.execution import execute_tool_call
from creatoros.tools.skill_edit_batch import SkillEditChangeArgs
from creatoros.tools.studio import UpdateProducerSkillFileArgs, get_producer_skill
from creatoros.web.chat import ACCOUNT_TOOLS
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class SkillEditBatchTests(unittest.TestCase):
    def test_batch_schema_accepts_only_bounded_well_formed_operations(self):
        write = {"operation": "write_text", "path": "SKILL.md", "content": "new text"}
        image = {"operation": "replace_image", "path": "assets/reference.png", "attachment_id": "attachment-1"}
        self.assertEqual(SkillEditChangeArgs(**write).operation, "write_text")
        self.assertEqual(SkillEditChangeArgs(**image).attachment_id, "attachment-1")
        for invalid in (
            {"path": "SKILL.md", "content": "missing operation"},
            {"operation": "write_text", "path": "SKILL.md"},
            {"operation": "delete_image", "path": "assets/ref.png", "content": "not allowed"},
            {"operation": "add_image", "path": "assets/new.png", "attachment_id": "a", "content": "mixed"},
            {"operation": "replace_image", "path": "assets/ref.png"},
            {"operation": "delete_image", "path": "assets/ref.png", "attachment_id": "a"},
            {"operation": "write_text", "path": "SKILL.md", "content": "x", "unexpected": True},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                SkillEditChangeArgs(**invalid)

        identity = "comic--" + "a" * 16
        common = {"skill_id": identity, "expected_digest": "b" * 64, "operation": "batch",
                  "changes": [write, image]}
        valid = UpdateProducerSkillFileArgs(**common)
        self.assertEqual(len(valid.changes), 2)
        with self.assertRaises(ValidationError):
            UpdateProducerSkillFileArgs(**{**common, "path": "SKILL.md"})
        with self.assertRaises(ValidationError):
            UpdateProducerSkillFileArgs(**{**common, "content": "top-level text"})
        with self.assertRaises(ValidationError):
            UpdateProducerSkillFileArgs(**{**common, "changes": [write] * 21})
        with self.assertRaises(ValidationError):
            UpdateProducerSkillFileArgs(**{**common, "attachment_id": "top-level-attachment"})
        with self.assertRaises(ValidationError):
            from creatoros.tools.studio import ProducerSkillArgs
            ProducerSkillArgs(skill_id=identity, include_versions=True, list_files=True)

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database, self.runs, self.producer, self.app = make_fixture(self.root)
        self.addCleanup(self.database.close)
        self.catalog = self.app.state.skill_installs.catalog
        self.session_id = self.app.state.chat.create("review-lab")["id"]
        self.context = self._context()
        self.source = self.root / "story-skill"
        (self.source / "assets").mkdir(parents=True)
        self.original_text = (
            "---\nname: story-skill\ndescription: test story skill\n"
            "creatoros-output: social-content-pack.image-carousel\n---\n"
            "Keep the reference: [image](assets/reference.png).\n"
        )
        (self.source / "SKILL.md").write_text(self.original_text, encoding="utf-8")
        self.original_api_text = (self.source / "SKILL.md").read_bytes().decode("utf-8")
        self.original_image = self._png("blue")
        (self.source / "assets" / "reference.png").write_bytes(self.original_image)
        self.unused_image = self._png("green")
        (self.source / "assets" / "unused.png").write_bytes(self.unused_image)
        self.original = self.catalog.register_local(self.source, role="legacy_end_to_end")
        from creatoros.storage import Series
        with self.database.session() as session:
            session.get(Series, "agent-notes").skill_name = self.original["id"]

    @staticmethod
    def _png(color):
        from io import BytesIO
        buffer = BytesIO()
        Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
        return buffer.getvalue()

    def _image_attachment(self, color="red", *, session_id=None):
        raw = self._png(color)
        return self.app.state.chat.attachments.put(session_id or self.session_id, "reference.png",
                                                   base64.b64encode(raw).decode())

    def _context(self, studio_url=None):
        return RuntimeContext(project_root=self.root, studio_url=studio_url, creator_id="review-lab",
            agent_session_id=self.session_id, user_request_id="edit-batch-test",
            allowed_tools=ACCOUNT_TOOLS)

    def _execute_edit(self, arguments, *, context=None):
        return execute_tool_call(
            ToolCall("edit-batch", "update_producer_skill_file", json.dumps(arguments)),
            context=context or self.context, model_requested=True,
        )

    def test_batch_applies_text_and_image_changes_sequentially_to_candidate(self):
        with serve(self.app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False):
            attachment = self._image_attachment("red")
            replacement = self._png("red")
            changed_text = self.original_text + "\nKeep each panel concise.\n"
            start_digest = self.catalog.describe(self.original["id"])["digest"]
            result = self._execute_edit({
                "skill_id": self.original["id"], "expected_digest": start_digest, "operation": "batch",
                "changes": [
                    {"operation": "write_text", "path": "SKILL.md", "content": changed_text},
                    {"operation": "replace_image", "path": "assets/reference.png",
                     "attachment_id": attachment["id"]},
                    {"operation": "add_image", "path": "assets/extra.png",
                     "attachment_id": attachment["id"]},
                    {"operation": "delete_image", "path": "assets/unused.png"},
                ],
            }, context=self._context(url))

        self.assertFalse(result.is_error, result.content)
        receipt = json.loads(result.content)
        self.assertEqual(receipt["status"], "completed")
        self.assertTrue(receipt["completed"])
        self.assertFalse(receipt["partial"])
        self.assertEqual(receipt["applied_count"], 4)
        self.assertEqual(receipt["total_count"], 4)
        self.assertFalse(receipt["outcome_unknown"])
        self.assertIsNone(receipt["failed_index"])
        self.assertEqual([item["index"] for item in receipt["applied"]], [1, 2, 3, 4])
        self.assertEqual([item["path"] for item in receipt["applied"]], [
            "SKILL.md", "assets/reference.png", "assets/extra.png", "assets/unused.png"])
        self.assertNotIn(changed_text, result.content)
        self.assertNotIn("content", receipt)
        self.assertNotEqual(receipt["skill_id"], self.original["id"])
        candidate = self.catalog.describe(receipt["skill_id"])
        self.assertEqual(candidate["family_id"], self.catalog.describe(self.original["id"])["family_id"])
        self.assertEqual(candidate["version_status"], "candidate")
        self.assertEqual(candidate["digest"], receipt["digest"])
        self.assertEqual(self.catalog.read_skill_file(receipt["skill_id"], "SKILL.md")["content"], changed_text)
        self.assertEqual(self.catalog.read_skill_file(receipt["skill_id"], "assets/reference.png")["content"], replacement)
        self.assertEqual(self.catalog.read_skill_file(receipt["skill_id"], "assets/extra.png")["content"], replacement)
        self.assertFalse((self.catalog.locate(receipt["skill_id"]) / "assets/unused.png").exists())
        self.assertEqual(self.catalog.read_skill_file(self.original["id"], "SKILL.md")["content"], self.original_api_text)
        self.assertEqual(self.catalog.read_skill_file(self.original["id"], "assets/reference.png")["content"], self.original_image)
        self.assertEqual(self.producer.calls, 0)

    def test_batch_failure_keeps_prior_candidate_and_stops_remaining_changes(self):
        with serve(self.app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False):
            changed_text = self.original_text + "\nThis edit is retained as a candidate.\n"
            result = self._execute_edit({
                "skill_id": self.original["id"],
                "expected_digest": self.catalog.describe(self.original["id"])["digest"],
                "operation": "batch",
                "changes": [
                    {"operation": "write_text", "path": "SKILL.md", "content": changed_text},
                    {"operation": "delete_image", "path": "assets/reference.png"},
                    {"operation": "delete_image", "path": "assets/unused.png"},
                ],
            }, context=self._context(url))

        self.assertTrue(result.is_error)
        receipt = json.loads(result.content)
        self.assertEqual(receipt["status"], "partial")
        self.assertTrue(receipt["partial"])
        self.assertFalse(receipt["completed"])
        self.assertEqual(receipt["applied_count"], 1)
        self.assertEqual(receipt["total_count"], 3)
        self.assertFalse(receipt["outcome_unknown"])
        self.assertEqual(receipt["failed_index"], 2)
        self.assertEqual(receipt["failed"], {"index": 2, "operation": "delete_image", "path": "assets/reference.png"})
        self.assertEqual(receipt["error_type"], "skill_asset_referenced")
        self.assertEqual(self.catalog.read_skill_file(receipt["skill_id"], "SKILL.md")["content"], changed_text)
        self.assertTrue((self.catalog.locate(receipt["skill_id"]) / "assets/unused.png").exists())
        self.assertEqual(self.catalog.read_skill_file(self.original["id"], "SKILL.md")["content"], self.original_api_text)

    def test_batch_cannot_use_an_attachment_from_another_chat(self):
        other_session = self.app.state.chat.create("review-lab")["id"]
        foreign_attachment = self._image_attachment("yellow", session_id=other_session)
        with serve(self.app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False):
            result = self._execute_edit({
                "skill_id": self.original["id"],
                "expected_digest": self.catalog.describe(self.original["id"])["digest"],
                "operation": "batch",
                "changes": [{"operation": "replace_image", "path": "assets/reference.png",
                             "attachment_id": foreign_attachment["id"]}],
            }, context=self._context(url))

        self.assertTrue(result.is_error)
        receipt = json.loads(result.content)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["applied_count"], 0)
        self.assertEqual(receipt["failed_index"], 1)
        self.assertEqual(receipt["error_type"], "not_found")
        self.assertEqual(receipt["skill_id"], self.original["id"])
        self.assertEqual(self.catalog.read_skill_file(self.original["id"], "assets/reference.png")["content"],
                         self.original_image)

    def test_batch_stops_after_response_loss_without_retrying_or_running_later_items(self):
        with serve(self.app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False):
            attachment = self._image_attachment("purple")
            changes = [
                {"operation": "write_text", "path": "SKILL.md",
                 "content": self.original_text + "\nFirst edit saved.\n"},
                {"operation": "add_image", "path": "assets/new.png", "attachment_id": attachment["id"]},
                {"operation": "delete_image", "path": "assets/unused.png"},
            ]
            original_request = httpx.Client.request
            asset_posts = 0

            def lose_asset_response(client, method, url_or_request, *args, **kwargs):
                nonlocal asset_posts
                response = original_request(client, method, url_or_request, *args, **kwargs)
                if method.upper() == "POST" and "/image-actions/skill-asset" in str(url_or_request):
                    asset_posts += 1
                    raise httpx.ReadTimeout("controlled response loss after isolated server commit")
                return response

            with patch.object(httpx.Client, "request", new=lose_asset_response):
                result = self._execute_edit({
                    "skill_id": self.original["id"],
                    "expected_digest": self.catalog.describe(self.original["id"])["digest"],
                    "operation": "batch", "changes": changes,
                }, context=self._context(url))

        self.assertTrue(result.is_error)
        receipt = json.loads(result.content)
        self.assertEqual(asset_posts, 1)
        self.assertEqual(receipt["status"], "outcome_unknown")
        self.assertTrue(receipt["partial"])
        self.assertFalse(receipt["completed"])
        self.assertEqual(receipt["applied_count"], 1)
        self.assertEqual(receipt["failed_index"], 2)
        self.assertTrue(receipt["outcome_unknown"])
        self.assertEqual(receipt["error_type"], "studio_outcome_unknown")
        self.assertTrue((self.catalog.locate(receipt["skill_id"]) / "assets/new.png").is_file())
        self.assertTrue((self.catalog.locate(receipt["skill_id"]) / "assets/unused.png").is_file())

    def test_file_tree_and_known_skill_body_are_one_consistent_read(self):
        with serve(self.app) as url, httpx.Client(base_url=url, timeout=15, trust_env=False) as client:
            context = self._context(url)
            read = get_producer_skill(self.original["id"], path="SKILL.md", list_files=True,
                                      context=context)
            self.assertFalse(read.is_error, read.content)
            data = json.loads(read.content)
            self.assertEqual(data["content"], self.original_api_text)
            self.assertEqual(data["digest"], self.catalog.describe(self.original["id"])["digest"])
            self.assertEqual(data["family_id"], self.catalog.describe(self.original["id"])["family_id"])
            self.assertIn({"path": "SKILL.md", "kind": "markdown", "size": len(self.original_api_text.encode())}, data["files"])

            candidate = client.put(f"/api/producer-skills/{self.original['id']}/files/content", json={
                "path": "SKILL.md", "content": self.original_text + "\nCandidate body.\n",
                "expected_digest": data["digest"],
            })
            self.assertEqual(candidate.status_code, 200, candidate.text)
            identity = candidate.json()["skill_id"]
            current = self.catalog.describe(identity)
            # The candidate itself is editable in place, making a change between
            # the two reads observable without mutating its released parent.
            original_request = httpx.Client.request
            injected = False

            def mutate_between_reads(http_client, method, url_or_request, *args, **kwargs):
                nonlocal injected
                response = original_request(http_client, method, url_or_request, *args, **kwargs)
                if not injected and method.upper() == "GET" and str(url_or_request).endswith("/files"):
                    injected = True
                    with httpx.Client(base_url=url, timeout=15, trust_env=False) as writer:
                        changed = writer.put(f"/api/producer-skills/{identity}/files/content", json={
                            "path": "SKILL.md", "content": self.original_text + "\nConcurrent update.\n",
                            "expected_digest": current["digest"],
                        })
                    self.assertEqual(changed.status_code, 200, changed.text)
                return response

            with patch.object(httpx.Client, "request", new=mutate_between_reads):
                conflict = get_producer_skill(identity, path="SKILL.md", list_files=True,
                                              context=context)
        self.assertTrue(conflict.is_error)
        self.assertEqual(conflict.error_type, "skill_digest_conflict")
        conflict_data = json.loads(conflict.content)
        self.assertNotIn("content", conflict_data)
        self.assertIn("不同版本", conflict_data["message"])


if __name__ == "__main__":
    unittest.main()
