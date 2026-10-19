"""Authored drafts use real files/loopback HTTP, and never call a model."""
import base64
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from PIL import Image

from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.integrations.skill_extraction import ExtractionError, SkillExtractionService
from creatoros.skills.loader import parse_skill_frontmatter
from creatoros.web.extraction_routes import extraction_routes
from tests.agent_studio_support import serve


def picture(color="blue"):
    stream = io.BytesIO()
    Image.new("RGB", (24, 16), color).save(stream, format="PNG")
    return stream.getvalue()


class SkillAuthoringTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.catalog = ProducerSkillCatalog(self.root / "catalog")
        self.initial_skills = self.catalog.list()
        self.service = SkillExtractionService(self.catalog, extractor=self.forbidden,
                                               reviser=self.forbidden, producer_factory=self.forbidden)
        self.service.start()

    def tearDown(self):
        self.service.shutdown()
        self.temporary.cleanup()

    @staticmethod
    def forbidden(*args, **kwargs):
        raise AssertionError("Authoring must not call Codex or produce images")

    def request(self, **changes):
        return {"request_id": "my-authored-skill", "name": "my-comic", "description": "制作同类新漫画",
                "content": "  这是我调好的提示词。\r\n必须保留这段正文！\n", "display_name": "我的漫画", **changes}

    def upload(self, data=None):
        raw = picture() if data is None else data
        return self.service.upload("reference.png", base64.b64encode(raw).decode())["id"]

    def test_ready_preserves_original_text_and_image(self):
        image_id = self.upload()
        request = self.request(upload_ids=[image_id])
        job = self.service.author(**request)
        self.assertEqual(job["status"], "ready")
        self.assertEqual(job["task_kind"], "author")
        self.assertIsNone(job["thread_id"])
        self.assertIsNone(self.service.worker)
        self.assertIsNone(job["operation"])
        self.assertEqual(self.catalog.list(), self.initial_skills)
        self.assertEqual(self.service.file(job["id"], "legacy_end_to_end", "assets/source.txt").read_bytes(),
                         request["content"].encode("utf-8"))
        self.assertEqual(self.service.file(job["id"], "legacy_end_to_end", "assets/reference-01.png").read_bytes(), picture())
        text = self.service.file(job["id"], "legacy_end_to_end", "SKILL.md").read_text(encoding="utf-8")
        self.assertIn(request["content"].replace("\r\n", "\n"), text)
        self.assertIn("实际读取", text)
        self.assertIn("assets/reference-01.png", text)
        self.assertEqual(parse_skill_frontmatter(text)["creatoros-display-name"], "我的漫画")
        self.assertIn("creatoros-output: social-content-pack.image-carousel", text)
        self.assertEqual({f["path"] for f in job["files"]}, {"SKILL.md", "assets/source.txt", "assets/reference-01.png"})

    def test_same_input_idempotent_changed_input_conflicts(self):
        request = self.request()
        original = self.service.author(**request)
        repeated = self.service.author(**request)
        self.assertEqual(original["id"], repeated["id"])
        self.assertEqual(original["digest"], repeated["digest"])
        with self.assertRaises(ExtractionError) as caught:
            self.service.author(**{**request, "content": "修改过的提示词"})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(len(self.service.list()), 1)

    def test_all_roles_retain_assets_and_source(self):
        identifier = self.upload()
        for role, mode in (("mind", "mind"), ("production", "visual"), ("legacy_end_to_end", "single")):
            with self.subTest(role=role):
                job = self.service.author(**self.request(request_id=role, role=role, upload_ids=[identifier]))
                self.assertEqual(job["mode"], mode)
                self.assertEqual(job["skills"][0]["role"], role)
                self.assertEqual(self.service.file(job["id"], role, "assets/reference-01.png").read_bytes(), picture())
                self.assertEqual(self.service.file(job["id"], role, "assets/source.txt").read_bytes(),
                                 self.request()["content"].encode())

    def test_complete_skill_preserves_metadata_and_body(self):
        content = ('---\nname: my-comic\ndescription: 制作同类新漫画\ncustom: retained\n'
                   'creatoros-output: text\n---\n\n# 我已写好的 Skill\n正文不会被提炼或重写。\n')
        job = self.service.author(**self.request(content=content))
        text = self.service.file(job["id"], "legacy_end_to_end", "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(parse_skill_frontmatter(text)["custom"], "retained")
        self.assertEqual(parse_skill_frontmatter(text)["creatoros-output"], "text")
        self.assertEqual(text.split("---", 2)[2], content.split("---", 2)[2])
        self.assertEqual(job["skills"][0]["output_kind"], "text")
        self.assertEqual(self.service.file(job["id"], "legacy_end_to_end", "assets/source.txt").read_bytes(), content.encode())

    def test_full_skill_conflicting_name_or_description_rejected(self):
        for header in ("name: another-name\ndescription: 制作同类新漫画", "name: my-comic\ndescription: 另一个说明"):
            with self.subTest(header=header), self.assertRaises(ExtractionError):
                self.service.author(**self.request(content="---\n" + header + "\n---\n正确正文。"))
        self.assertEqual(self.service.list(), [])

    def test_invalid_image_or_modified_upload_rejected(self):
        with self.assertRaises(ExtractionError):
            self.upload(b"not-an-image")
        identifier = self.upload()
        self.service.image_path(identifier).write_bytes(picture("red"))
        with self.assertRaises(ExtractionError):
            self.service.author(**self.request(upload_ids=[identifier]))
        self.assertEqual(self.service.list(), [])

    def test_save_is_explicit_and_uses_creatoros_catalog(self):
        job = self.service.author(**self.request(upload_ids=[self.upload()]))
        self.assertEqual(self.catalog.list(), self.initial_skills)
        saved = self.service.save(job["id"], job["digest"])
        self.assertEqual(saved["status"], "saved")
        self.assertEqual(len(self.catalog.list()), len(self.initial_skills) + 1)
        registered = saved["saved_skills"][0]
        destination = Path(registered["local_path"])
        self.assertTrue(destination.resolve().is_relative_to(self.catalog.root.resolve()))
        self.assertEqual((destination / "assets/reference-01.png").read_bytes(), picture())
        self.assertEqual((destination / "assets/source.txt").read_bytes(), self.request()["content"].encode())
        self.assertEqual(self.service.save(job["id"], job["digest"])["saved_skills"], saved["saved_skills"])

    def test_edit_and_save_existing_workbench_contract(self):
        job = self.service.author(**self.request(upload_ids=[self.upload()]))
        skill = {**job["skills"][0], "skill_md": job["skills"][0]["skill_md"] + "\n用户的新要求。\n"}
        edited = self.service.edit(job["id"], job["digest"], [skill])
        self.assertEqual(edited["revision"], 2)
        self.assertIn("用户的新要求", edited["skills"][0]["skill_md"])
        self.assertEqual(self.service.file(job["id"], skill["role"], "assets/reference-01.png").read_bytes(), picture())
        self.assertEqual(self.service.save(edited["id"], edited["digest"])["status"], "saved")

    def test_closed_service_rejects_even_duplicate_input(self):
        request = self.request()
        self.service.author(**request)
        self.service.shutdown()
        with self.assertRaises(ExtractionError) as caught:
            self.service.author(**request)
        self.assertEqual(caught.exception.status, 409)

    def test_ready_draft_survives_restart_without_a_worker(self):
        job = self.service.author(**self.request(upload_ids=[self.upload()]))
        self.service.shutdown()
        self.service = SkillExtractionService(self.catalog, extractor=self.forbidden)
        self.service.start()
        reloaded = self.service.get(job["id"])
        self.assertEqual(reloaded["status"], "ready")
        self.assertEqual(reloaded["digest"], job["digest"])
        self.assertIsNone(reloaded["thread_id"])
        self.assertIsNone(self.service.worker)
        self.assertEqual(self.catalog.list(), self.initial_skills)

    def test_duplicate_images_deduplicate_but_changed_order_conflicts(self):
        first, second = self.upload(), self.upload(picture("red"))
        request = self.request(upload_ids=[first, second, first])
        job = self.service.author(**request)
        self.assertEqual(job["upload_ids"], [first, second])
        self.assertEqual(self.service.author(**{**request, "upload_ids": [first, second]})["digest"], job["digest"])
        with self.assertRaises(ExtractionError) as caught:
            self.service.author(**{**request, "upload_ids": [second, first]})
        self.assertEqual(caught.exception.status, 409)

    def test_complete_skill_crlf_preserved_and_name_not_overwritten(self):
        content = "---\r\nname: my-comic\r\ndescription: 制作同类新漫画\r\n---\r\n原始正文。\r\n"
        job = self.service.author(**self.request(content=content, display_name=None))
        raw = self.service.file(job["id"], "legacy_end_to_end", "SKILL.md").read_bytes()
        self.assertTrue(raw.endswith(content.split("---", 2)[2].encode()))
        self.assertIn(b"name: my-comic\r\n", raw)

    def test_copy_failure_retains_failed_record(self):
        request = self.request(upload_ids=[self.upload()])
        with patch("creatoros.integrations.skill_authoring.shutil.copyfile", side_effect=PermissionError("injected IO fault")):
            with self.assertRaises(ExtractionError):
                self.service.author(**request)
        job = self.service.list()[0]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["skills"], [])
        self.assertEqual(self.catalog.list(), self.initial_skills)
        retry = self.service.author(**request)
        self.assertEqual(retry["status"], "failed")

    def test_missing_or_oversize_inputs_no_job(self):
        cases = [{"content": " "}, {"name": "中文"}, {"name": "a" * 65}, {"description": ""},
                 {"description": "a" * 1001}, {"content": "a" * 20001}, {"role": "pair"},
                 {"display_name": "hello\nworld"}, {"upload_ids": ["a" * 64] * 21}]
        for changes in cases:
            with self.subTest(changes=list(changes)), self.assertRaises(ExtractionError):
                self.service.author(**self.request(**changes))
        self.assertEqual(self.service.list(), [])

    def test_true_loopback_upload_author_files_edit_save_and_conflict(self):
        app = FastAPI()
        app.include_router(extraction_routes(self.service))
        with serve(app) as endpoint, httpx.Client(base_url=endpoint, timeout=20, trust_env=False) as client:
            base = "/api/skill-extractions"
            uploaded = client.post(base + "/uploads", json={"name": "ref.png", "data_base64": base64.b64encode(picture()).decode()})
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            request = self.request(upload_ids=[uploaded.json()["id"]])
            response = client.post(base + "/author", json=request)
            self.assertEqual(response.status_code, 201, response.text)
            job = response.json()
            self.assertEqual(job["status"], "ready")
            self.assertEqual(client.get(base + "/" + job["id"]).json()["digest"], job["digest"])
            self.assertEqual(client.post(base + "/author", json=request).json()["id"], job["id"])
            self.assertEqual(client.post(base + "/author", json={**request, "content": "改动内容"}).status_code, 409)
            self.assertEqual(client.post(base + "/author", json={**request, "role": "pair"}).status_code, 422)
            original_file = next(file for file in job["files"] if file["path"] == "assets/source.txt")
            self.assertEqual(client.get(original_file["url"]).content, request["content"].encode())
            image_file = next(file for file in job["files"] if file["kind"] == "image")
            self.assertEqual(client.get(image_file["url"]).content, picture())
            skill = {**job["skills"][0], "skill_md": job["skills"][0]["skill_md"] + "\n手动修改。\n"}
            edited = client.post(base + "/" + job["id"] + "/draft", json={"expected_digest": job["digest"], "skills": [skill]})
            self.assertEqual(edited.status_code, 200, edited.text)
            saved = client.post(base + "/" + job["id"] + "/save", json={"expected_digest": edited.json()["digest"]})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["status"], "saved")
            self.assertEqual(len(self.catalog.list()), len(self.initial_skills) + 1)


if __name__ == "__main__":
    unittest.main()
