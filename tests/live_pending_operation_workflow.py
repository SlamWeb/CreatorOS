import os
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv

from creatoros.ai import DeepSeekProvider
from creatoros.operations import OperationPlanParser, PendingOperationService
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
    for topic_id, title in (("state", "AgentState"), ("context", "AgentContext")):
        content.add_topic(
            topic_id=topic_id,
            series_id="agent-series",
            title=title,
            source=TopicSource.MANUAL,
        )

    parser = OperationPlanParser(DeepSeekProvider(api_key=api_key), content)
    service = PendingOperationService(database, parser)
    pending = service.propose("给 Agent 每日一题增加 MCP 和 Tool Calling 两个选题。")
    assert pending.status.value == "awaiting_approval"
    assert [topic.id for topic in content.list_topics("agent-series")] == ["state", "context"]
    operation_id = pending.id
    database.close()

    restarted = Database(database_url)
    try:
        restarted_content = ContentRepository(restarted)
        restarted_parser = OperationPlanParser(
            DeepSeekProvider(api_key=api_key),
            restarted_content,
        )
        restarted_service = PendingOperationService(restarted, restarted_parser)
        edited = restarted_service.edit(
            operation_id,
            "把 Tool Calling 放第一条、MCP 放第二条，原有选题保持相对顺序。",
            expected_version=restarted_service.get(operation_id).version,
            expected_revision=restarted_service.get(operation_id).revision,
        )
        assert edited.revision == 2
        assert [topic.id for topic in restarted_content.list_topics("agent-series")] == [
            "state",
            "context",
        ]
        completed = restarted_service.confirm(operation_id, expected_version=edited.version, expected_revision=edited.revision, confirmation_token=edited.confirmation_token)
        titles = [topic.title for topic in restarted_content.list_topics("agent-series")]
        assert completed.status.value == "succeeded"
        assert titles[:2] == ["Tool Calling", "MCP"]
        assert titles.index("AgentState") < titles.index("AgentContext")
    finally:
        restarted.close()

print("live_pending_operation_workflow=passed")
print(f"operation_id={operation_id[:8]} revision={edited.revision}")
print("before_confirm=readonly restart=passed final_order=passed")
