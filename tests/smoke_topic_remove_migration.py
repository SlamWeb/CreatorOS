"""Upgrade/downgrade preservation check for topic removal storage."""
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text

from creatoros.config import PROJECT_ROOT
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRun, Database, upgrade_database
from creatoros.storage.models import Base


def _alembic_config(database_url: str) -> Config:
    config = Config(str(Path(PROJECT_ROOT) / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


with TemporaryDirectory() as temporary:
    database_path = Path(temporary) / "legacy-0008.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

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
        before_topic = connection.execute(text("SELECT * FROM topics WHERE id='migration-topic'")).one()
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261005_0008"
    database.close()

    upgrade_database(database_url)
    database = Database(database_url)
    try:
        with database.engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261009_0009"
            assert connection.execute(text("SELECT * FROM topics WHERE id='migration-topic'")).one() == before_topic
            assert connection.execute(text("SELECT COUNT(*) FROM topic_removals")).scalar_one() == 0
            assert not connection.execute(text("PRAGMA foreign_key_check")).all()
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
        legacy_run = ContentRunService(database, output_root=Path(temporary) / "outputs").create("migration-topic")
        with database.engine.connect() as connection:
            before_run = connection.execute(text("SELECT * FROM content_runs WHERE id=:id"),
                                            {"id": legacy_run.id}).one()
    finally:
        database.close()

    # Downgrade is exercised only in this disposable database; it may remove
    # the new tombstone table, but must retain the pre-existing Topic and Run.
    command.downgrade(_alembic_config(database_url), "20261005_0008")
    database = Database(database_url)
    try:
        with database.engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM topics WHERE id='migration-topic'")).one() == before_topic
            assert connection.execute(text("SELECT * FROM content_runs WHERE id=:id"),
                                      {"id": legacy_run.id}).one() == before_run
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261005_0008"
    finally:
        database.close()

    upgrade_database(database_url)
    database = Database(database_url)
    try:
        with database.engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM topics WHERE id='migration-topic'")).one() == before_topic
            assert connection.execute(text("SELECT * FROM content_runs WHERE id=:id"),
                                      {"id": legacy_run.id}).one() == before_run
            assert not connection.execute(text("PRAGMA foreign_key_check")).all()
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        database.close()

print("topic_remove_migration_smoke=passed predecessor=0008 revision=0009 legacy_rows=preserved downgrade=preserved fk=clean drift=0")
