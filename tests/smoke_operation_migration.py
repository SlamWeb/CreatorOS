from pathlib import Path
from tempfile import TemporaryDirectory
from sqlalchemy import text
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from creatoros.storage import Database, upgrade_database
from creatoros.storage.models import Base
from creatoros.web.queries import StudioQueryService

with TemporaryDirectory() as tmp:
    url = f"sqlite:///{(Path(tmp) / 'old.db').as_posix()}"
    upgrade_database(url, "20260902_0003")
    db = Database(url)
    with db.engine.begin() as c:
        c.execute(text("""INSERT INTO pending_operations
            (id, request_text, decision_status, status, revision, created_at, updated_at)
            VALUES ('old', '历史请求', 'needs_clarification', 'needs_clarification', 3, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""))
        c.exec_driver_sql("""INSERT INTO operation_events
            (pending_operation_id, event_type, payload_json, created_at)
            VALUES ('old', 'proposed', '{"revision":3}', CURRENT_TIMESTAMP)""")
    db.close()
    upgrade_database(url)
    db = Database(url)
    try:
        with db.engine.connect() as c:
            assert c.execute(text("select version, revision, scope_series_id from pending_operations")).one() == (1, 3, None)
            assert c.execute(text("select count(*) from operation_events")).scalar() == 1
            assert not compare_metadata(MigrationContext.configure(c), Base.metadata)
            assert not c.execute(text("PRAGMA foreign_key_check")).all()
        old = StudioQueryService(db).get_operation("old")
        assert old.version == 1 and old.revision == 3
    finally:
        db.close()
print("operation_migration_smoke=passed old_rows=preserved events=preserved metadata=no_drift")
