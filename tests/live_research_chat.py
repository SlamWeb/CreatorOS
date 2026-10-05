"""Opt-in real DeepSeek + Codex online research in isolated SQLite/HTTP, no images or queue writes."""
import argparse
import json
import os
from pathlib import Path
from tempfile import mkdtemp
from time import monotonic, sleep
from uuid import uuid4

import httpx
import uvicorn

from creatoros.integrations.producer_skills import ProducerSkillCatalog, inherit_copy_permissions, skills_root_for
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, CreatorPlatform, Database, upgrade_database
from creatoros.web.app import create_app
from creatoros.web.server import StudioServer
from tests.agent_studio_support import serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Use real model quota and local Codex login.")
    parser.add_argument("--serve", type=Path, help="Inspect existing isolated evidence on 8896 without another model request.")
    args = parser.parse_args()
    if args.serve:
        root = args.serve.resolve()
        if not (root / "result.json").is_file() or not (root / "test.db").is_file():
            parser.error("--serve requires an existing probe directory with result.json and test.db")
        db = Database(f"sqlite:///{(root / 'test.db').as_posix()}")
        app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs"),
                         chat_root=root / "chats")
        try:
            StudioServer(uvicorn.Config(app, host="127.0.0.1", port=8896, log_level="warning")).run()
        finally:
            db.close()
        return
    if not args.run:
        parser.error("Requires --run; creates only isolated evidence, never production data.")
    root = Path(mkdtemp(prefix="research-chat-live-", dir=Path("tmp").resolve()))
    inherit_copy_permissions(root)
    url = f"sqlite:///{(root / 'test.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)
    repo = ContentRepository(db)
    repo.create_creator(creator_id="english-probe", display_name="[隔离验收] 英语词汇",
                        platform=CreatorPlatform.XIAOHONGSHU)
    repo.create_series(series_id="synonym-probe", creator_id="english-probe", name="英语近义词辨析",
        description="用具体生活情境和中英双语例子辨析容易混淆的近义词；不夸大难度或热度。",
        audience="英语初学者", skill_name="knowledge-to-carousel")
    research = TopicResearchService(db, ProducerSkillCatalog(skills_root_for(db)))
    # Simulate the user's Studio shell: no Codex on PATH. Keep other tooling intact.
    os.environ["PATH"] = os.pathsep.join(part for part in os.environ.get("PATH", "").split(os.pathsep)
        if not any(word in part.lower() for word in ("codex", "openai")))
    app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs"),
        chat_root=root / "chats", topic_research_service=research)
    started = monotonic()
    print(f"probe_directory={root}", flush=True)
    try:
        with serve(app) as base, httpx.Client(base_url=base, timeout=20, trust_env=False) as client:
            assert client.get("/api/health").json()["codex_available"]
            session = client.post("/api/agent/sessions", json={"creator_id": "english-probe"}).json()
            response = client.post(f"/api/agent/sessions/{session['id']}/turns", json={
                "request_id": str(uuid4()), "expected_version": session["version"],
                "text": "给英语近义词辨析栏目联网调研1组选题（1组易混淆单词），使用权威词典来源，给出具体切入点。只调研并在这个对话里告诉我最终结果，不入队、不生产。"})
            assert response.status_code == 202, response.text
            observed = []
            last = None
            while monotonic() - started < 620:
                session = client.get(f"/api/agent/sessions/{session['id']}").json()
                rows = [e for e in session["entries"] if e.get("name") in {"research_series_topics", "get_topic_research"}]
                if rows:
                    view = rows[-1].get("research") or {}
                    state = (view.get("status"), (view.get("progress") or {}).get("last_activity_at"))
                    if state != last:
                        observed.append({"elapsed_seconds": round(monotonic() - started, 1), **view})
                        print(f"chat={session['status']} research={state[0]} public_events={len((view.get('progress') or {}).get('events', []))}", flush=True)
                        last = state
                if session["status"] != "running":
                    break
                sleep(1)
            (root / "result.json").write_text(json.dumps({"session": session, "observed": observed,
                "elapsed_seconds": round(monotonic() - started, 1)}, ensure_ascii=False, indent=2), encoding="utf-8")
            assert session["status"] == "idle", f"chat={session['status']} see {root}"
            ledger = json.loads((root / "chats" / session["id"] / "messages.json").read_text(encoding="utf-8"))
            calls = [call for message in ledger for call in message.get("tool_calls", [])]
            names = [call["name"] for call in calls]
            assert names.count("research_series_topics") == 1, names
            assert not {"queue_topics", "start_content_run", "prepare_topic_selection"}.intersection(names)
            tool = next(e for e in session["entries"] if e.get("name") == "research_series_topics")
            batch = research.get(tool["research"]["id"])
            assert batch["status"] == "ready", f"{batch.get('error_type')}: {batch.get('error')}"
            assert len(batch["candidates"]) == 1 and batch["candidates"][0]["sources"]
            assert any(x.get("progress", {}).get("events") for x in observed)
            assert not repo.list_topics("synonym-probe")
            print(f"live_research_chat=passed seconds={round(monotonic()-started, 1)} candidates=1 queue=unchanged", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
