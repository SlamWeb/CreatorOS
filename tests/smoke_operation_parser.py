import json
from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.operations import (
    OperationParseError,
    OperationParseDecision,
    OperationPlan,
    build_operation_catalog,
    parse_operation_plan_response,
    parse_operation_decision_response,
)
from creatoros.storage import ContentRepository, Database, TopicSource, upgrade_database


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

    catalog = build_operation_catalog(repository)
    assert catalog == {
        "series": [
            {
                "series_id": "agent-series",
                "creator_id": "creator-1",
                "creator_name": "Creator One",
                "name": "Agent 每日一题",
                "is_active": True,
                "creator_active": True,
                "skill_name": "knowledge-to-carousel",
                "topics": [
                    {"topic_id": "state", "position": 1, "title": "AgentState"}
                ],
            }
        ]
    }
    content = json.dumps(
        {
            "schema_version": 1,
            "operations": [
                {
                    "action": "add_topics",
                    "series_id": "agent-series",
                    "topics": [{"topic_id": "mcp", "title": "MCP", "source": "manual"}],
                }
            ],
        },
        ensure_ascii=False,
    )
    plan = parse_operation_plan_response(content)
    assert isinstance(plan, OperationPlan)
    assert plan.operations[0].series_id == "agent-series"
    unsupported = parse_operation_decision_response(
        json.dumps(
            {
                "status": "unsupported",
                "plan": None,
                "message": "当前不支持删除栏目。",
            },
            ensure_ascii=False,
        )
    )
    assert isinstance(unsupported, OperationParseDecision)
    assert unsupported.plan is None

    for invalid in (None, "", "{}", '{"operations": [{"action": "unknown"}]}'):
        try:
            parse_operation_plan_response(invalid)
        except OperationParseError:
            pass
        else:
            raise AssertionError(f"非法模型输出未被拒绝：{invalid!r}")

    schema = OperationParseDecision.model_json_schema()
    assert "status" in schema["properties"]
    assert schema["additionalProperties"] is False
    database.close()

print("operation_parser_smoke=passed catalog=passed validation=passed")
