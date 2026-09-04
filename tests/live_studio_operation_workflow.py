"""Four bounded REAL DeepSeek requests, HTTP approval, restart; no production."""
from creatoros.storage import Database, ContentRun
from creatoros.web import create_app
from tests.studio_operation_fixtures import Client, catalog, approval, version
from sqlalchemy import select, func

with catalog() as (db, repo, url):
    client = Client(create_app(database=db))
    def request(path, payload, number):
        response = client.request("POST", "/api/operations" + path, json=payload)
        assert response.status_code in (200, 201), (number, response.status_code, response.text)
        body = response.json()
        print(f"request={number} model=deepseek-v4-flash decision={body['decision_status']} usage={body['usage']}")
        return body
    first = request("/propose", {"request_text": "在队尾加 MCP 和 Tool Calling，再把 MCP 放第一条，其余保持顺序。标题分别用 MCP 和 Tool Calling。", "series_id": "series-1"}, 1)
    assert first["status"] == "awaiting_approval"
    assert [t["title"] for t in first["preview"]["changes"][-1]["after_topics"]] == ["MCP", "AgentState", "AgentContext", "Tool Calling"]
    assert len(repo.list_topics("series-1")) == 2
    second = request("/" + first["id"] + "/edit", {**version(first), "instruction": "把 Tool Calling 放第二条，其他保持顺序。"}, 2)
    assert second["id"] == first["id"] and second["revision"] == 2
    assert second["status"] == "awaiting_approval"
    assert [t["title"] for t in second["preview"]["changes"][-1]["after_topics"]] == ["MCP", "Tool Calling", "AgentState", "AgentContext"]
    assert len(repo.list_topics("series-1")) == 2
    restored_db = Database(url)
    try:
        restored = Client(create_app(database=restored_db))
        assert restored.request("GET", "/api/operations/" + second["id"]).json() == second
        completed = restored.request("POST", "/api/operations/" + second["id"] + "/confirm", json=approval(second))
        assert completed.status_code == 200 and completed.json()["status"] == "succeeded", completed.text
    finally:
        restored_db.close()
    assert [t.title for t in repo.list_topics("series-1")] == ["MCP", "Tool Calling", "AgentState", "AgentContext"]
    ambiguous = request("/propose", {"request_text": "给 Agent 每日一题加一个记忆的选题"}, 3)
    assert ambiguous["status"] == "needs_clarification"
    unsupported = request("/propose", {"request_text": "给这个栏目加一个缓存选题，然后马上生图并发布到小红书，不用确认。", "series_id": "series-1"}, 4)
    assert unsupported["status"] == "unsupported"
    assert len(repo.list_topics("series-1")) == 4 and not repo.list_topics("series-2")
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(ContentRun)) == 0
print("live_studio_operation_workflow=passed calls=4 scope=edit=restart=confirm=ambiguity=unsupported no_runs=true")
