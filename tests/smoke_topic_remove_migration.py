"""Upgrade/downgrade preservation check for topic removal storage."""
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from creatoros.config import PROJECT_ROOT
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRun, Database, upgrade_database
from creatoros.storage.models import Base


def _alembic_config(database_url: str) -> Config:
    config = Config(str(Path(PROJECT_ROOT) / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


def stored_row(connection, table, identifier, columns=None):
    # Table/columns come only from this fixture/schema, never user input.
    row = dict(connection.execute(text(f"SELECT * FROM {table} WHERE id=:id"),
                                  {"id": identifier}).mappings().one())
    return {name: row[name] for name in columns} if columns is not None else row


with TemporaryDirectory() as temporary:
    database_path = Path(temporary) / "legacy-0008.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    head = ScriptDirectory.from_config(_alembic_config(database_url)).get_current_head()

    # Build a genuine 0008 database and preserve representative Topic/Run rows
    # across the schema upgrade, downgrade, and re-upgrade.
    upgrade_database(database_url, "20261005_0008")
    database = Database(database_url)
    with database.engine.begin() as connection:
        # Seed raw old-schema rows because the current Repository correctly
        # checks the new removal table, which does not exist at revision 0008.
        connection.execute(text("""INSERT INTO creators
            (id, display_name, platform, timezone, is_active, created_at, updated_at)
            VALUES ('migration-owner', 'Migration', 'xiaohongshu', 'Asia/Shanghai', 1,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""))
        connection.execute(text("""INSERT INTO series
            (id, creator_id, name, description, audience, skill_name, selection_policy,
             publish_policy, replenish_threshold, revision, is_active, created_at, updated_at)
            VALUES ('migration-series', 'migration-owner', 'Legacy', 'Legacy description',
                    'Legacy audience', 'knowledge-to-carousel', 'approval', 'approval',
                    5, 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""))
        connection.execute(text("""INSERT INTO topics
            (id, series_id, title, source, status, position, created_at, updated_at)
            VALUES ('migration-topic', 'migration-series', 'Legacy topic', 'manual',
                    'queued', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""))
        before_topic = stored_row(connection, "topics", "migration-topic")
        legacy_run_columns = [row[1] for row in connection.execute(text("PRAGMA table_info(content_runs)"))]
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261005_0008"
    database.close()

    upgrade_database(database_url)
    database = Database(database_url)
    try:
        with database.engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == head
            assert stored_row(connection, "topics", "migration-topic", before_topic) == before_topic
            # Later migrations add optional references; no source is invented for legacy topics.
            assert connection.execute(text("SELECT reference_id FROM topics WHERE id='migration-topic'")).scalar_one() is None
            assert connection.execute(text("SELECT COUNT(*) FROM topic_removals")).scalar_one() == 0
            assert not connection.execute(text("PRAGMA foreign_key_check")).all()
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert connection.execute(text("SELECT COUNT(*) FROM production_queue_items")).scalar_one() == 0
        legacy_run = ContentRunService(database, output_root=Path(temporary) / "outputs").create("migration-topic")
        with database.engine.connect() as connection:
            before_run = stored_row(connection, "content_runs", legacy_run.id)
    finally:
        database.close()

    # Downgrade is exercised only in this disposable database; it may remove
    # the new tombstone table, but must retain the pre-existing Topic and Run.
    command.downgrade(_alembic_config(database_url), "20261005_0008")
    database = Database(database_url)
    try:
        with database.engine.connect() as connection:
            assert stored_row(connection, "topics", "migration-topic") == before_topic
            assert stored_row(connection, "content_runs", legacy_run.id) == {
                name: before_run[name] for name in legacy_run_columns}
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261005_0008"
    finally:
        database.close()

    upgrade_database(database_url)
    database = Database(database_url)
    try:
        with database.engine.connect() as connection:
            assert stored_row(connection, "topics", "migration-topic", before_topic) == before_topic
            assert stored_row(connection, "content_runs", legacy_run.id) == before_run
            assert not connection.execute(text("PRAGMA foreign_key_check")).all()
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        database.close()

print(f"topic_remove_migration_smoke=passed predecessor=0008 revision={head} legacy_rows=preserved downgrade=preserved fk=clean drift=0")
