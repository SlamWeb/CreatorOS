from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import text

from creatoros.ai import ModelUsage
from creatoros.operations import (
    OperationParseDecision,
    OperationParseResult,
    OperationPlan,
    PendingOperationRepository,
    PendingOperationService,
)
from creatoros.storage import (
    ContentRepository,
    Database,
    OperationEventType,
    PendingOperationStatus,
    TopicSource,
    upgrade_database,
)


def parse_result(plan: OperationPlan | None, *, status: str = "ready") -> OperationParseResult:
    return OperationParseResult(
        decision=OperationParseDecision(
            status=status,
            plan=plan,
            message=None if status == "ready" else "需要更多信息",
        ),
        usage=ModelUsage(100, 20, 120),
    )


def add_plan(topic_id: str, title: str) -> OperationPlan:
    return OperationPlan.model_validate(
        {
            "operations": [
                {
                    "action": "add_topics",
                    "series_id": "agent-series",
                    "topics": [{"topic_id": topic_id, "title": title}],
                }
            ]
        }
    )


with TemporaryDirectory() as temporary_directory:
    database_path = Path(temporary_directory) / "creatoros.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    content = ContentRepository(database)
    content.create_creator(creator_id="creator-1", display_name="Creator One")
    content.create_series(
        series_id="agent-series",
        creator_id="creator-1",
        name="Agent 每日一题",
        description="Agent 知识轮播",
        audience="Agent 初学者",
        skill_name="knowledge-to-carousel",
    )
    content.add_topic(
        topic_id="state",
        series_id="agent-series",
        title="AgentState",
        source=TopicSource.MANUAL,
    )

    service = PendingOperationService(database, parser=None)
    pending = service.persist_proposal("增加 MCP", parse_result(add_plan("mcp", "MCP")))
    assert pending.status is PendingOperationStatus.AWAITING_APPROVAL
    assert [topic.id for topic in content.list_topics("agent-series")] == ["state"]
    database.close()

    restarted_database = Database(database_url)
    restarted_service = PendingOperationService(restarted_database, parser=None)
    assert [item.id for item in restarted_service.list_actionable()] == [pending.id]
    succeeded = restarted_service.confirm(pending.id)
    assert succeeded.status is PendingOperationStatus.SUCCEEDED
    restarted_content = ContentRepository(restarted_database)
    assert [topic.id for topic in restarted_content.list_topics("agent-series")] == [
        "state",
        "mcp",
    ]
    restarted_service.confirm(pending.id)
    assert [topic.id for topic in restarted_content.list_topics("agent-series")].count("mcp") == 1
    events = PendingOperationRepository(restarted_database).list_events(pending.id)
    assert [event.event_type for event in events] == [
        OperationEventType.PROPOSED,
        OperationEventType.CONFIRMED,
        OperationEventType.SUCCEEDED,
    ]

    cancellable = restarted_service.persist_proposal(
        "增加 Memory",
        parse_result(add_plan("memory", "Agent Memory")),
    )
    cancelled = restarted_service.cancel(cancellable.id)
    assert cancelled.status is PendingOperationStatus.CANCELLED
    assert restarted_content.get_topic("memory") is None

    clarification = restarted_service.persist_proposal(
        "放到前面",
        parse_result(None, status="needs_clarification"),
    )
    edited = restarted_service.persist_edit(
        clarification.id,
        "给 Agent 栏目增加 Context Engineering",
        parse_result(add_plan("context-engineering", "Context Engineering")),
        expected_revision=1,
    )
    assert edited.revision == 2
    assert edited.status is PendingOperationStatus.AWAITING_APPROVAL
    assert "修改要求" in edited.request_text

    stale = restarted_service.persist_proposal(
        "增加 Guard",
        parse_result(add_plan("guard", "Agent Guard")),
    )
    restarted_content.add_topic(
        topic_id="external",
        series_id="agent-series",
        title="External Change",
        source=TopicSource.MANUAL,
    )
    stale_result = restarted_service.confirm(stale.id)
    assert stale_result.status is PendingOperationStatus.STALE
    assert restarted_content.get_topic("guard") is None

    failed = restarted_service.persist_proposal(
        "增加两个回滚选题",
        parse_result(
            OperationPlan.model_validate(
                {
                    "operations": [
                        {
                            "action": "add_topics",
                            "series_id": "agent-series",
                            "topics": [
                                {"topic_id": "rollback-good", "title": "Good"},
                                {"topic_id": "rollback-blocked", "title": "Blocked"},
                            ],
                        }
                    ]
                }
            )
        ),
    )
    with restarted_database.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TRIGGER reject_pending_blocked_topic
                BEFORE INSERT ON topics
                WHEN NEW.id = 'rollback-blocked'
                BEGIN
                    SELECT RAISE(ABORT, 'blocked for pending rollback smoke');
                END
                """
            )
        )
    failed_result = restarted_service.confirm(failed.id)
    assert failed_result.status is PendingOperationStatus.FAILED
    assert restarted_content.get_topic("rollback-good") is None
    assert restarted_content.get_topic("rollback-blocked") is None
    failed_events = PendingOperationRepository(restarted_database).list_events(failed.id)
    assert [event.event_type for event in failed_events] == [
        OperationEventType.PROPOSED,
        OperationEventType.CONFIRMED,
        OperationEventType.FAILED,
    ]
    restarted_database.close()

print("pending_operation_service_smoke=passed restart=confirm edit=passed rollback=passed")
