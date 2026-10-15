"""Batch boundaries through real local HTTP; controlled extractor only tests handoff."""
import base64
import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from PIL import Image
from pydantic import ValidationError

from creatoros.integrations.producer_skills import MAX_LOCAL_SKILL_BYTES, ProducerSkillCatalog, SkillInstallService
from creatoros.integrations.skill_draft_files import checked_folder, read_file_drafts
from creatoros.integrations.skill_extraction import ExtractionError, MAX_REFERENCE_IMAGES, SkillExtractionService
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.tools.skill_extraction import ExtractSkillsFromArtifactArgs, extract_skills_from_artifact
from creatoros.web import create_app
from tests.agent_studio_support import serve


def skill_text():
    return "---\nname: batch-reference\ndescription: Reusable batch method\n---\nRead the reference assets for new topics.\n"


class ExtractionBatchTests(unittest.TestCase):
    def test_twenty_http_references_preserve_order_and_save(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = f"sqlite:///{(root / 'test.db').as_posix()}"
            upgrade_database(url)
            db = Database(url)
            catalog = ProducerSkillCatalog(root / "skills")
            calls = []

            async def controlled(directory, images, mode, instruction, cancel, on_thread):
                # This observes the real service snapshot, not model quality.
                calls.append([path.read_bytes() for path in images])
                target = directory / "draft" / "skill"
                (target / "assets").mkdir(parents=True)
                (target / "SKILL.md").write_text(skill_text(), encoding="utf-8")
                for path in images:
                    (target / "assets" / path.name).write_bytes(path.read_bytes())
                return read_file_drafts(directory, mode, "Controlled handoff only")

            service = SkillExtractionService(catalog, controlled)
            app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "output"),
                             chat_root=root / "sessions", skill_install_service=SkillInstallService(catalog),
                             skill_extraction_service=service)
            try:
                with serve(app) as endpoint, httpx.Client(base_url=endpoint, timeout=20, trust_env=False) as client:
                    base = "/api/skill-extractions"
                    images, ids = [], []
                    for index in range(MAX_REFERENCE_IMAGES + 1):
                        stream = io.BytesIO()
                        Image.new("RGB", (8, 8), (index, 40, 180)).save(stream, format="PNG")
                        images.append(stream.getvalue())
                        uploaded = client.post(base + "/uploads", json={"name": f"sample-{index}.png",
                            "data_base64": base64.b64encode(images[-1]).decode()})
                        self.assertEqual(uploaded.status_code, 201, uploaded.text)
                        ids.append(uploaded.json()["id"])
                    self.assertEqual(client.post(base, json={"request_id": "too-many", "upload_ids": ids}).status_code, 422)
                    self.assertEqual(calls, [])
                    with self.assertRaises(ExtractionError):
                        service.submit("direct-too-many", ids)
                    payload = {"request_id": "twenty", "upload_ids": list(reversed(ids[:20]))}
                    response = client.post(base, json=payload)
                    self.assertEqual(response.status_code, 202, response.text)
                    job_id = response.json()["id"]
                    service.worker.join(10)
                    job = client.get(base + "/" + job_id).json()
                    self.assertEqual(job["status"], "ready", job)
                    self.assertEqual(calls, [list(reversed(images[:20]))])
                    self.assertEqual([item["id"] for item in job["uploads"]], payload["upload_ids"])
                    self.assertEqual(len(list((service.root / "jobs" / job_id / "references").glob("*"))), 20)
                    self.assertEqual(client.post(base, json=payload).json()["id"], job_id)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(client.post(base, json={**payload, "upload_ids": ids[:20]}).status_code, 409)
                    before = len(catalog.list())
                    saved = client.post(base + "/" + job_id + "/save", json={"expected_digest": job["digest"]})
                    self.assertEqual(saved.status_code, 200, saved.text)
                    self.assertEqual(len(catalog.list()), before + 1)
                    assets = Path(saved.json()["saved_skills"][0]["local_path"]) / "assets"
                    for index, raw in enumerate(reversed(images[:20]), 1):
                        self.assertEqual((assets / f"reference-{index:02d}.png").read_bytes(), raw)
                    self.assertFalse((service.root / "jobs" / hashlib.sha256(b"too-many").hexdigest()).exists())
            finally:
                db.close()

    def test_agent_schema_uses_same_limit(self):
        args = {"request_id": "boundary", "upload_ids": [str(i) for i in range(20)]}
        self.assertEqual(len(ExtractSkillsFromArtifactArgs(**args).upload_ids), 20)
        with self.assertRaises(ValidationError):
            ExtractSkillsFromArtifactArgs(**{**args, "upload_ids": args["upload_ids"] + ["21"]})
        rejected = extract_skills_from_artifact(upload_ids=args["upload_ids"] + ["21"], request_id="reject")
        self.assertTrue(rejected.is_error)
        self.assertIn("20", rejected.content)

    def test_large_local_draft_registers_and_over_budget_rejects(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "SKILL.md").write_text(skill_text(), encoding="utf-8")
            resource = source / "reference.bin"
            # Actual filesystem bytes test the former 32 MiB failure, no model needed.
            with resource.open("wb") as stream:
                stream.truncate(33 * 1024 * 1024)
            self.assertEqual(checked_folder(source, root), source)
            catalog = ProducerSkillCatalog(root / "catalog")
            registered = catalog.register_local(source, role="legacy_end_to_end")
            self.assertEqual((Path(registered["local_path"]) / resource.name).stat().st_size, 33 * 1024 * 1024)
            with resource.open("wb") as stream:
                stream.truncate(MAX_LOCAL_SKILL_BYTES + 1)
            with self.assertRaisesRegex(ExtractionError, "96 MiB"):
                checked_folder(source, root)
            with self.assertRaisesRegex(ValueError, "96 MiB"):
                catalog.register_local(source, role="legacy_end_to_end")


if __name__ == "__main__":
    unittest.main()
