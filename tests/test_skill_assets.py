"""Isolated real-file and loopback checks for Skill assets and removal."""
import base64
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from fastapi import FastAPI
from PIL import Image

from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService
from creatoros.storage import Base, Database, Series
from creatoros.web.skill_routes import skill_routes
from tests.agent_studio_support import serve


def picture(color="blue", kind="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (24, 16), color).save(stream, format=kind)
    return stream.getvalue()


class SkillAssetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db = Database(f"sqlite:///{(self.root / 'test.db').as_posix()}")
        Base.metadata.create_all(self.db.engine)
        self.catalog = ProducerSkillCatalog(self.root / "catalog")
        source = self.root / "source"
        (source / "assets").mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: asset-test\ndescription: asset test\n---\nSee assets/original.png\n",
            encoding="utf-8")
        (source / "assets" / "original.png").write_bytes(picture())
        self.skill = self.catalog.register_local(source, role="mind")
        self.skill_id = self.skill["id"]
        self.released_id = self.skill_id
        self.working = Path(self.skill["local_path"])
        self.released_working = self.working
        self.original = self.catalog.root / "versions" / self.skill_id
        app = FastAPI()
        app.include_router(skill_routes(self.db, SkillInstallService(self.catalog)))
        self.server = serve(app)
        self.endpoint = self.server.__enter__()
        self.client = httpx.Client(base_url=self.endpoint, timeout=10, trust_env=False)

    def tearDown(self):
        self.client.close()
        self.server.__exit__(None, None, None)
        self.db.close()
        self.temporary.cleanup()

    def files(self):
        response = self.client.get(f"/api/producer-skills/{self.skill_id}/files")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def add(self, path="assets/new.png", data=None, digest=None, overwrite=False):
        return self.client.put(f"/api/producer-skills/{self.skill_id}/assets", json={
            "path": path, "data_base64": base64.b64encode(picture("red") if data is None else data).decode(),
            "expected_digest": digest or self.files()["digest"], "overwrite": overwrite,
        })

    def delete(self, path, digest=None):
        return self.client.request("DELETE", f"/api/producer-skills/{self.skill_id}/assets", json={
            "path": path, "expected_digest": digest or self.files()["digest"],
        })

    def follow_candidate(self, result):
        self.skill_id = result["skill_id"]
        self.working = self.catalog.locate(self.skill_id)

    def edit_candidate(self):
        candidate = self.catalog.create_skill_candidate(self.skill_id, self.files()["digest"], "asset-test-candidate")
        self.follow_candidate({"skill_id": candidate["id"]})

    def test_add_replace_delete_preserves_original_and_reads_actual_bytes(self):
        original_bytes = (self.original / "assets" / "original.png").read_bytes()
        first = self.add()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["operation"], "add")
        self.assertNotEqual(first.json()["skill_id"], self.released_id)
        self.follow_candidate(first.json())
        image = self.client.get(f"/api/producer-skills/{self.skill_id}/files/content",
                                params={"path": "assets/new.png"})
        self.assertEqual(image.status_code, 200, image.text)
        self.assertEqual(image.headers["content-type"], "image/png")
        self.assertEqual(image.content, picture("red"))
        second = self.add(data=picture("green"), digest=first.json()["digest"], overwrite=True)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["operation"], "replace")
        self.assertEqual((self.working / "assets/new.png").read_bytes(), picture("green"))
        removed = self.delete("assets/new.png", digest=second.json()["digest"])
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["operation"], "delete")
        self.assertFalse((self.working / "assets/new.png").exists())
        self.assertEqual((self.original / "assets/original.png").read_bytes(), original_bytes)
        self.assertFalse((self.released_working / "assets/new.png").exists())
        self.assertGreaterEqual(len(list((self.catalog.root / "trash/asset-backups").rglob("new.png"))), 2)

    def test_conflicts_invalid_images_path_and_reference_guard_are_zero_write(self):
        initial = self.files()["digest"]
        self.assertEqual(self.add(path="assets/original.png").status_code, 409)
        self.assertEqual(self.add(path="assets/missing.png", overwrite=True).status_code, 409)
        self.assertEqual(self.add(path="../escape.png").status_code, 422)
        self.assertEqual(self.add(path="assets/.hidden.png").status_code, 422)
        self.assertEqual(self.add(data=b"not-an-image").status_code, 422)
        self.assertEqual(self.add(data=picture(kind="JPEG")).status_code, 422)
        self.assertEqual(self.add(data=b"x" * (4 * 1024 * 1024 + 1)).status_code, 413)
        referenced = self.delete("assets/original.png")
        self.assertEqual(referenced.status_code, 409, referenced.text)
        self.assertEqual(referenced.json()["error"]["code"], "skill_asset_referenced")
        self.assertEqual(referenced.json()["error"]["referencing_files"], ["SKILL.md"])
        self.assertEqual(self.files()["digest"], initial)
        added = self.add()
        self.assertEqual(added.status_code, 200, added.text)
        self.follow_candidate(added.json())
        stale = self.delete("assets/new.png", initial)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "skill_digest_conflict")
        self.assertEqual((self.working / "assets/new.png").read_bytes(), picture("red"))

    def test_builtin_read_only_and_link_rejected(self):
        built_in = self.client.put("/api/producer-skills/knowledge-to-carousel/assets", json={
            "path": "assets/new.png", "data_base64": base64.b64encode(picture()).decode(),
            "expected_digest": "0" * 64,
        })
        self.assertEqual(built_in.status_code, 403)
        self.assertEqual(self.client.request("DELETE", "/api/producer-skills/knowledge-to-carousel",
                                             json={"expected_digest": "0" * 64}).status_code, 403)

    def test_delete_old_invalid_large_image_and_total_budget(self):
        old = self.working / "assets/old.png"
        old.write_bytes(b"old invalid image" * 350_000)
        deleted = self.delete("assets/old.png")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.follow_candidate(deleted.json())
        self.assertTrue(old.exists(), "old released version retains even the invalid historical asset")
        self.assertFalse((self.working / "assets/old.png").exists())
        filler = self.working / "large.txt"
        with filler.open("wb") as stream:
            stream.truncate(96 * 1024 * 1024)
        before = self.files()["digest"]
        rejected = self.add(digest=before)
        self.assertEqual(rejected.status_code, 413, rejected.text)
        self.assertFalse((self.working / "assets/new.png").exists())
        self.assertEqual(self.files()["digest"], before)

    def test_linked_asset_or_backup_parent_rejected(self):
        self.edit_candidate()
        initial = self.files()["digest"]
        outside = self.root / "outside.png"
        outside.write_bytes(picture())
        linked = self.working / "assets/link.png"
        try:
            linked.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation unavailable on this host")
        denied = self.add(path="assets/link.png", digest=initial, overwrite=True)
        self.assertEqual(denied.status_code, 422, denied.text)
        self.assertEqual(outside.read_bytes(), picture())
        linked.unlink()
        backup_skill = self.catalog.root / "trash/asset-backups" / self.skill_id
        backup_skill.parent.mkdir(parents=True)
        backup_skill.symlink_to(self.root, target_is_directory=True)
        blocked = self.add(path="assets/original.png", overwrite=True)
        self.assertEqual(blocked.status_code, 422, blocked.text)
        self.assertEqual((self.working / "assets/original.png").read_bytes(), picture())

    def test_remove_checks_active_bindings_and_keeps_recovery_files(self):
        with self.db.session() as session:
            session.add(Series(id="legacy", name="Legacy", description="", audience="",
                               skill_name=self.skill_id))
            session.add(Series(id="paired", name="Paired", description="", audience="",
                               skill_name=None, mind_skill_id=self.skill_id,
                               production_skill_id=self.skill_id))
        url = f"/api/producer-skills/{self.skill_id}"
        body = {"expected_digest": self.files()["digest"]}
        blocked = self.client.request("DELETE", url, json=body)
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["error"]["code"], "skill_in_use")
        self.assertEqual({item["id"] for item in blocked.json()["error"]["series"]}, {"legacy", "paired"})
        self.assertEqual(self.client.request("DELETE", url, json=body,
                                             headers={"x-creatoros-agent-session": "some-session"}).status_code, 403)
        with self.db.session() as session:
            for row in session.query(Series).all():
                row.is_active = False
        removed = self.client.request("DELETE", url, json=body)
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertNotIn(self.skill_id, {item["id"] for item in self.catalog.list()})
        self.assertEqual(self.client.get(url + "/files").status_code, 404)
        self.assertTrue(self.working.is_dir())
        self.assertTrue(self.original.is_dir())
        self.assertEqual(len(list((self.catalog.root / "trash/removed-skill-records").glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
