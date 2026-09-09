"""Real DeepSeek selects from saved real Luna research, then browser can confirm."""
import argparse
import json
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx
import uvicorn

from creatoros.runs import ContentRunService
from creatoros.storage import Database, ContentRepository
from creatoros.web import create_app
from creatoros.web.server import StudioServer
from tests.agent_studio_support import serve


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--session", help="Replay validation of an already completed real session without another model call")
    args = parser.parse_args()
    db = Database(f"sqlite:///{(args.root / 'test.db').resolve().as_posix()}")
    app = create_app(database=db, run_service=ContentRunService(db, output_root=args.root / "outputs"))
    if args.serve:
        try:
            StudioServer(uvicorn.Config(app, host="127.0.0.1", port=8894, log_level="warning")).run()
        finally:
            db.close()
        return
    batch = json.loads((args.root / "result.json").read_text(encoding="utf-8"))
    with serve(app) as base, httpx.Client(base_url=base, timeout=20) as client:
        if args.session:
            doc = client.get(f"/api/agent/sessions/{args.session}").json()
        else:
            doc = client.post("/api/agent/sessions", json={}).json()
            response = client.post(f"/api/agent/sessions/{doc['id']}/turns", json={
                "request_id": str(uuid4()), "expected_version": doc["version"],
                "text": f"请读取已就绪调研批次 {batch['id']}，只选择第二个候选 c2，标题改成‘上下文不是越长越好’，保留原切入点和来源。请生成入队 Preview 链接供我确认，不要确认入队，不要重新调研，不要生产。"})
            assert response.status_code == 202, response.text
        deadline = monotonic() + 180
        while monotonic() < deadline:
            doc = client.get(f"/api/agent/sessions/{doc['id']}").json()
            if doc["status"] != "running":
                break
            sleep(1)
        assert doc["status"] == "idle", doc
        messages = json.loads((args.root / "test-agent-sessions" / doc["id"] / "messages.json").read_text(encoding="utf-8"))
        calls = [call for message in messages for call in message.get("tool_calls", [])]
        names = [c["name"] for c in calls]
        assert "get_topic_research" in names and "prepare_topic_selection" in names, names
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        assert not any(r.get("error") for r in results), results
        prepared = next(r for r in results if "operation_id" in r)
        operation = client.get(f"/api/operations/{prepared['operation_id']}").json()
        after = operation["preview"]["changes"][0]["after_topics"]
        assert len(after) == 1 and after[0]["title"] == "上下文不是越长越好"
        assert batch["candidates"][1]["angle"] in after[0]["brief"]
        assert all(s["url"] in after[0]["brief"] for s in batch["candidates"][1]["sources"])
        assert not {"start_content_run", "research_series_topics", "install_producer_skill"}.intersection(names), names
        assert not ContentRepository(db).list_topics(batch["series_id"])
        (args.root / "studio-result.json").write_text(json.dumps({"session": doc, "tool_calls": calls}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"live_research_studio=passed tools={names} queue=unchanged session={doc['id']}", flush=True)
    db.close()


if __name__ == "__main__":
    main()
