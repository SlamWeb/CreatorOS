"""Two real DeepSeek read-only queries against isolated account data."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from creatoros.storage import ContentRepository, ContentRun
from creatoros.session.context_trace import read_trace
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests.smoke_account_sessions import wait_live
from tests.studio_review_fixtures import make_fixture


def main():
    with TemporaryDirectory(prefix="creator-context-live-") as temporary:
        root = Path(temporary)
        db, runs, producer, _ = make_fixture(root)
        repo = ContentRepository(db)
        repo.create_creator(creator_id="other-account", display_name="OTHER_ACCOUNT_SENTINEL")
        app = create_app(database=db, run_service=runs, chat_root=root / "chats")
        try:
            with serve(app) as url, httpx.Client(base_url=url, timeout=20, trust_env=False) as client:
                session = client.post("/api/agent/sessions", json={"creator_id": "review-lab"}).json()
                prompts = [
                    "只读：根据宿主提供的当前账号目录，告诉我绑定账号名称、栏目的名称、受众和绑定Skill的name与description。"
                    "不要读取Skill正文，不要调研、生产或修改数据。",
                    "我明确要求查看刚才绑定的Skill写法：请读取其SKILL.md前200个字符，告诉我name和description。"
                    "只读取这一页，不读取全文，不调用调研或生产，不修改任何数据。",
                ]
                turns = []
                for prompt in prompts:
                    response = client.post(f"/api/agent/sessions/{session['id']}/turns", json={
                        "request_id": str(uuid4()), "text": prompt, "expected_version": session["version"]})
                    assert response.status_code == 202, response.text
                    session = wait_live(client, session["id"])
                    assert session["status"] == "idle", session.get("error")
                    turns.append(session)
                ledger_path = root / "chats" / session["id"] / "messages.json"
                messages = json.loads(ledger_path.read_text(encoding="utf-8"))
                calls = [call for message in messages for call in message.get("tool_calls", [])]
                allowed = {"list_creators", "list_creator_series", "list_series_topics",
                           "list_producer_skills", "get_producer_skill"}
                assert all(call["name"] in allowed for call in calls), calls
                assert any(call["name"] == "get_producer_skill" for call in calls), calls
                answers = "\n".join(m.get("content") or "" for m in messages if m["role"] == "assistant")
                assert "知识实验室" in answers and "把 Agent 讲明白" in answers, answers
                assert "knowledge-to-carousel" in answers, answers
                assert "OTHER_ACCOUNT_SENTINEL" not in json.dumps(messages, ensure_ascii=False)
                assert "creator_context" not in json.dumps(messages, ensure_ascii=False)
                rows = [r for r in read_trace(ledger_path, limit=100)["items"]
                        if r["event"] == "finished" and r["request_kind"] == "main"]
                assert rows and all(r["account_context"]["creator"]["id"] == "review-lab" for r in rows)
                assert all(r["estimated_parts"]["account_context"] > 0 for r in rows)
                assert all(sum(r["estimated_parts"].values()) == r["estimated_input_tokens"] for r in rows)
                assert "OTHER_ACCOUNT_SENTINEL" not in json.dumps(rows, ensure_ascii=False)
                assert producer.calls == 0
                with db.session() as database_session:
                    assert database_session.scalar(select(func.count()).select_from(ContentRun)) == 0
                print(json.dumps({"real_deepseek": True, "queries": 2, "main_requests": len(rows),
                    "tools": [c["name"] for c in calls], "account_tree_tokens": [
                        r["estimated_parts"]["account_context"] for r in rows],
                    "usage": [r["usage"] for r in rows], "production_calls": producer.calls}, ensure_ascii=False))
        finally:
            db.close()
    print("account_context_live=passed isolated_db metadata=automatic skill_body=explicit")


if __name__ == "__main__":
    main()
