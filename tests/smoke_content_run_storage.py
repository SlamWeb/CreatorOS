from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from creatoros.runs import ContentRunRepository
from creatoros.storage import (
    Base,
    ContentAttempt,
    ContentAttemptStatus,
    ContentRepository,
    ContentRevision,
    ContentRun,
    ContentRunEventType,
    ContentRunStatus,
    CreatorPlatform,
    Database,
    TopicSource,
    upgrade_database,
)


with TemporaryDirectory() as temporary:
    path = Path(temporary) / "creatoros.db"
    url = f"sqlite:///{path.as_posix()}"
    upgrade_database(url)
    database = Database(url)
    assert {"content_runs", "content_revisions", "content_attempts", "content_run_events"} <= set(
        inspect(database.engine).get_table_names()
    )
    with database.engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert context.get_current_revision() == "20260902_0003"
        drift = compare_metadata(context, Base.metadata)
        assert drift == [], drift

    content = ContentRepository(database)
    content.create_creator(
        creator_id="creator-1",
        display_name="Creator One",
        platform=CreatorPlatform.XIAOHONGSHU,
    )
    content.create_series(
        series_id="series-1",
        creator_id="creator-1",
        name="Agent Basics",
        description="Agent interview cards",
        audience="beginners",
        skill_name="knowledge-to-carousel",
    )
    content.add_topic(
        topic_id="topic-1",
        series_id="series-1",
        title="What is Agent State?",
        source=TopicSource.MANUAL,
    )

    runs = ContentRunRepository(database)
    run_id = str(uuid4())
    revision_id = str(uuid4())
    with runs.transaction() as repository:
        repository.add_run(
            ContentRun(
                id=run_id,
                topic_id="topic-1",
                idempotency_key="topic-1:first",
                status=ContentRunStatus.QUEUED,
                input_snapshot_json={"topic_title": "What is Agent State?"},
            )
        )
        repository.add_revision(
            ContentRevision(
                id=revision_id,
                content_run_id=run_id,
                revision_number=1,
                production_input_json={"topic_title": "What is Agent State?"},
            )
        )
        repository.add_attempt(
            ContentAttempt(
                id=str(uuid4()),
                revision_id=revision_id,
                attempt_number=1,
                status=ContentAttemptStatus.RUNNING,
            )
        )
        repository.add_event(
            run_id,
            ContentRunEventType.CREATED,
            revision_id=revision_id,
            to_status=ContentRunStatus.QUEUED,
        )

    assert runs.get_by_idempotency_key("topic-1:first").id == run_id
    assert runs.get_revision_number(run_id, 1).id == revision_id
    assert runs.next_attempt_number(revision_id) == 2
    database.close()

    restarted = Database(url)
    restored = ContentRunRepository(restarted)
    assert restored.get_run(run_id).status is ContentRunStatus.QUEUED
    assert len(restored.list_revisions(run_id)) == 1
    assert len(restored.list_events(run_id)) == 1
    restarted.close()

print("content_run_storage_smoke=passed revision=20260902_0003 restart=passed")
