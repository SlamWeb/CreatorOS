"""Real isolated HTTP/SQLite rename contracts; no model or formal service calls."""
from copy import deepcopy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

import httpx
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from creatoros.config import PROJECT_ROOT
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, ContentRun, ContentRunStatus, Database, WriteReceipt, upgrade_database
from creatoros.storage.models import Base, TopicSource
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


def rows(database):
    with database.engine.connect() as connection:
        return {table: sorted([dict(row) for row in connection.execute(
            text(f'SELECT * FROM "{table}"')).mappings()], key=repr)
            for table in inspect(connection).get_table_names() if table != "alembic_version"}


class SeriesRenameTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db, self.runs, self.producer, self.app = make_fixture(self.root)
        self.host = serve(self.app)
        self.base = self.host.__enter__()
        self.client = httpx.Client(base_url=self.base, timeout=10, trust_env=False)

    def tearDown(self):
        try:
            self.client.close()
            self.host.__exit__(None, None, None)
        finally:
            self.db.close()
            self.temporary.cleanup()

    def rename(self, name, *, revision=1, request_id=None, series_id="agent-notes"):
        return self.client.patch(f"/api/series/{series_id}", json={
            "name": name, "expected_revision": revision, "request_id": request_id or uuid4().hex})

    def test_rename_preserves_all_production_rows_and_files_while_running(self):
        run = self.runs.create("review-1")
        self.runs.execute(run.id)  # Local fixture images, never a model invocation.
        with self.db.session() as session:
            session.get(ContentRun, run.id).status = ContentRunStatus.PRODUCING
        before = rows(self.db)
        files = {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in (self.root / "outputs").rglob("*") if path.is_file()}
        result = self.rename("  新栏目名  ")
        self.assertEqual(result.status_code, 200, result.text)
        renamed = result.json()["series"]
        self.assertEqual((renamed["id"], renamed["name"], renamed["revision"]),
                         ("agent-notes", "新栏目名", 2))
        after = rows(self.db)
        for table in before.keys() - {"series", "write_receipts"}:
            self.assertEqual(after[table], before[table], table)
        old_series, new_series = deepcopy(before["series"]), deepcopy(after["series"])
        for item in old_series + new_series:
            for key in ("name", "revision", "updated_at"):
                item.pop(key)
        self.assertEqual(old_series, new_series)
        self.assertEqual(files, {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (self.root / "outputs").rglob("*") if path.is_file()})
        self.assertEqual(self.producer.calls, 1)

    def test_cas_and_exact_replay_keep_first_frozen_response(self):
        request_id = uuid4().hex
        first = self.rename("第一次改名", request_id=request_id)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.rename("第二次改名", revision=2)
        self.assertEqual(second.status_code, 200, second.text)
        before = rows(self.db)
        replay = self.rename("第一次改名", request_id=request_id)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json(), {**first.json(), "deduplicated": True})
        self.assertEqual(rows(self.db), before)
        for name, revision, rid in (("不同正文", 1, request_id), ("第一次改名", 2, request_id),
                                    ("过期请求", 1, uuid4().hex)):
            refused = self.rename(name, revision=revision, request_id=rid)
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(rows(self.db), before)
        with self.db.session() as session:
            saved = session.get(WriteReceipt, request_id)
            self.assertEqual(saved.operation, "rename_series")
        self.assertEqual(self.client.get("/api/series/agent-notes").json()["name"], "第二次改名")

    def test_validation_name_conflict_and_removed_series_do_not_write(self):
        before = rows(self.db)
        for name in ("", "  \n ", "名" * 121):
            response = self.rename(name)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(rows(self.db), before)
        created = self.client.post("/api/series", json={"name": "已有栏目", "creator_id": "review-lab",
            "skill_name": "knowledge-to-carousel", "request_id": uuid4().hex})
        self.assertEqual(created.status_code, 201, created.text)
        other_id = created.json()["series"]["id"]
        request_id = uuid4().hex
        conflict = self.rename("已有栏目", request_id=request_id)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        corrected = self.rename("可用名字", request_id=request_id)
        self.assertEqual(corrected.status_code, 200, corrected.text)
        wrong_target = self.rename("可用名字", request_id=request_id, series_id=other_id)
        self.assertEqual(wrong_target.status_code, 409, wrong_target.text)
        for identifier, revision in ((other_id, 1), ("agent-notes", 2)):
            removed = self.client.request("DELETE", f"/api/series/{identifier}", json={
                "expected_revision": revision, "request_id": uuid4().hex})
            self.assertEqual(removed.status_code, 200, removed.text)
            refused = self.rename("不能改已删除栏目", revision=revision, series_id=identifier)
            self.assertEqual(refused.status_code, 404, refused.text)
        self.assertEqual(self.rename("不存在", series_id="missing").status_code, 404)

    def test_current_account_can_rename_and_foreign_session_cannot(self):
        repo = ContentRepository(self.db)
        repo.create_creator(creator_id="other", display_name="Other")
        own = self.app.state.chat.create("review-lab")["id"]
        other = self.app.state.chat.create("other")["id"]
        payload = {"name": "账号改名", "expected_revision": 1, "request_id": uuid4().hex}
        before = rows(self.db)
        denied = self.client.patch("/api/series/agent-notes", json=payload,
                                   headers={"x-creatoros-agent-session": other})
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(rows(self.db), before)
        accepted = self.client.patch("/api/series/agent-notes", json=payload,
                                     headers={"x-creatoros-agent-session": own})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["series"]["name"], "账号改名")


class SeriesRenameMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_old_rows_schema_and_refuses_unsafe_downgrade(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = f"sqlite:///{(root / 'old.db').as_posix()}"
            upgrade_database(url, "20261024_0018")
            db = Database(url)
            try:
                repo = ContentRepository(db)
                repo.create_creator(creator_id="owner", display_name="Owner")
                repo.create_series(series_id="series", creator_id="owner", name="Series",
                    description="Original", audience="Original", skill_name="knowledge-to-carousel")
                repo.add_topic(topic_id="topic", series_id="series", title="Original topic",
                               source=TopicSource.MANUAL)
                ContentRunService(db, output_root=root / "outputs").create("topic")
                with db.session() as session:
                    session.add(WriteReceipt(request_id="old-receipt", operation="queue_topics", origin="web",
                        resource_id="series", response_json={"creator_id": "owner", "original": [1, 2, 3]}))
                before = rows(db)
            finally:
                db.close()
            cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
            cfg.attributes["database_url"] = url
            for operation in (lambda: upgrade_database(url),
                              lambda: command.downgrade(cfg, "20261024_0018"),
                              lambda: upgrade_database(url)):
                operation()
                db = Database(url)
                try:
                    self.assertEqual(rows(db), before)
                    with db.engine.connect() as connection:
                        self.assertFalse(connection.execute(text("PRAGMA foreign_key_check")).all())
                        self.assertEqual(connection.execute(text("PRAGMA integrity_check")).scalar_one(), "ok")
                finally:
                    db.close()
            db = Database(url)
            try:
                with db.engine.connect() as connection:
                    self.assertFalse(compare_metadata(MigrationContext.configure(connection), Base.metadata))
                with db.session() as session:
                    session.add(WriteReceipt(request_id="rename-receipt", operation="rename_series", origin="web",
                        resource_id="series", response_json={"creator_id": "owner"}))
            finally:
                db.close()
            with self.assertRaisesRegex(RuntimeError, "cannot be downgraded safely"):
                command.downgrade(cfg, "20261024_0018")


if __name__ == "__main__":
    unittest.main()
