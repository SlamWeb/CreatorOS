import os
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv

from creatoros.ai import DeepSeekProvider
from creatoros.operations import OperationExecutor, OperationPlanParser
from creatoros.storage import ContentRepository, Database, TopicSource, upgrade_database


load_dotenv()
api_key = os.getenv("DEEPSEEK_API_KEY")
if not api_key:
    raise RuntimeError("缺少 DEEPSEEK_API_KEY。")

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

    parser = OperationPlanParser(DeepSeekProvider(api_key=api_key), repository)
    result = parser.parse(
        "给 Agent 每日一题增加 MCP 和 Tool Calling 两个选题，"
        "然后把 MCP 放到第一条，其他原有选题保持相对顺序。"
    )
    assert result.decision.status == "ready"
    assert result.plan is not None
    plan = result.plan
    executor = OperationExecutor(repository)
    preview = executor.preview(plan)
    assert [topic.id for topic in repository.list_topics("agent-series")] == [
        "state",
        "context",
    ]

    added = {
        topic.topic_id: topic.title
        for operation in plan.operations
        if operation.action == "add_topics"
        for topic in operation.topics
    }
    assert len(added) == 2
    mcp_topic_id = next(
        topic_id for topic_id, title in added.items() if "MCP" in title.upper()
    )
    assert preview.changes[-1].after_order[0] == mcp_topic_id
    assert preview.changes[-1].after_order.index("state") < preview.changes[-1].after_order.index(
        "context"
    )
    receipt = executor.execute(plan, preview.confirmation_token)
    assert receipt.topic_orders["agent-series"][0] == mcp_topic_id
    assert result.usage is not None
    unsupported = parser.parse("删除 Agent 每日一题整个栏目。")
    assert unsupported.decision.status == "unsupported"
    assert unsupported.plan is None
    assert unsupported.decision.message
    database.close()

print("live_operation_parser=passed")
print(f"operations={len(plan.operations)}")
print(f"unsupported_status={unsupported.decision.status}")
print(f"input_tokens={result.usage.input_tokens}")
print(f"output_tokens={result.usage.output_tokens}")
