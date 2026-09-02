from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.operations import (
    OperationParseDecision,
    OperationParseResult,
    OperationPlan,
    PendingOperationService,
)
from creatoros.operations.cli import PendingOperationCLI
from creatoros.storage import ContentRepository, Database, TopicSource, upgrade_database
from creatoros.terminal import RichConsole


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
    plan = OperationPlan.model_validate(
        {
            "operations": [
                {
                    "action": "add_topics",
                    "series_id": "agent-series",
                    "topics": [{"topic_id": "mcp", "title": "MCP"}],
                }
            ]
        }
    )
    service = PendingOperationService(database, parser=None)
    pending = service.persist_proposal(
        "增加 MCP",
        OperationParseResult(
            decision=OperationParseDecision(status="ready", plan=plan),
            usage=None,
        ),
    )
    database.close()

    restarted_database = Database(database_url)
    restarted_service = PendingOperationService(restarted_database, parser=None)
    inputs = iter(("确认", "返回"))
    output = StringIO()
    cli = PendingOperationCLI(
        RichConsole(input_fn=lambda _prompt: next(inputs), output=output),
        restarted_service,
    )
    try:
        cli.run()
        rendered = output.getvalue()
        assert f"已恢复待处理计划 {pending.id[:8]}" in rendered
        assert "+ MCP" in rendered
        assert "计划已执行" in rendered
        assert [
            topic.id
            for topic in ContentRepository(restarted_database).list_topics("agent-series")
        ] == ["state", "mcp"]
    finally:
        restarted_database.close()

print("pending_operation_cli_smoke=passed resume=confirm")
