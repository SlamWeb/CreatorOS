"""Display categories: isolated SQLite and catalog, no production or external calls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import sys
import unittest

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.storage import Base, ContentRepository, Database, upgrade_database
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
        app = FastAPI()
        app.include_router(skill_display_category_routes(self.db, self.catalog))
        self.app = app
        self.client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1234))
        self.addCleanup(self.client.close)
        self.path = "/api/skill-display-categories"

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
