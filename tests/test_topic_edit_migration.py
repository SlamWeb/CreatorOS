"""Receipt CHECK-only migration preserves every existing row in an isolated DB."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from creatoros.config import PROJECT_ROOT
from creatoros.storage import ContentRepository, Database, WriteReceipt, upgrade_database
from creatoros.storage.models import Base, TopicSource
from creatoros.runs import ContentRunService


def config(url):
    result = Config(str(PROJECT_ROOT / "alembic.ini"))
    result.attributes["database_url"] = url
    return result


def snapshot(database):
    with database.engine.connect() as connection:
        return {table: sorted([dict(row) for row in connection.execute(
            text(f'SELECT * FROM "{table}"')).mappings()], key=repr)
            for table in inspect(connection).get_table_names() if table != "alembic_version"}


class TopicEditMigrationTests(unittest.TestCase):
    def test_upgrade_downgrade_preserve_all_existing_rows_and_schema_matches(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = f"sqlite:///{(root / 'old.db').as_posix()}"
            upgrade_database(url, "20261022_0016")
            db = Database(url)
            try:
                repo = ContentRepository(db)
                repo.create_creator(creator_id="owner", display_name="Owner")
                repo.create_series(series_id="series", creator_id="owner", name="Series",
                    description="Original", audience="Original", skill_name="knowledge-to-carousel")
                repo.add_topic(topic_id="topic", series_id="series", title="Original topic",
                    brief="Original brief", source=TopicSource.MANUAL)
                ContentRunService(db, output_root=root / "outputs").create("topic")
                with db.session() as session:
                    session.add(WriteReceipt(request_id="old-receipt", operation="queue_topics", origin="web",
                        resource_id="series", response_json={"creator_id": "owner", "original": [1, 2, 3]}))
                before = snapshot(db)
            finally:
                db.close()
            upgrade_database(url)
            db = Database(url)
            try:
                self.assertEqual(snapshot(db), before)
                with db.engine.connect() as connection:
                    self.assertFalse(compare_metadata(MigrationContext.configure(connection), Base.metadata))
                    self.assertFalse(connection.execute(text("PRAGMA foreign_key_check")).all())
                    self.assertEqual(connection.execute(text("PRAGMA integrity_check")).scalar_one(), "ok")
            finally:
                db.close()
            command.downgrade(config(url), "20261022_0016")
            db = Database(url)
            try:
                self.assertEqual(snapshot(db), before)
            finally:
                db.close()
            upgrade_database(url)
            db = Database(url)
            try:
                self.assertEqual(snapshot(db), before)
                with db.session() as session:
                    session.add(WriteReceipt(request_id="new-edit-receipt", operation="edit_topic", origin="agent",
                        resource_id="topic", response_json={"creator_id": "owner"}))
            finally:
                db.close()
            with self.assertRaisesRegex(RuntimeError, "cannot be downgraded safely"):
                command.downgrade(config(url), "20261022_0016")
            db = Database(url)
            try:
                with db.session() as session:
                    self.assertIsNotNone(session.get(WriteReceipt, "new-edit-receipt"))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
