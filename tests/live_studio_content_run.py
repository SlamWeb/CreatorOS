"""One isolated real Codex ContentRun for S7; it never publishes content."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from creatoros.config import PROJECT_ROOT
from creatoros.runs import ContentRunRepository, ContentRunService
from creatoros.storage import ContentRepository, CreatorPlatform, Database, TopicSource, upgrade_database
from creatoros.web.app import create_app


def database_url(root: Path) -> str:
    return f"sqlite:///{(root / 'studio.db').as_posix()}"


def generate(topic: str) -> Path:
    root = (PROJECT_ROOT / "tmp" / f"s7-live-{datetime.now():%Y%m%d-%H%M%S}").resolve()
    root.mkdir(parents=True, exist_ok=False)
    url = database_url(root)
    upgrade_database(url)
    database = Database(url)
    try:
        content = ContentRepository(database)
        content.create_creator(creator_id="s7-live-lab", display_name="[S7 临时] Agent 知识实验室", platform=CreatorPlatform.XIAOHONGSHU)
        content.create_series(series_id="s7-agent-cards", creator_id="s7-live-lab", name="Agent 每日一题", description="用原创图片讲明白 Agent 工程概念", audience="准备 AI Agent 面试的开发者", skill_name="knowledge-to-carousel")
        content.add_topic(topic_id="s7-real-topic", series_id="s7-agent-cards", title=topic, source=TopicSource.MANUAL)
        service = ContentRunService(database, output_root=root / "outputs")
        content_run = service.create("s7-real-topic")
        result = service.execute(content_run.id)
        run_id = result.run_id
    finally:
        database.close()

    detail, usage = verify(root, run_id)
    revision = detail["revisions"][-1]
    print(
        "live_studio_content_run=passed "
        f"run={run_id} cards={len(revision['cards'])} input={usage.get('input_tokens', 0)} "
        f"cached={usage.get('cached_input_tokens', 0)} output={usage.get('output_tokens', 0)} root={root}"
    )
    return root


def verify(root: Path, run_id: str):
    reopened = Database(database_url(root))
    try:
        service = ContentRunService(reopened, output_root=root / "outputs")
        app = create_app(database=reopened, run_service=service)
        with TestClient(app) as client:
            detail = client.get(f"/api/runs/{run_id}").json()
            assert detail["status"] == "awaiting_approval"
            revision = detail["revisions"][-1]
            assert revision["review_digest"] and revision["cards"]
            for card in revision["cards"]:
                response = client.get(card["url"])
                assert response.status_code == 200 and response.headers["content-type"].startswith("image/")
            usage = revision["attempts"][-1]["usage"] or {}
    finally:
        reopened.close()
    return detail, usage


def resume(root: Path) -> None:
    root = root.resolve()
    database = Database(database_url(root))
    try:
        runs = ContentRunRepository(database).list_runs()
        if len(runs) != 1:
            raise RuntimeError(f"期望一个隔离 Run，实际为 {len(runs)} 个。")
        service = ContentRunService(database, output_root=root / "outputs")
        result = service.execute(runs[0].id)
        run_id = result.run_id
    finally:
        database.close()
    detail, usage = verify(root, run_id)
    print(
        "live_studio_content_run=passed resumed=true "
        f"run={run_id} cards={len(detail['revisions'][-1]['cards'])} input={usage.get('input_tokens', 0)} "
        f"cached={usage.get('cached_input_tokens', 0)} output={usage.get('output_tokens', 0)} root={root}"
    )


def serve(root: Path, port: int) -> None:
    root = root.resolve()
    database = Database(database_url(root))
    service = ContentRunService(database, output_root=root / "outputs")
    app = create_app(database=database, run_service=service)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="Agent 的 Tool Calling 为什么需要 Schema？")
    parser.add_argument("--review-root", type=Path)
    parser.add_argument("--resume-root", type=Path)
    parser.add_argument("--port", type=int, default=8879)
    args = parser.parse_args()
    if args.review_root:
        serve(args.review_root, args.port)
    elif args.resume_root:
        resume(args.resume_root)
    else:
        generate(args.topic)
