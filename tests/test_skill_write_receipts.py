"""Real HTTP and isolated Skill files distinguish rejected and uncertain writes."""
from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
from PIL import Image

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.integrations import producer_skills
from creatoros.storage import Series
from creatoros.tools.execution import execute_tool_call
from creatoros.web.chat import ACCOUNT_TOOLS
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class SkillWriteReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db, _runs, _producer, self.app = make_fixture(self.root)
        self.addCleanup(self.db.close)
        self.catalog = self.app.state.skill_installs.catalog
        source = self.root / "source-skill"
        source.mkdir()
        self.original_text = (
            "---\nname: receipt-test\ndescription: test\n"
            "creatoros-output: social-content-pack.image-carousel\n---\nOriginal text.\n"
        )
        (source / "SKILL.md").write_text(self.original_text, encoding="utf-8")
        (source / "notes.txt").write_text("original note", encoding="utf-8")
        (source / "assets").mkdir()
        (source / "assets" / "old.png").write_bytes(self.png("blue"))
        self.original = self.catalog.register_local(source, role="production")
        self.candidate = self.catalog.create_skill_candidate(
            self.original["id"], self.original["digest"], "receipt-test")
        self.working = Path(self.candidate["local_path"])
        with self.db.session() as session:
            session.get(Series, "agent-notes").skill_name = self.original["id"]
        self.session_id = self.app.state.chat.create("review-lab")["id"]
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    @staticmethod
    def png(color):
        buffer = BytesIO()
        Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
        return buffer.getvalue()

    def inspect_after(self, predicate):
        inspect = producer_skills._inspect_checkout
        fired = [False]

        def change_after_write(directory, requested_path):
            receipt = inspect(directory, requested_path)
            if not fired[0] and directory == self.working and predicate(directory):
                fired[0] = True
                (directory / "notes.txt").write_text("changed elsewhere", encoding="utf-8")
            return receipt

        return fired, patch("creatoros.integrations.producer_skills._inspect_checkout", side_effect=change_after_write)

    def test_text_write_reports_unknown_if_receipt_read_changes_after_commit(self):
        inspect = producer_skills._inspect_checkout
        fired = False

        def change_after_write(directory, requested_path):
            nonlocal fired
            receipt = inspect(directory, requested_path)
            if (not fired and directory == self.working
                    and "Saved text." in (directory / "SKILL.md").read_text(encoding="utf-8")):
                fired = True
                (directory / "notes.txt").write_text("changed elsewhere", encoding="utf-8")
            return receipt

        with patch("creatoros.integrations.producer_skills._inspect_checkout", side_effect=change_after_write):
            response = self.client.put(
                f"/api/producer-skills/{self.candidate['id']}/files/content",
                json={"path": "SKILL.md", "expected_digest": self.candidate["digest"],
                      "content": self.original_text.replace("Original text.", "Saved text.")})

        self.assertTrue(fired)
        self.assertEqual(response.status_code, 409, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], "studio_outcome_unknown")
        self.assertTrue(error["outcome_unknown"])
        self.assertEqual(error["skill_id"], self.candidate["id"])
        self.assertIn("不要", error["message"])
        self.assertIn("Saved text.", (self.working / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("Original text.", self.catalog.read_skill_file(self.original["id"], "SKILL.md")["content"])

    def test_change_before_text_write_remains_a_definite_rejection(self):
        fired, fault = self.inspect_after(lambda _directory: True)
        with fault:
            response = self.client.put(
                f"/api/producer-skills/{self.candidate['id']}/files/content",
                json={"path": "SKILL.md", "expected_digest": self.candidate["digest"],
                      "content": self.original_text.replace("Original text.", "Should not be saved.")})
        self.assertTrue(fired[0])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertNotEqual(response.json()["error"]["code"], "studio_outcome_unknown")
        self.assertNotIn("Should not be saved.", (self.working / "SKILL.md").read_text(encoding="utf-8"))

    def test_asset_add_reports_unknown_if_receipt_read_changes_after_commit(self):
        target = self.working / "assets" / "new.png"
        fired, fault = self.inspect_after(lambda _directory: target.exists())
        with fault:
            response = self.client.put(
                f"/api/producer-skills/{self.candidate['id']}/assets",
                json={"path": "assets/new.png", "expected_digest": self.candidate["digest"],
                      "data_base64": base64.b64encode(self.png("red")).decode("ascii")})
        self.assertTrue(fired[0])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "studio_outcome_unknown")
        self.assertEqual(response.json()["error"]["skill_id"], self.candidate["id"])
        self.assertTrue(target.is_file())

    def test_asset_delete_reports_unknown_if_receipt_read_changes_after_commit(self):
        target = self.working / "assets" / "old.png"
        fired, fault = self.inspect_after(lambda _directory: not target.exists())
        with fault:
            response = self.client.request(
                "DELETE", f"/api/producer-skills/{self.candidate['id']}/assets",
                json={"path": "assets/old.png", "expected_digest": self.candidate["digest"]})
        self.assertTrue(fired[0])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "studio_outcome_unknown")
        self.assertFalse(target.exists())

    def test_candidate_creation_reports_unknown_after_registry_commit(self):
        inspect = producer_skills._inspect_checkout
        created = []

        def change_after_create(directory, requested_path):
            receipt = inspect(directory, requested_path)
            record = self.catalog.root / "registry" / f"{directory.name}.json"
            if (not created and directory.parent.name == "working" and record.exists()
                    and directory.name not in {self.original["id"], self.candidate["id"]}):
                created.append(directory.name)
                (directory / "notes.txt").write_text("changed elsewhere", encoding="utf-8")
            return receipt

        with patch("creatoros.integrations.producer_skills._inspect_checkout", side_effect=change_after_create):
            response = self.client.post(f"/api/producer-skills/{self.original['id']}/versions",
                json={"request_id": "another-candidate", "expected_digest": self.original["digest"]})
        self.assertEqual(len(created), 1)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "studio_outcome_unknown")
        self.assertEqual(response.json()["error"]["skill_id"], created[0])
        self.assertEqual(self.catalog._record(created[0])["version_status"], "candidate")

    def test_publish_reports_unknown_after_registry_commit(self):
        inspect = producer_skills._inspect_checkout
        fired = [False]

        def change_after_publish(directory, requested_path):
            receipt = inspect(directory, requested_path)
            if (not fired[0] and directory == self.working
                    and self.catalog._record(self.candidate["id"])["version_status"] == "released"):
                fired[0] = True
                (directory / "notes.txt").write_text("changed elsewhere", encoding="utf-8")
            return receipt

        with patch("creatoros.integrations.producer_skills._inspect_checkout", side_effect=change_after_publish):
            response = self.client.post(f"/api/producer-skills/{self.candidate['id']}/publish-version",
                json={"request_id": "publish-version", "expected_digest": self.candidate["digest"]})
        self.assertTrue(fired[0])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "studio_outcome_unknown")
        self.assertEqual(response.json()["error"]["skill_id"], self.candidate["id"])
        self.assertEqual(self.catalog._record(self.candidate["id"])["version_status"], "released")
        self.assertTrue(self.catalog.original(self.candidate["id"], self.candidate["digest"]).is_dir())

    def test_agent_batch_keeps_new_candidate_identity_and_stops_after_unknown_write(self):
        inspect = producer_skills._inspect_checkout
        changed_ids = []

        def change_after_write(directory, requested_path):
            receipt = inspect(directory, requested_path)
            if (not changed_ids and directory.parent.name == "working"
                    and directory.name not in {self.original["id"], self.candidate["id"]}
                    and "Saved text." in (directory / "SKILL.md").read_text(encoding="utf-8")):
                changed_ids.append(directory.name)
                (directory / "notes.txt").write_text("changed elsewhere", encoding="utf-8")
            return receipt

        arguments = {"skill_id": self.original["id"], "expected_digest": self.original["digest"],
                     "operation": "batch", "changes": [
                         {"operation": "write_text", "path": "SKILL.md",
                          "content": self.original_text.replace("Original text.", "Saved text.")},
                         {"operation": "delete_image", "path": "assets/old.png"},
                     ]}
        with serve(self.app) as url:
            context = RuntimeContext(project_root=self.root, studio_url=url, creator_id="review-lab",
                agent_session_id=self.session_id, user_request_id="unknown-batch", allowed_tools=ACCOUNT_TOOLS)
            with patch("creatoros.integrations.producer_skills._inspect_checkout", side_effect=change_after_write):
                result = execute_tool_call(ToolCall("edit", "update_producer_skill_file", json.dumps(arguments)),
                                           context=context, model_requested=True)
        self.assertTrue(result.is_error, result.content)
        self.assertEqual(result.error_type, "studio_outcome_unknown")
        self.assertEqual(len(changed_ids), 1)
        receipt = json.loads(result.content)
        self.assertTrue(receipt["outcome_unknown"])
        self.assertEqual(receipt["skill_id"], changed_ids[0])
        self.assertIsNone(receipt["digest"])
        self.assertTrue((self.catalog.root / "working" / changed_ids[0] / "assets" / "old.png").exists())
        self.assertIn(changed_ids[0], result.to_model_content())

    def test_agent_treats_write_after_502_as_unknown_without_retry(self):
        requests = []
        request = httpx.Client.request

        def gateway_error(client, method, url, *args, **kwargs):
            response = request(client, method, url, *args, **kwargs)
            if method.upper() == "PUT" and "/files/content" in str(url):
                requests.append(str(url))
                return httpx.Response(502, json={"error": {"code": "upstream_failure", "message": "gateway"}},
                                      request=response.request)
            return response

        arguments = {"skill_id": self.candidate["id"], "expected_digest": self.candidate["digest"],
                     "path": "SKILL.md", "content": self.original_text.replace("Original text.", "Saved text.")}
        with serve(self.app) as url:
            context = RuntimeContext(project_root=self.root, studio_url=url, creator_id="review-lab",
                agent_session_id=self.session_id, user_request_id="unknown-single", allowed_tools=ACCOUNT_TOOLS)
            with patch.object(httpx.Client, "request", new=gateway_error):
                result = execute_tool_call(ToolCall("edit", "update_producer_skill_file", json.dumps(arguments)),
                                           context=context, model_requested=True)
        self.assertEqual(len(requests), 1)
        self.assertTrue(result.is_error, result.content)
        self.assertEqual(result.error_type, "studio_outcome_unknown")
        self.assertIn("不要自动重试", result.to_model_content())
        self.assertIn("Saved text.", (self.working / "SKILL.md").read_text(encoding="utf-8"))

    def test_agent_single_text_write_with_empty_success_receipt_is_unknown(self):
        request = httpx.Client.request
        writes = []
        def empty_receipt(client, method, url, *args, **kwargs):
            response = request(client, method, url, *args, **kwargs)
            if method.upper() == "PUT" and "/files/content" in str(url):
                writes.append(str(url))
                return httpx.Response(200, json={}, request=response.request)
            return response
        arguments = {"skill_id": self.candidate["id"], "expected_digest": self.candidate["digest"],
                     "path": "SKILL.md", "content": self.original_text.replace("Original text.", "Saved text.")}
        with serve(self.app) as url:
            context = RuntimeContext(project_root=self.root, studio_url=url, creator_id="review-lab",
                agent_session_id=self.session_id, user_request_id="empty-receipt", allowed_tools=ACCOUNT_TOOLS)
            with patch.object(httpx.Client, "request", new=empty_receipt):
                result = execute_tool_call(ToolCall("edit", "update_producer_skill_file", json.dumps(arguments)),
                                           context=context, model_requested=True)
        self.assertEqual(len(writes), 1)
        self.assertTrue(result.is_error, result.content)
        self.assertEqual(result.error_type, "studio_outcome_unknown")
        self.assertIn("Saved text.", (self.working / "SKILL.md").read_text(encoding="utf-8"))

    def test_agent_image_write_with_invalid_success_receipt_is_unknown(self):
        raw = self.png("red")
        attachment = self.app.state.chat.attachments.put(self.session_id, "new.png", base64.b64encode(raw).decode())
        request = httpx.Client.request
        writes = []
        def invalid_receipt(client, method, url, *args, **kwargs):
            response = request(client, method, url, *args, **kwargs)
            if method.upper() == "POST" and "/image-actions/skill-asset" in str(url):
                writes.append(str(url))
                return httpx.Response(200, json={"skill_id": "not-a-real-id", "family_id": "bad",
                    "digest": "bad", "path": "assets/new.png"}, request=response.request)
            return response
        arguments = {"skill_id": self.candidate["id"], "expected_digest": self.candidate["digest"],
                     "operation": "add_image", "path": "assets/new.png", "attachment_id": attachment["id"]}
        with serve(self.app) as url:
            context = RuntimeContext(project_root=self.root, studio_url=url, creator_id="review-lab",
                agent_session_id=self.session_id, user_request_id="bad-image-receipt", allowed_tools=ACCOUNT_TOOLS)
            with patch.object(httpx.Client, "request", new=invalid_receipt):
                result = execute_tool_call(ToolCall("edit", "update_producer_skill_file", json.dumps(arguments)),
                                           context=context, model_requested=True)
        self.assertEqual(len(writes), 1)
        self.assertTrue(result.is_error, result.content)
        self.assertEqual(result.error_type, "studio_outcome_unknown")
        self.assertEqual((self.working / "assets" / "new.png").read_bytes(), raw)

    def test_agent_single_text_write_with_broken_json_receipt_is_unknown(self):
        request = httpx.Client.request
        writes = []
        def broken_receipt(client, method, url, *args, **kwargs):
            response = request(client, method, url, *args, **kwargs)
            if method.upper() == "PUT" and "/files/content" in str(url):
                writes.append(str(url))
                return httpx.Response(200, content=b'{', request=response.request)
            return response
        arguments = {"skill_id": self.candidate["id"], "expected_digest": self.candidate["digest"],
                     "path": "SKILL.md", "content": self.original_text.replace("Original text.", "Saved text.")}
        with serve(self.app) as url:
            context = RuntimeContext(project_root=self.root, studio_url=url, creator_id="review-lab",
                agent_session_id=self.session_id, user_request_id="broken-receipt", allowed_tools=ACCOUNT_TOOLS)
            with patch.object(httpx.Client, "request", new=broken_receipt):
                result = execute_tool_call(ToolCall("edit", "update_producer_skill_file", json.dumps(arguments)),
                                           context=context, model_requested=True)
        self.assertEqual(len(writes), 1)
        self.assertTrue(result.is_error, result.content)
        self.assertEqual(result.error_type, "studio_outcome_unknown")
        self.assertIn("Saved text.", (self.working / "SKILL.md").read_text(encoding="utf-8"))

    def test_agent_single_text_write_with_unclassified_409_is_unknown(self):
        request = httpx.Client.request
        writes = []
        def unclassified_conflict(client, method, url, *args, **kwargs):
            response = request(client, method, url, *args, **kwargs)
            if method.upper() == "PUT" and "/files/content" in str(url):
                writes.append(str(url))
                return httpx.Response(409, json={}, request=response.request)
            return response
        arguments = {"skill_id": self.candidate["id"], "expected_digest": self.candidate["digest"],
                     "path": "SKILL.md", "content": self.original_text.replace("Original text.", "Saved text.")}
        with serve(self.app) as url:
            context = RuntimeContext(project_root=self.root, studio_url=url, creator_id="review-lab",
                agent_session_id=self.session_id, user_request_id="unknown-409", allowed_tools=ACCOUNT_TOOLS)
            with patch.object(httpx.Client, "request", new=unclassified_conflict):
                result = execute_tool_call(ToolCall("edit", "update_producer_skill_file", json.dumps(arguments)),
                                           context=context, model_requested=True)
        self.assertEqual(len(writes), 1)
        self.assertTrue(result.is_error, result.content)
        self.assertEqual(result.error_type, "studio_outcome_unknown")
        self.assertIn("Saved text.", (self.working / "SKILL.md").read_text(encoding="utf-8"))

    def test_agent_batch_stops_when_write_receipt_is_unclassified_409(self):
        request = httpx.Client.request
        writes = []
        def unclassified_conflict(client, method, url, *args, **kwargs):
            response = request(client, method, url, *args, **kwargs)
            if method.upper() == "PUT" and "/files/content" in str(url):
                writes.append(response.json()["skill_id"])
                return httpx.Response(409, json={}, request=response.request)
            return response
        arguments = {"skill_id": self.original["id"], "expected_digest": self.original["digest"],
                     "operation": "batch", "changes": [
                         {"operation": "write_text", "path": "SKILL.md",
                          "content": self.original_text.replace("Original text.", "Saved text.")},
                         {"operation": "delete_image", "path": "assets/old.png"},
                     ]}
        with serve(self.app) as url:
            context = RuntimeContext(project_root=self.root, studio_url=url, creator_id="review-lab",
                agent_session_id=self.session_id, user_request_id="unknown-409-batch", allowed_tools=ACCOUNT_TOOLS)
            with patch.object(httpx.Client, "request", new=unclassified_conflict):
                result = execute_tool_call(ToolCall("edit", "update_producer_skill_file", json.dumps(arguments)),
                                           context=context, model_requested=True)
        self.assertEqual(len(writes), 1)
        self.assertTrue(result.is_error, result.content)
        self.assertEqual(result.error_type, "studio_outcome_unknown")
        receipt = json.loads(result.content)
        self.assertEqual(receipt["status"], "outcome_unknown")
        self.assertIsNone(receipt["digest"])
        candidate_directory = self.catalog.root / "working" / writes[0]
        self.assertIn("Saved text.", (candidate_directory / "SKILL.md").read_text(encoding="utf-8"))
        self.assertTrue((candidate_directory / "assets" / "old.png").exists())

    def test_agent_batch_does_not_offer_stale_digest_when_success_receipt_is_incomplete(self):
        request = httpx.Client.request
        writes = []

        def incomplete_receipt(client, method, url, *args, **kwargs):
            response = request(client, method, url, *args, **kwargs)
            if method.upper() == "PUT" and "/files/content" in str(url):
                writes.append(str(url))
                return httpx.Response(200, json={"skill_id": response.json()["skill_id"]}, request=response.request)
            return response

        arguments = {"skill_id": self.original["id"], "expected_digest": self.original["digest"],
                     "operation": "batch", "changes": [
                         {"operation": "write_text", "path": "SKILL.md",
                          "content": self.original_text.replace("Original text.", "Saved text.")},
                         {"operation": "delete_image", "path": "assets/old.png"},
                     ]}
        with serve(self.app) as url:
            context = RuntimeContext(project_root=self.root, studio_url=url, creator_id="review-lab",
                agent_session_id=self.session_id, user_request_id="bad-receipt", allowed_tools=ACCOUNT_TOOLS)
            with patch.object(httpx.Client, "request", new=incomplete_receipt):
                result = execute_tool_call(ToolCall("edit", "update_producer_skill_file", json.dumps(arguments)),
                                           context=context, model_requested=True)
        self.assertEqual(len(writes), 1)
        self.assertTrue(result.is_error)
        receipt = json.loads(result.content)
        self.assertEqual(receipt["status"], "outcome_unknown")
        self.assertIsNone(receipt["digest"])
        self.assertIn("Skill 版本列表", receipt["message"])
        self.assertTrue((self.catalog.root / "working" / self.original["id"] / "assets" / "old.png").exists())


if __name__ == "__main__":
    unittest.main()
