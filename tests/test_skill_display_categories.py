"""Display categories: isolated SQLite and catalog, no production or external calls."""
import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.integrations.skill_extraction import ExtractionError, SkillExtractionService
from creatoros.storage import Base, ContentRepository, Database, upgrade_database
from creatoros.web.extraction_routes import extraction_routes
from creatoros.web.skill_display_categories import skill_display_category_routes


class SkillDisplayCategoriesTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.url = f"sqlite:///{(root / 'categories.db').as_posix()}"
        upgrade_database(self.url)
        self.db = Database(self.url)
        self.addCleanup(self.db.close)
        self.catalog = ProducerSkillCatalog(root / "catalog")
        source = root / "example"
        source.mkdir()
        (source / "SKILL.md").write_text("---\nname: example\ndescription: example\n---\nbody\n", encoding="utf-8")
        self.skill = self.catalog.register_local(source, role="mind")
        self.skill_path = self.catalog.root / "working" / self.skill["id"] / "SKILL.md"
        self.extraction_calls = []

        async def extract(_directory, images, mode, instruction, cancel, on_thread):
            self.extraction_calls.append((images, mode, instruction))
            return {"note": "已提炼", "skills": [{"name": "extracted",
                "role": "legacy_end_to_end", "output_kind": "text",
                "skill_md": "---\nname: extracted\ndescription: Extracted prompt\n---\nInstructions\n"}]}

        self.extractions = SkillExtractionService(self.catalog, extractor=extract)
        self.addCleanup(self.extractions.shutdown)
        app = FastAPI()
        app.include_router(skill_display_category_routes(self.db, self.catalog))
        app.include_router(extraction_routes(self.extractions, self.db))
        self.app = app
        self.client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234))
        self.addCleanup(self.client.close)
        self.path = "/api/skill-display-categories"
        self.draft_path = "/api/skill-extractions"

    def authored(self, **changes):
        return {"request_id": "authored-category-test", "name": "authored", "description": "Example",
                "content": "Keep my original instructions.", **changes}

    def test_author_intent_is_local_and_initialized_on_confirmation_only(self):
        payload = self.authored(categories=["visual", "content"])
        result = self.client.post(self.draft_path + "/author", json=payload)
        self.assertEqual(result.status_code, 201, result.text)
        job = result.json()
        job_path = f"{self.draft_path}/{job['id']}"
        self.assertEqual(job["categories"], ["content", "visual"])
        self.assertEqual(job["mode"], "single")
        self.assertEqual(job["skills"][0]["role"], "legacy_end_to_end")
        self.assertEqual(self.client.get(self.path).json(), {"items": []})
        self.assertEqual(self.extractions.with_category_intent(self.extractions.get(job["id"])), job)
        self.assertNotIn("categories", self.extractions.get(job["id"]))
        self.assertEqual(self.client.post(self.draft_path + "/author", json={**payload,
            "categories": ["content", "visual"]}).json(), job)
        self.assertEqual(self.client.post(self.draft_path + "/author", json={**payload,
            "categories": ["rules"]}).status_code, 409)
        self.assertEqual({file["path"] for file in job["files"]}, {"SKILL.md", "assets/source.txt"})
        request_record = self.extractions._path("jobs", job["id"]) / "request.json"
        self.assertNotIn("categories", json.loads(request_record.read_text(encoding="utf-8")))
        self.assertEqual(self.client.get(job_path + "/files", params={
            "role": "legacy_end_to_end", "path": "../../request.json"}).status_code, 404)
        self.assertNotIn("categories", job["skills"][0]["skill_md"])

        save = {"expected_digest": job["digest"]}
        for headers, query in (({"x-creatoros-agent-session": "overview"}, ""),
                               ({"x-creatoros-origin": "agent"}, ""), ({}, "?creator_id=account"),
                               ({"origin": "https://example.com"}, "")):
            self.assertEqual(self.client.post(self.draft_path + "/author" + query,
                                             json=payload, headers=headers).status_code, 403)
            self.assertNotIn("categories", self.client.get(job_path + query, headers=headers).json())
            self.assertNotIn("categories", self.client.get(self.draft_path + query, headers=headers).json()["items"][0])
            self.assertEqual(self.client.post(job_path + "/save" + query, json=save, headers=headers).status_code, 403)
        self.assertEqual(self.client.get(self.path).json(), {"items": []})
        self.assertEqual(len(self.catalog.list()), 2)

        saved = self.client.post(job_path + "/save", json=save)
        self.assertEqual(saved.status_code, 200, saved.text)
        identity = saved.json()["saved_skills"][0]["id"]
        self.assertEqual(self.client.get(self.path).json()["items"], [{"family_id": identity,
            "categories": ["content", "visual"], "revision": 1}])
        edited = self.client.put(f"{self.path}/{identity}", json={"categories": [], "expected_revision": 1})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(self.client.post(job_path + "/save", json=save).json(), saved.json())
        agent_replay = self.client.post(job_path + "/save", json=save, headers={"x-creatoros-origin": "agent"})
        self.assertEqual(agent_replay.status_code, 200, agent_replay.text)
        self.assertNotIn("categories", agent_replay.json())
        self.assertEqual(self.client.get(self.path).json()["items"], [edited.json()])

    def test_extract_intent_replays_after_restart_and_preserves_legacy_inputs(self):
        payload = {"request_id": "extracted-categories", "source_text": "Reference", "categories": ["rules"]}
        submitted = self.client.post(self.draft_path, json=payload)
        self.assertEqual(submitted.status_code, 202, submitted.text)
        identifier = submitted.json()["id"]
        self.extractions.worker.join(timeout=10)
        current = self.client.get(f"{self.draft_path}/{identifier}").json()
        self.assertEqual(current["status"], "ready")
        self.assertEqual(current["categories"], ["rules"])
        self.assertEqual(self.extraction_calls, [([], "single", "")])
        self.assertEqual(self.client.get(self.path).json(), {"items": []})
        request_record = self.extractions._path("jobs", identifier) / "request.json"
        self.assertNotIn("categories", json.loads(request_record.read_text(encoding="utf-8")))

        reopened = SkillExtractionService(self.catalog, extractor=self.extractions.extractor)
        self.addCleanup(reopened.shutdown)
        reopened.start()
        replay = reopened.submit(**payload)
        self.assertEqual(replay["digest"], current["digest"])
        self.assertEqual(reopened.get(identifier, include_categories=True)["categories"], ["rules"])
        self.assertEqual(self.extraction_calls, [([], "single", "")])
        saved = self.client.post(f"{self.draft_path}/{identifier}/save", json={"expected_digest": current["digest"]})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.client.get(self.path).json()["items"][0]["categories"], ["rules"])

        legacy = {"request_id": "legacy-extraction", "source_text": "Legacy reference", "mode": "single"}
        original = self.client.post(self.draft_path, json=legacy).json()
        repeated = self.client.post(self.draft_path, json={**legacy, "categories": []}).json()
        self.assertEqual(repeated["id"], original["id"])
        self.assertNotIn("categories", repeated)
        request_record = self.extractions._path("jobs", original["id"]) / "request.json"
        self.assertNotIn("categories", json.loads(request_record.read_text(encoding="utf-8")))
        legacy_author = self.authored(role="mind")
        original_author = self.client.post(self.draft_path + "/author", json=legacy_author).json()
        repeated_author = self.client.post(self.draft_path + "/author", json={**legacy_author, "categories": []}).json()
        self.assertEqual(repeated_author, original_author)
        self.assertEqual(repeated_author["skills"][0]["role"], "mind")

    def test_invalid_or_scoped_categories_do_not_create_drafts(self):
        for path, payload, legacy in (
                (self.draft_path + "/author", self.authored(), {"role": "mind"}),
                (self.draft_path, {"request_id": "extract-scoped", "source_text": "Reference"}, {"mode": "pair"})):
            for categories in (None, ["unknown"], ["rules", "rules"], [False]):
                self.assertEqual(self.client.post(path, json={**payload, "categories": categories}).status_code, 422)
            self.assertEqual(self.client.post(path, json={**payload, **legacy, "categories": ["rules"]}).status_code, 422)
            with TestClient(self.app, base_url="http://127.0.0.1", client=("192.0.2.2", 1234)) as remote:
                self.assertEqual(remote.post(path, json={**payload, "categories": ["rules"]}).status_code, 403)
        self.assertEqual(self.extractions.list(), [])

    def test_interrupted_save_retries_without_overwriting_existing_classification(self):
        job = self.client.post(self.draft_path + "/author", json=self.authored(categories=["visual"])).json()
        job_path = f"{self.draft_path}/{job['id']}"
        save = {"expected_digest": job["digest"]}
        with patch("creatoros.web.extraction_routes.initialize_display_categories",
                   side_effect=OperationalError(None, None, RuntimeError("injected database write failure"))):
            self.assertEqual(self.client.post(job_path + "/save", json=save).status_code, 503)
        self.assertEqual(self.client.get(job_path).json()["status"], "ready")
        identity = next(item["id"] for item in self.catalog.list() if item["name"] == "authored")
        self.assertEqual(self.client.get(self.path).json()["items"], [])
        skills = [{**job["skills"][0], "skill_md": job["skills"][0]["skill_md"] + "changed"}]
        self.assertEqual(self.client.post(job_path + "/draft", json={**save, "skills": skills}).status_code, 409)
        update = self.extractions._update

        def interrupt_terminal_write(job_id, **changes):
            if changes.get("status") == "saved":
                raise OSError("injected terminal receipt write failure")
            return update(job_id, **changes)

        with patch.object(self.extractions, "_update", side_effect=interrupt_terminal_write), TestClient(
                self.app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234),
                raise_server_exceptions=False) as interrupted:
            self.assertEqual(interrupted.post(job_path + "/save", json=save).status_code, 500)
        self.assertEqual(self.client.get(self.path).json()["items"], [{"family_id": identity,
            "categories": ["visual"], "revision": 1}])
        edited = self.client.put(f"{self.path}/{identity}", json={"categories": [], "expected_revision": 1})
        self.assertEqual(edited.status_code, 200, edited.text)
        saved = self.client.post(job_path + "/save", json=save)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["saved_skills"][0]["id"], identity)
        self.assertEqual(self.client.get(self.path).json()["items"], [edited.json()])

    def test_preexisting_registration_does_not_initialize_categories(self):
        job = self.client.post(self.draft_path + "/author", json=self.authored(categories=["visual"])).json()
        draft = self.extractions._draft_root(self.extractions._read(job["id"])) / "legacy_end_to_end"
        previous = self.catalog.register_local(draft, role="legacy_end_to_end", source_note="earlier local import")
        saved = self.client.post(f"{self.draft_path}/{job['id']}/save", json={"expected_digest": job["digest"]})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["saved_skills"][0]["id"], previous["id"])
        self.assertEqual(self.client.get(self.path).json()["items"], [])

    def test_rejected_registration_leaves_the_unpublished_draft_editable(self):
        job = self.client.post(self.draft_path + "/author", json=self.authored(categories=["visual"])).json()
        identifier = job["id"]
        initializer = lambda *_: self.fail("Rejected drafts must not initialize categories")
        with patch.object(self.catalog, "register_local", side_effect=ValueError("Invalid catalog source")):
            refused = self.client.post(f"{self.draft_path}/{identifier}/save", json={"expected_digest": job["digest"]})
            self.assertEqual(refused.status_code, 422)
            self.assertEqual(refused.json()["detail"], "Invalid catalog source")
        self.assertIsNone(self.extractions._read(identifier)["category_save_digest"])
        self.extractions._update(identifier, category_save_digest="0" * 64)
        with self.assertRaises(ExtractionError) as mismatch:
            self.extractions.save(identifier, job["digest"], initialize_categories=initializer)
        self.assertEqual(mismatch.exception.status, 409)
        self.extractions._update(identifier, category_save_digest=None)

        skills = [{**job["skills"][0], "skill_md": job["skills"][0]["skill_md"] + "changed"}]
        edited = self.client.post(f"{self.draft_path}/{identifier}/draft", json={
            "expected_digest": job["digest"], "skills": skills})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertNotEqual(edited.json()["digest"], job["digest"])
        self.assertEqual(edited.json()["categories"], ["visual"])

    def test_version_family_metadata_and_empty_classification(self):
        before_bytes = self.skill_path.read_bytes()
        before_digest = self.catalog.describe(self.skill["id"])["digest"]
        candidate = self.catalog.create_skill_candidate(self.skill["id"], before_digest, "candidate-one")
        self.assertEqual(self.client.get(self.path).json(), {"items": []})
        result = self.client.put(f"{self.path}/{self.skill['id']}", json={
            "categories": ["visual", "rules"], "expected_revision": 0})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), {"family_id": self.skill["id"], "categories": ["rules", "visual"], "revision": 1})
        # An exact candidate version maps back to the same family and revision.
        updated = self.client.put(f"{self.path}/{candidate['id']}", json={"categories": [], "expected_revision": 1})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json(), {"family_id": self.skill["id"], "categories": [], "revision": 2})
        self.assertEqual(self.client.get(self.path).json()["items"], [updated.json()])
        self.assertEqual(self.skill_path.read_bytes(), before_bytes)
        self.assertEqual(self.catalog.describe(self.skill["id"])["digest"], before_digest)

    def test_builtin_unknown_and_validation(self):
        builtin = self.client.put(f"{self.path}/knowledge-to-carousel", json={"categories": ["content"], "expected_revision": 0})
        self.assertEqual(builtin.status_code, 200, builtin.text)
        self.assertEqual(builtin.json()["family_id"], "knowledge-to-carousel")
        self.assertEqual(self.client.put(f"{self.path}/does-not-exist", json={"categories": [], "expected_revision": 0}).status_code, 404)
        for payload in [
            {"categories": ["other"], "expected_revision": 0},
            {"categories": ["rules", "rules"], "expected_revision": 0},
            {"categories": ["content"], "expected_revision": True},
            {"categories": ["content"], "expected_revision": -1},
            {"categories": ["content"], "expected_revision": 0, "extra": 1},
        ]:
            self.assertEqual(self.client.put(f"{self.path}/{self.skill['id']}", json=payload).status_code, 422)
        self.assertEqual(len(self.client.get(self.path).json()["items"]), 1)

    def test_compare_and_set_and_concurrent_create(self):
        def write(category):
            response = self.client.put(f"{self.path}/{self.skill['id']}", json={
                "categories": [category], "expected_revision": 0})
            return response.status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(list(executor.map(write, ["content", "visual"])), [200, 409])
        current = self.client.get(self.path).json()["items"][0]
        self.assertEqual(current["revision"], 1)
        stale = self.client.put(f"{self.path}/{self.skill['id']}", json={"categories": [], "expected_revision": 0})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "skill_display_category_conflict")
        self.assertEqual(self.client.get(self.path).json()["items"][0], current)
        def replace(category):
            return self.client.put(f"{self.path}/{self.skill['id']}", json={
                "categories": [category], "expected_revision": 1}).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(list(executor.map(replace, ["processing", "rules"])), [200, 409])
        latest = self.client.get(self.path).json()["items"][0]
        self.assertEqual(latest["revision"], 2)
        same = self.client.put(f"{self.path}/{self.skill['id']}", json={
            "categories": latest["categories"], "expected_revision": 2})
        self.assertEqual(same.status_code, 200)
        self.assertEqual(same.json()["revision"], 3)

    def test_agent_account_remote_and_nonlocal_origin_are_denied(self):
        body = {"categories": ["rules"], "expected_revision": 0}
        for headers in [
            {"x-creatoros-agent-session": "overview-session"},
            {"x-creatoros-agent-session": "creator-session"},
            {"x-creatoros-origin": "agent"},
            {"origin": "https://example.com"},
        ]:
            self.assertEqual(self.client.get(self.path, headers=headers).status_code, 403)
            self.assertEqual(self.client.put(f"{self.path}/{self.skill['id']}", headers=headers, json=body).status_code, 403)
        self.assertEqual(self.client.get(self.path + "?creator_id=a").status_code, 403)
        self.assertEqual(self.client.put(f"{self.path}/{self.skill['id']}?creator_id=a", json=body).status_code, 403)
        with TestClient(self.app, base_url="http://127.0.0.1", client=("192.0.2.2", 1234)) as remote:
            self.assertEqual(remote.get(self.path).status_code, 403)
            self.assertEqual(remote.put(f"{self.path}/{self.skill['id']}", json=body).status_code, 403)
        self.assertEqual(self.client.get(self.path).json(), {"items": []})

    def test_upgrade_preserves_existing_rows_and_metadata(self):
        with TemporaryDirectory() as directory:
            old_url = f"sqlite:///{(Path(directory) / 'old.db').as_posix()}"
            upgrade_database(old_url, "20261103_0028")
            old = Database(old_url)
            try:
                ContentRepository(old).create_creator(creator_id="keep", display_name="Preserved")
                with old.engine.connect() as connection:
                    before = connection.execute(text("SELECT * FROM creators WHERE id = 'keep'")).mappings().one()
                    before = dict(before)
                upgrade_database(old_url)
                with old.engine.connect() as connection:
                    self.assertEqual(dict(connection.execute(text("SELECT * FROM creators WHERE id = 'keep'")).mappings().one()), before)
                    self.assertEqual(connection.execute(text("PRAGMA integrity_check")).scalar(), "ok")
                    self.assertEqual(connection.execute(text("PRAGMA foreign_key_check")).all(), [])
                    self.assertEqual(compare_metadata(MigrationContext.configure(connection), Base.metadata), [])
            finally:
                old.close()

    def test_actual_app_installs_route_and_preserves_middleware_boundary(self):
        # A separate interpreter sets DATABASE_URL before app.py's module-level
        # app is constructed. All resulting files remain in the temporary root.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            url = f"sqlite:///{(root / 'app.db').as_posix()}"
            program = r'''
import os
from fastapi.testclient import TestClient
from creatoros.storage import upgrade_database
upgrade_database(os.environ["DATABASE_URL"])
from creatoros.web.app import create_app
app = create_app(database_url=os.environ["DATABASE_URL"], defer_background_start=True)
with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as client:
    path = "/api/skill-display-categories"
    assert client.get(path).json() == {"items": []}
    body = {"categories": ["content"], "expected_revision": 0}
    assert client.put(path + "/knowledge-to-carousel", json=body, headers={"origin": "https://example.com"}).status_code == 403
    assert client.put(path + "/knowledge-to-carousel", json=body, headers={"x-creatoros-agent-session": "unknown"}).status_code == 403
    result = client.put(path + "/knowledge-to-carousel", json=body)
    assert result.status_code == 200, result.text
    assert client.get(path).json()["items"] == [result.json()]
    draft_path = "/api/skill-extractions/author"
    authored = {"request_id": "actual-app-categories", "name": "actual-app", "description": "Example",
                "content": "Keep these instructions.", "categories": ["processing"]}
    overview = app.state.chat.create()["id"]
    headers = {"x-creatoros-agent-session": overview}
    assert client.post(draft_path, json=authored, headers=headers).status_code == 403
    created = client.post(draft_path, json=authored)
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["categories"] == ["processing"]
    job_path = "/api/skill-extractions/" + job["id"]
    assert "categories" not in client.get(job_path, headers=headers).json()
    save = {"expected_digest": job["digest"]}
    assert client.post(job_path + "/save", json=save, headers=headers).status_code == 403
    saved = client.post(job_path + "/save", json=save)
    assert saved.status_code == 200, saved.text
    assert any(item["family_id"] == saved.json()["saved_skills"][0]["id"] and item["categories"] == ["processing"]
               for item in client.get(path).json()["items"])
    assert not app.state.background_services_started
'''
            env = os.environ.copy()
            env.update(DATABASE_URL=url, PYTHON_DOTENV_DISABLED="1", DEEPSEEK_API_KEY="", OPENAI_API_KEY="",
                       CODEX_API_KEY="", CODEX_HOME=str(root / "codex-home"), TEMP=str(root), TMP=str(root))
            completed = subprocess.run([sys.executable, "-c", program], env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_downgrade_rejects_loss_of_saved_categories(self):
        result = self.client.put(f"{self.path}/{self.skill['id']}", json={
            "categories": ["rules"], "expected_revision": 0})
        self.assertEqual(result.status_code, 200)
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["database_url"] = self.url
        with self.assertRaisesRegex(RuntimeError, "不允许降级删除"):
            command.downgrade(config, "20261103_0028")
        self.assertEqual(self.client.get(self.path).json()["items"], [result.json()])


if __name__ == "__main__":
    unittest.main()
