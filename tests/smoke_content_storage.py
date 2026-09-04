from pathlib import Path
from tempfile import TemporaryDirectory

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from creatoros.storage import (
    Base,
    ContentRepository,
    CreatorPlatform,
    Database,
    OperationPolicy,
    TopicSource,
    TopicStatus,
    upgrade_database,
)


with TemporaryDirectory() as temporary_directory:
    database_path = Path(temporary_directory) / "creatoros.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)

    database = Database(database_url)
    assert set(inspect(database.engine).get_table_names()) == {
        "alembic_version",
        "content_attempts",
        "content_revisions",
        "content_run_events",
        "content_runs",
        "creators",
        "operation_events",
        "pending_operations",
        "series",
        "topics",
    }
    with database.engine.connect() as connection:
        migration_context = MigrationContext.configure(connection)
        assert migration_context.get_current_revision() == "20260904_0004"
        assert compare_metadata(migration_context, Base.metadata) == []

    repository = ContentRepository(database)
    creator = repository.create_creator(
        creator_id="interview-lab",
        display_name="面试知识实验室",
        platform=CreatorPlatform.XIAOHONGSHU,
        daily_content_limit=3,
    )
    series = repository.create_series(
        series_id="agent-basics",
        creator_id=creator.id,
        name="Agent 每日一题",
        description="用轮播图讲清一个 Agent 工程知识点。",
        audience="准备 AI Agent 开发面试的初学者",
        skill_name="knowledge-to-carousel",
        selection_policy=OperationPolicy.APPROVAL,
        publish_policy=OperationPolicy.APPROVAL,
        replenish_threshold=5,
    )
    second_series = repository.create_series(
        series_id="rag-basics",
        creator_id=creator.id,
        name="RAG 每日一题",
        description="用轮播图讲清一个 RAG 知识点。",
        audience="准备 RAG 开发面试的初学者",
        skill_name="knowledge-to-carousel",
    )

    first = repository.add_topic(
        topic_id="agent-state",
        series_id=series.id,
        title="AgentState 和 Messages 有什么区别？",
        source=TopicSource.RESEARCH,
    )
    second = repository.add_topic(
        topic_id="tool-calling",
        series_id=series.id,
        title="Tool Calling 为什么需要 Schema？",
        source=TopicSource.RESEARCH,
    )
    third = repository.add_topic(
        topic_id="mcp-basics",
        series_id=series.id,
        title="MCP、Tool 和 Skill 有什么区别？",
        source=TopicSource.MANUAL,
    )
    outside = repository.add_topic(
        topic_id="hybrid-search",
        series_id=second_series.id,
        title="Dense 与 Sparse 为什么要融合？",
        source=TopicSource.RESEARCH,
    )

    assert [topic.id for topic in repository.list_topics(series.id)] == [
        first.id,
        second.id,
        third.id,
    ]
    reordered = repository.reorder_topics(series.id, [third.id, first.id, second.id])
    assert [(topic.id, topic.position) for topic in reordered] == [
        (third.id, 1),
        (first.id, 2),
        (second.id, 3),
    ]

    for invalid_order in (
        [third.id, first.id],
        [third.id, third.id, second.id],
        [third.id, first.id, outside.id],
    ):
        try:
            repository.reorder_topics(series.id, invalid_order)
        except ValueError:
            pass
        else:
            raise AssertionError(f"非法调序未被拒绝: {invalid_order}")

    database.close()

    restarted_database = Database(database_url)
    restarted_repository = ContentRepository(restarted_database)
    restored_creator = restarted_repository.get_creator(creator.id)
    restored_series = restarted_repository.get_series(series.id)
    restored_topics = restarted_repository.list_topics(series.id)
    assert restored_creator is not None
    assert restored_creator.display_name == "面试知识实验室"
    assert restored_series is not None
    assert restored_series.skill_name == "knowledge-to-carousel"
    assert restored_series.publish_policy is OperationPolicy.APPROVAL
    assert [topic.id for topic in restored_topics] == [third.id, first.id, second.id]
    assert restored_topics[0].source is TopicSource.MANUAL
    assert all(topic.status is TopicStatus.QUEUED for topic in restored_topics)
    restarted_database.close()

print("content_storage_smoke=passed creators=1 series=2 topics=4 restart=passed")
