"""Real DeepSeek Agent → HTTP → isolated ContentRun → real Codex. No publishing."""
from datetime import datetime
import json
import os
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

from creatoros.agent.loop import run_agent
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.config import PROJECT_ROOT
from creatoros.integrations.studio import StudioClient
from creatoros.runs import ContentRunService
from creatoros.session import snapshot
from creatoros.storage import ContentRepository, CreatorPlatform, Database, TopicSource, upgrade_database
from creatoros.terminal import Console
from creatoros.web.app import create_app
from tests.agent_studio_support import serve


def main():
    root = PROJECT_ROOT / "tmp" / f"agent-studio-live-{datetime.now():%Y%m%d-%H%M%S}"
    root.mkdir(parents=True)
    url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)
    repo = ContentRepository(db)
    repo.create_creator(creator_id="interview-lab", display_name="AI 面试实验室", platform=CreatorPlatform.XIAOHONGSHU)
    repo.create_series(series_id="agent-daily", creator_id="interview-lab", name="AI 面试", audience="零基础开发者",
                       description="简洁的两张图知识栏目", skill_name="knowledge-to-carousel")
    repo.add_topic(topic_id="mcp-origin", series_id="agent-daily", title="MCP 为什么出现", source=TopicSource.MANUAL,
                   brief="本次只做两张原创图片：一张用插座比喻解释动机，一张解释 host/client/server；准确说明协议不保证工具正确或自动授权。")
    service = ContentRunService(db, output_root=root / "outputs")
    app = create_app(database=db, run_service=service, studio_dist=PROJECT_ROOT / "web" / "dist")
    provider = DeepSeekProvider(api_key=os.environ["DEEPSEEK_API_KEY"], timeout_seconds=90, max_retries=0)
    events = []
    try:
        with serve(app) as base, patch.dict(os.environ, {"CREATOROS_STUDIO_URL": base}), \
                patch.object(snapshot, "SESSION_FILE", root / "agent-session.json"):
            print(f"live_root={root} studio={base}", flush=True)
            prompts = iter(["帮我把 AI 面试实验室账号下 AI 面试栏目里的 MCP 那篇做出来。提交后告诉我在哪里看进度就行。", "/menu"])
            with (root / "agent-output.txt").open("w", encoding="utf-8") as output:
                run_agent(provider, max_turns=10, console=Console(input_fn=lambda _: next(prompts), output=output),
                          on_agent_event=lambda event: events.append({"kind": event.kind, "data": event.data}))
            (root / "agent-events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
            calls = [event["data"]["name"] for event in events if event["kind"] == "tool_call"]
            assert "start_content_run" in calls, calls
            assert "produce_content_pack" not in calls
            assert "list_series_topics" in calls, calls
            print(f"agent_calls={calls}", flush=True)
            client = StudioClient(base)
            try:
                runs = client.request("GET", "/api/runs")["items"]
                assert len(runs) == 1 and runs[0]["topic_id"] == "mcp-origin", runs
                run_id = runs[0]["id"]
                (root / "review.json").write_text(json.dumps({"run_id": run_id, "database_url": url}), encoding="utf-8")
                print(f"agent_returned run={run_id} status={runs[0]['status']}", flush=True)
                deadline = monotonic() + 1800
                while monotonic() < deadline:
                    detail = client.get_run(run_id)
                    print(f"production_status={detail['status']} thread={detail['producer_thread_id']}", flush=True)
                    if detail["status"] not in {"queued", "producing", "validating"}:
                        break
                    sleep(20)
                assert detail["status"] == "awaiting_approval", detail.get("error_message") or detail["status"]
                revision = detail["revisions"][-1]
                assert revision["cards"] and revision["review_digest"]
                for card in revision["cards"]:
                    response = client.client.get(base + card["url"])
                    assert response.status_code == 200
                assert not client.start("mcp-origin")["accepted"]
                assert len(client.get_run(run_id)["revisions"][0]["attempts"]) == 1
                report = {"run_id": run_id, "cards": len(revision["cards"]),
                          "codex_usage": revision["attempts"][-1]["usage"],
                          "agent_usage": [e["data"] for e in events if e["kind"] == "model_usage"],
                          "calls": calls, "status": detail["status"]}
                (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print("live_agent_studio=passed " + json.dumps(report, ensure_ascii=False), flush=True)
            finally:
                client.close()
    finally:
        provider.client.close()
        db.close()


if __name__ == "__main__":
    main()
