from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from creatoros.operations import (
    OperationConflictError,
    OperationExecutor,
    OperationPlan,
    OperationPlanError,
)
from creatoros.storage import ContentRepository, Database, TopicSource, upgrade_database


def expect_invalid(payload: dict) -> None:
    try:
        OperationPlan.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError(f"非法 OperationPlan 未被拒绝：{payload}")


with TemporaryDirectory() as temporary_directory:
    database_path = Path(temporary_directory) / "creatoros.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    repository = ContentRepository(database)
    repository.create_creator(creator_id="creator-1", display_name="Creator One")
    repository.create_series(
        series_id="agent-series",
        creator_id="creator-1",
        name="Agent 每日一题",
        description="Agent 知识轮播",
        audience="Agent 初学者",
        skill_name="knowledge-to-carousel",
    )
    repository.add_topic(
        topic_id="state",
        series_id="agent-series",
        title="AgentState",
        source=TopicSource.MANUAL,
    )
    repository.add_topic(
        topic_id="context",
        series_id="agent-series",
        title="AgentContext",
        source=TopicSource.MANUAL,
    )

    expect_invalid({"operations": []})
    expect_invalid({"operations": [{"action": "unknown", "series_id": "agent-series"}]})
    expect_invalid(
        {
            "operations": [
                {
                    "action": "add_topics",
                    "series_id": "agent-series",
                    "topics": [
                        {"topic_id": "same", "title": "A"},
                        {"topic_id": "same", "title": "B"},
                    ],
                }
            ]
        }
    )
    expect_invalid(
        {
            "operations": [
                {
                    "action": "reorder_topics",
                    "series_id": "agent-series",
                    "ordered_topic_ids": ["state", "state"],
                    "unexpected": True,
                }
            ]
        }
    )

    plan = OperationPlan.model_validate(
        {
            "operations": [
                {
                    "action": "add_topics",
                    "series_id": "agent-series",
                    "topics": [
                        {"topic_id": "tools", "title": "Tool Calling", "source": "research"},
                        {"topic_id": "mcp", "title": "MCP 与 Skill", "source": "manual"},
                    ],
                },
                {
                    "action": "reorder_topics",
                    "series_id": "agent-series",
                    "ordered_topic_ids": ["mcp", "state", "tools", "context"],
                },
            ]
        }
    )
    executor = OperationExecutor(repository)
    preview = executor.preview(plan)
    assert [topic.id for topic in repository.list_topics("agent-series")] == ["state", "context"]
    assert preview.changes[-1].after_order == ["mcp", "state", "tools", "context"]

    receipt = executor.execute(plan, preview.confirmation_token)
    assert receipt.applied_operations == 2
    assert receipt.topic_orders["agent-series"] == ["mcp", "state", "tools", "context"]
    assert [topic.id for topic in repository.list_topics("agent-series")] == [
        "mcp",
        "state",
        "tools",
        "context",
    ]

    stale_plan = OperationPlan.model_validate(
        {
            "operations": [
                {
                    "action": "add_topics",
                    "series_id": "agent-series",
                    "topics": [{"topic_id": "memory", "title": "Agent Memory"}],
                }
            ]
        }
    )
    stale_preview = executor.preview(stale_plan)
    repository.add_topic(
        topic_id="guard",
        series_id="agent-series",
        title="Agent Guard",
        source=TopicSource.MANUAL,
    )
    try:
        executor.execute(stale_plan, stale_preview.confirmation_token)
    except OperationConflictError:
        pass
    else:
        raise AssertionError("过期 confirmation token 未被拒绝。")
    assert repository.get_topic("memory") is None

    rollback_plan = OperationPlan.model_validate(
        {
            "operations": [
                {
                    "action": "add_topics",
                    "series_id": "agent-series",
                    "topics": [
                        {"topic_id": "rollback-good", "title": "Should Roll Back"},
                        {"topic_id": "rollback-blocked", "title": "Blocked By Database"},
                    ],
                }
            ]
        }
    )
    rollback_preview = executor.preview(rollback_plan)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TRIGGER reject_blocked_topic
                BEFORE INSERT ON topics
                WHEN NEW.id = 'rollback-blocked'
                BEGIN
                    SELECT RAISE(ABORT, 'blocked for rollback smoke');
                END
                """
            )
        )
    try:
        executor.execute(rollback_plan, rollback_preview.confirmation_token)
    except SQLAlchemyError:
        pass
    else:
        raise AssertionError("数据库拒绝第二项时整份计划应失败。")
    assert repository.get_topic("rollback-good") is None
    assert repository.get_topic("rollback-blocked") is None

    try:
        executor.preview(
            OperationPlan.model_validate(
                {
                    "operations": [
                        {
                            "action": "add_topics",
                            "series_id": "missing-series",
                            "topics": [{"topic_id": "missing", "title": "Missing"}],
                        }
                    ]
                }
            )
        )
    except OperationPlanError:
        pass
    else:
        raise AssertionError("未知 Series 未被拒绝。")

    database.close()

print("operation_plan_smoke=passed preview=readonly stale=blocked rollback=passed")
