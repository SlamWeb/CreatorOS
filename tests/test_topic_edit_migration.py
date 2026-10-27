"""Receipt CHECK-only migration preserves every existing row in an isolated DB."""
from pathlib import Path
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
import unittest

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, inspect, text

from creatoros.config import PROJECT_ROOT
from creatoros.storage import Database, WriteReceipt, upgrade_database
from creatoros.storage.models import Base


def config(url):
    result = Config(str(PROJECT_ROOT / "alembic.ini"))
    result.attributes["database_url"] = url
    return result


def snapshot(database):
    with database.engine.connect() as connection:
        return {table: sorted([dict(row) for row in connection.execute(
            text(f'SELECT * FROM "{table}"')).mappings()], key=repr)
            for table in inspect(connection).get_table_names() if table != "alembic_version"}


def seed_old_schema(database):
    """Use revision 0016's reflected tables, never today's Run ORM/service."""
    now = datetime(2026, 10, 22, 1, 2, 3, tzinfo=timezone.utc)
    stamp = dict(created_at=now, updated_at=now)
    payload = dict(creator_id="owner", series_id="series", series_name="Series",
        skill_name="knowledge-to-carousel", topic_id="topic", topic_title="Original topic",
        topic_brief="Original brief", production_protocol="legacy")
    with database.engine.begin() as connection:
        schema = MetaData()
        schema.reflect(bind=connection)
        connection.execute(schema.tables["creators"].insert(), dict(id="owner", display_name="Owner",
            platform="xiaohongshu", timezone="Asia/Shanghai", is_active=True, **stamp))
        connection.execute(schema.tables["series"].insert(), dict(id="series", creator_id="owner",
            name="Series", description="Original", audience="Original", skill_name="knowledge-to-carousel",
            selection_policy="approval", publish_policy="approval", replenish_threshold=5,
            revision=1, is_active=True, **stamp))
        connection.execute(schema.tables["topics"].insert(), dict(id="topic", series_id="series",
            title="Original topic", brief="Original brief", source="manual", status="queued", position=1, **stamp))
        connection.execute(schema.tables["content_runs"].insert(), dict(id="old-run", topic_id="topic",
            idempotency_key="content:topic", status="queued", active_revision_number=1,
            input_snapshot_json=payload, retryable=False, version=1, **stamp))
        connection.execute(schema.tables["content_revisions"].insert(), dict(id="old-revision",
            content_run_id="old-run", revision_number=1, production_input_json=payload, **stamp))
        connection.execute(schema.tables["content_run_events"].insert(), dict(content_run_id="old-run",
            revision_id="old-revision", event_type="created", to_status="queued",
            payload_json={"idempotency_key": "content:topic"}, created_at=now))
        connection.execute(schema.tables["write_receipts"].insert(), dict(request_id="old-receipt",
            operation="queue_topics", origin="web", resource_id="series",
            response_json={"creator_id": "owner", "original": [1, 2, 3]}, created_at=now))


class TopicEditMigrationTests(unittest.TestCase):
    def assert_upgraded_rows_preserved(self, database, before):
        after = snapshot(database)
        self.assertTrue(set(before).issubset(after))
        for table, original in before.items():
            if table == "content_runs":
                # Migration 0020 adds this nullable scope; it cannot rewrite old data.
                self.assertTrue(all(row["task_id"] is None for row in after[table]))
                actual = [{key: value for key, value in row.items() if key != "task_id"}
                          for row in after[table]]
            else:
                actual = after[table]
            self.assertEqual(actual, original, table)
        for table in set(after) - set(before):
            self.assertEqual(after[table], [], f"Upgrade invented rows in {table}")

    def test_upgrade_downgrade_preserve_all_existing_rows_and_schema_matches(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = f"sqlite:///{(root / 'old.db').as_posix()}"
            upgrade_database(url, "20261022_0016")
            db = Database(url)
            try:
                seed_old_schema(db)
                before = snapshot(db)
            finally:
                db.close()
            upgrade_database(url)
            db = Database(url)
            try:
                self.assert_upgraded_rows_preserved(db, before)
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
                self.assert_upgraded_rows_preserved(db, before)
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
