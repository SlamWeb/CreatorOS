"""Opt-in paid DS/Codex fixture. Fresh ignored roots only; never formal data."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    root = args.root.resolve()
    repo = Path(__file__).resolve().parents[1]
    if not root.is_relative_to(repo / "tmp") or root.exists():
        parser.error("Choose a new root below repository tmp")
    root.mkdir(parents=True)
    database_url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    os.environ["DATABASE_URL"] = database_url
    os.environ["CREATOROS_PRODUCTION_CONCURRENCY"] = "2"
    import uvicorn
    from creatoros.storage import Database, ContentRepository, TopicSource, upgrade_database
    from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
    from creatoros.runs import ContentRunService
    from creatoros.web.app import create_app
    from creatoros.web.server import StudioServer
    upgrade_database(database_url)
    database = Database(database_url)
    catalog = ProducerSkillCatalog(skills_root_for(database))
    skills = []
    for name, label, color in (("task-dawn", "清晨像素", "冷蓝色天空"), ("task-dusk", "黄昏像素", "暖橘色天空")):
        source = root / name
        source.mkdir()
        (source / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: 隔离并行制作验收，一幅极简像素场景。\n"
            f"creatoros-display-name: {label}\ncreatoros-output: social-content-pack.image-carousel\n---\n"
            f"为每个制作任务只生成一张3:4竖版像素插画，{color}，一个穿白上衣的棕发小人站在地面。"
            "保留大量留白，不画文字。只完成当前任务要求，不生成别的任务。使用生图工具，不用代码绘制代替。"
            "这是隔离链路验收，不需要反复优化，不联网。\n", encoding="utf-8")
        skills.append(catalog.register_local(source, role="legacy_end_to_end"))
    content = ContentRepository(database)
    content.create_creator(creator_id="tasks-live", display_name="[隔离验证] 多任务制作")
    content.create_series(series_id="tasks-series", creator_id="tasks-live", name="一天的两个世界",
                          description="", audience="", skill_name=skills[0]["id"])
    content.create_series(series_id="tasks-alternate", creator_id="tasks-live", name="黄昏备用栏目",
                          description="", audience="", skill_name=skills[1]["id"])
    content.add_topic(topic_id="tasks-topic", series_id="tasks-series", title="同一个人，从清晨到黄昏",
                      brief="同一件最终作品包含清晨与黄昏两张图。", source=TopicSource.MANUAL)
    setup = dict(creator_id="tasks-live", series_id="tasks-series", topic_id="tasks-topic", skills=skills)
    (root / "setup.json").write_text(json.dumps(setup, ensure_ascii=False, indent=2), encoding="utf-8")
    runs = ContentRunService(database, output_root=root / "outputs")
    app = create_app(database=database, run_service=runs, studio_dist=args.dist.resolve(), chat_root=root / "chats")
    print(json.dumps(dict(root=str(root), port=args.port, skill_ids=[skill["id"] for skill in skills])), flush=True)
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port,
            log_level="warning", access_log=False, timeout_graceful_shutdown=3)).run()
    finally:
        database.close()


if __name__ == "__main__":
    main()
