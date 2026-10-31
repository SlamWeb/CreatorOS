"""Opt-in real DeepSeek/browser probe; copies a frozen list into isolated SQLite."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8898)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    root = args.root.resolve()
    if root.exists() or not root.is_relative_to(repo / "tmp"):
        parser.error("Use a fresh root below repository tmp")
    trace = json.loads(args.trace.read_text(encoding="utf-8"))
    rows = None
    for message in trace["context"]["messages"]:
        if message.get("role") != "tool":
            continue
        try:
            value = json.loads(message.get("content", ""))
        except ValueError:
            continue
        if value.get("action") == "list" and len(value.get("items", [])) == 36:
            rows = value["items"]
            break
    if rows is None:
        parser.error("Expected a frozen 36-topic list in the supplied trace")
    root.mkdir(parents=True)
    database_url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    os.environ["DATABASE_URL"] = database_url
    from creatoros.config import PROJECT_ROOT
    from creatoros.storage import Database, ContentRepository, TopicSource, upgrade_database
    from creatoros.runs import ContentRunService
    from creatoros.web.app import create_app
    from creatoros.web.server import StudioServer
    import uvicorn
    upgrade_database(database_url)
    database = Database(database_url)
    content = ContentRepository(database)
    content.create_creator(creator_id="index-live", display_name="[隔离验证] 工具上下文")
    content.create_series(series_id="index-series", creator_id="index-live", name="英语辨析",
                          description="词汇辨析", audience="", skill_name="")
    target = None
    for index, row in enumerate(rows):
        topic_id = f"index-topic-{index:02}"
        brief = row.get("brief") or ""
        if index == 0:
            target = topic_id
            brief += "\n最终排版约束：右下角保留一枚小蓝点。"
        content.add_topic(topic_id=topic_id, series_id="index-series", title=row["title"],
                          brief=brief, source=TopicSource.MANUAL)
    queries = [
        "列一下英语辨析栏目目前所有已入队选题的标题，不要新增或生产。",
        "再帮我想一组之前没写过的形近词，只给建议，别入队。",
        "把show / indicate / demonstrate / reveal那条的完整制作要求告诉我，尤其是最后一条约束，不要修改它。",
    ]
    (root / "setup.json").write_text(json.dumps({"creator_id": "index-live", "target_id": target,
        "topic_count": len(rows), "queries": queries}, ensure_ascii=False, indent=2), encoding="utf-8")
    runs = ContentRunService(database, output_root=root / "outputs")
    app = create_app(database=database, run_service=runs, chat_root=root / "chats",
                     studio_dist=PROJECT_ROOT / "web" / "dist")
    print(f"isolated_root={root}", flush=True)
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False,
            log_level="warning", timeout_graceful_shutdown=3)).run()
    finally:
        database.close()


if __name__ == "__main__":
    main()
