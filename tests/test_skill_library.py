"""Real isolated SQLite/catalog/HTTP contracts; no producer or running service touched."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from creatoros.storage import Base, ContentRepository, Database, upgrade_database
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillDigestConflict, freeze_skill
from creatoros.integrations.skill_library import SkillLibraryService, SkillLibraryConflict
from creatoros.web.skill_library_routes import create_skill_library_router


class SkillLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        url = f"sqlite:///{(self.root / 'test.db').as_posix()}"
        upgrade_database(url)
        self.db = Database(url)
        self.addCleanup(self.db.close)
        repository = ContentRepository(self.db)
        for identity in ("a", "b"):
            repository.create_creator(creator_id=identity, display_name=identity)
            repository.create_series(series_id=identity, creator_id=identity, name=identity,
                                     description="test", audience="test", skill_name="knowledge-to-carousel")
        self.catalog = ProducerSkillCatalog(self.root / "catalog")
        self.video = self.register("video-test", "production", "creatoros-outputs: [video]\n")
        self.mind = self.register("mind-test", "mind")
        self.library = SkillLibraryService(self.db, self.catalog)
        app = FastAPI()
        app.include_router(create_skill_library_router(self.library))
        self.client = TestClient(app)

    def register(self, name, role, metadata=""):
        directory = self.root / name
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n{metadata}---\nbody\n", encoding="utf-8")
        (directory / "script.py").write_text("print('frozen')\n", encoding="utf-8")
        return self.catalog.register_local(directory, role=role)

    def test_migration_and_old_bindings_are_read_only_compatible(self):
        with self.db.engine.connect() as connection:
            self.assertEqual(compare_metadata(MigrationContext.configure(connection), Base.metadata), [])
        result = self.client.get("/api/creators/a/skills").json()
        self.assertEqual(result["version"], 0)
        self.assertEqual(result["items"][0]["id"], "knowledge-to-carousel")
        series = self.library.get_series("a")
        self.assertEqual(series["defaults"]["image"], [{"skill_id": "knowledge-to-carousel", "role": "primary"}])
        response = self.client.delete("/api/creators/a/skills/knowledge-to-carousel?expected_version=0")
        self.assertEqual(response.status_code, 409)

    def test_idempotency_and_shared_use_does_not_grant_edit(self):
        body = {"skill_id": self.video["id"], "request_id": "request-one"}
        first = self.client.post("/api/creators/a/skills", json=body)
        self.assertEqual(first.status_code, 200, first.text)
        replay = self.client.post("/api/creators/a/skills", json=body)
        self.assertEqual(first.json(), replay.json())
        self.assertTrue(self.library.can_use("a", self.video["id"]))
        self.assertFalse(self.library.can_edit("a", self.video["id"]))
        body["skill_id"] = self.mind["id"]
        self.assertEqual(self.client.post("/api/creators/a/skills", json=body).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/creators/a/skills/{self.video['id']}?expected_version=0").status_code, 409)

    def test_candidates_require_accessible_family_and_pool_requires_account_library(self):
        candidate = self.catalog.create_skill_candidate(self.video["id"], self.video["digest"], "candidate-one")
        self.assertEqual(self.client.post("/api/creators/b/skills", json={
            "skill_id": candidate["id"], "request_id": "request-one"}).status_code, 403)
        self.library.add("a", self.video["id"], "request-one")
        self.assertTrue(self.library.contains("a", candidate["id"]))
        self.assertFalse(self.library.contains("b", candidate["id"]))
        self.assertIn(candidate["id"], [item["id"] for item in self.library.available("a")])
        defaults = {"image": [], "video": [{"skill_id": candidate["id"], "role": "primary"}]}
        body = {"expected_version": 0, "skill_ids": ["knowledge-to-carousel", candidate["id"]], "defaults": defaults}
        self.assertEqual(self.client.put("/api/series/b/skills", json=body).status_code, 403)
        first = self.client.put("/api/series/a/skills", json=body)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.client.put("/api/series/a/skills", json=body).status_code, 409)
        with self.assertRaises(SkillLibraryConflict):
            self.library.remove("a", self.video["id"], 1)

    def test_source_owner_can_add_private_candidate_without_exposing_it_to_other_accounts(self):
        candidate = self.catalog.create_skill_candidate(self.video["id"], self.video["digest"], "candidate-one")
        service = SkillLibraryService(self.db, self.catalog, source_owner=lambda item: "a")
        self.assertTrue(service.can_edit("a", candidate["id"]))
        self.assertFalse(service.can_edit("b", candidate["id"]))
        service.add("a", candidate["id"], "request-one")
        with self.assertRaises(PermissionError):
            service.add("b", candidate["id"], "request-one")

    def test_output_gate_digest_and_complete_frozen_bytes(self):
        self.assertEqual(self.video["outputs"], ["video"])
        self.assertFalse(self.video["producible"])
        directory = self.catalog.resolve_for_task(self.video["id"], self.video["digest"], "video", role="primary")
        target = self.root / "frozen"
        freeze_skill(directory, target, self.video["digest"])
        self.assertEqual((target / "script.py").read_text(), "print('frozen')\n")
        with self.assertRaises(SkillDigestConflict):
            self.catalog.resolve_for_task(self.video["id"], "0" * 64, "video", True)
        with self.assertRaises(ValueError):
            self.catalog.resolve_for_task(self.mind["id"], self.mind["digest"], "video", True)
        self.catalog.resolve_for_task(self.mind["id"], self.mind["digest"], "video", False)
        with self.assertRaises(ValueError):
            self.catalog.resolve(self.video["id"])

    def test_two_service_instances_cannot_both_win_same_pool_version(self):
        def write(_):
            try:
                SkillLibraryService(self.db, self.catalog).set_series("a", 0, ["knowledge-to-carousel"], {"image": [], "video": []})
                return "ok"
            except SkillLibraryConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(list(executor.map(write, range(2))), ["ok", "conflict"])


if __name__ == "__main__":
    unittest.main()
