"""Use the real installation receipt for browser and real DeepSeek acceptance."""
import argparse
import json
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx
import uvicorn

from creatoros.storage import Database, upgrade_database
from creatoros.runs import ContentRunService
from creatoros.web import create_app
from creatoros.web.server import StudioServer
from tests.agent_studio_support import serve


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    url = f"sqlite:///{(args.root / 'creatoros.db').resolve().as_posix()}"
    upgrade_database(url)
    db = Database(url)
    runs = ContentRunService(db, output_root=args.root / "outputs")
    app = create_app(database=db, run_service=runs)
    if args.serve:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=8893, log_level="warning")).run()
        return
    installed = json.loads((args.root / "result.json").read_text(encoding="utf-8"))
    with serve(app) as base, httpx.Client(base_url=base, timeout=20) as client:
        creator = client.post("/api/creators", json={"display_name": "Skill 接入验收（隔离）"}).json()
        series = client.post(f"/api/creators/{creator['id']}/series", json={"name": "每日知识轮播"}).json()
        # HTTP resubmission must find the existing result without another Codex invocation.
        duplicate = client.post("/api/producer-skills/install", json={"github_url": installed["github_url"]}).json()
        assert duplicate["id"] == installed["id"] and duplicate["status"] == "installed"
        doc = client.post("/api/agent/sessions", json={}).json()
        response = client.post(f"/api/agent/sessions/{doc['id']}/turns", json={
            "request_id": str(uuid4()), "expected_version": doc["version"],
            "text": "查询已安装的生产 Skill，告诉我有哪些版本，以及要怎么给栏目绑定。只查询，不安装、不绑定、不生成。"})
        assert response.status_code == 202, response.text
        deadline = monotonic() + 150
        while monotonic() < deadline:
            doc = client.get(f"/api/agent/sessions/{doc['id']}").json()
            if doc["status"] != "running":
                break
            sleep(1)
        assert doc["status"] == "idle", doc
        # Check persisted runtime messages, not just the model's verbal claim.
        message_file = args.root / "creatoros-agent-sessions" / doc["id"] / "messages.json"
        messages = json.loads(message_file.read_text(encoding="utf-8"))
        calls = [call["name"] for message in messages for call in message.get("tool_calls", [])]
        assert "list_producer_skills" in calls, calls
        assert not {"install_producer_skill", "start_content_run", "research_series_topics"}.intersection(calls), calls
        # Host confirmation binds the installed version; the model only queried it.
        endpoint = f"/api/series/{series['id']}/skill"
        body = {"skill_id": installed["skill"]["id"], "expected_skill_name": series["skill_name"]}
        bound = client.post(endpoint, json=body)
        assert bound.status_code == 200, bound.text
        assert client.get(f"/api/series/{series['id']}").json()["skill_name"] == body["skill_id"]
        # Replaying the obsolete confirmation must not overwrite the new binding.
        assert client.post(endpoint, json=body).status_code == 409
        evidence = {"series_id": series["id"], "job_id": installed["id"], "skill_id": installed["skill"]["id"],
                    "session_id": doc["id"], "agent": doc, "tool_calls": calls,
                    "binding_verified": True, "stale_confirmation_rejected": True}
        (args.root / "studio-result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"live_skill_studio=passed series={series['id']} session={doc['id']}", flush=True)
    db.close()


if __name__ == "__main__":
    main()
