"""Real DeepSeek + loopback HTTP, synthetic saved candidates, isolated SQLite."""
import json
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4
from unittest.mock import patch

import httpx

from creatoros.storage import Database, ContentRepository, upgrade_database
from creatoros.runs import ContentRunService
from creatoros.web import create_app
from tests.agent_studio_support import serve
from tests.smoke_topic_research import seed_batch


def main():
    root = Path("tmp") / ("topic-library-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True)
    url = f"sqlite:///{(root.resolve() / 'test.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)
    app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs"))
    # Restrict this probe independently of model compliance: no paid research/production.
    with patch("creatoros.web.chat.STUDIO_TOOLS", frozenset({"list_series_topics", "get_topic_research", "prepare_topic_selection"})), serve(app) as base, httpx.Client(base_url=base, timeout=20) as client:
        creator = client.post("/api/creators", json={"display_name": "选题库隔离验收"}).json()
        sid = client.post(f"/api/creators/{creator['id']}/series", json={"name": "AI 图解"}).json()["id"]
        batch = seed_batch(app.state.topic_research, sid, uuid4().hex)
        doc = client.post("/api/agent/sessions", json={}).json()
        response = client.post(f"/api/agent/sessions/{doc['id']}/turns", json={
            "request_id": str(uuid4()), "expected_version": doc["version"],
            "text": f"查询栏目 {sid} 的待选选题列表，把列表第二条准备为入队预览，保留标题和来源。只预览，不确认，不生产，不重新调研。"})
        assert response.status_code == 202, response.text
        deadline = monotonic() + 180
        while monotonic() < deadline:
            doc = client.get(f"/api/agent/sessions/{doc['id']}").json()
            if doc["status"] != "running":
                break
            sleep(1)
        messages = json.loads((root / "test-agent-sessions" / doc["id"] / "messages.json").read_text(encoding="utf-8"))
        names = [c["name"] for m in messages for c in m.get("tool_calls", [])]
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        (root / "report.json").write_text(json.dumps({"session": doc, "tools": names}, ensure_ascii=False, indent=2), encoding="utf-8")
        assert doc["status"] == "idle" and "list_series_topics" in names, names
        prepared = next(r for r in results if "operation_id" in r)
        operation = client.get(f"/api/operations/{prepared['operation_id']}").json()
        topics = operation["preview"]["changes"][0]["after_topics"]
        assert len(topics) == 1 and topics[0]["title"] == batch["candidates"][1]["title"]
        assert batch["candidates"][1]["sources"][0]["url"] in topics[0]["brief"]
        assert not ContentRepository(db).list_topics(sid)
        assert not {"start_content_run", "research_series_topics"}.intersection(names)
        print(f"live_topic_library=passed tools={names} report={root / 'report.json'}")
    db.close()


if __name__ == "__main__":
    main()
