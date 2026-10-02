"""Explicit one-image SDK trial; isolated DB/output, no publishing.

python -m tests.live_native_production init --directory tmp/native-production-trial
python -m tests.live_native_production produce --directory tmp/native-production-trial
python -m tests.live_native_production serve --directory tmp/native-production-trial --port 8876
"""
import argparse
import json
from pathlib import Path

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, CreatorPlatform, Database, TopicSource, upgrade_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "produce", "serve", "status"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8876)
    args = parser.parse_args()
    root = args.directory.resolve()
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()):
        parser.error("试产目录必须在项目 tmp 内，避免触碰正式库。")
    metadata = root / "trial.json"
    if args.action == "init":
        if root.exists():
            parser.error("目录已存在；使用新目录或继续已有试产。")
        root.mkdir(parents=True)
    elif not metadata.is_file():
        parser.error("先 init。")
    url = f"sqlite:///{(root / 'trial.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)
    runs = ContentRunService(db, output_root=root / "outputs")
    if args.action == "init":
        skill = ProducerSkillCatalog(skills_root_for(db)).register_local(
            PROJECT_ROOT / "production-skills/english-word-scenes", role="production",
            source_note="用户六格英语参考图的可复用方法；隔离一图试产")
        content = ContentRepository(db)
        content.create_creator(creator_id="trial-creator", display_name="英语情景漫画·隔离试产",
                               platform=CreatorPlatform.XIAOHONGSHU)
        content.create_series(series_id="trial-series", creator_id="trial-creator", name="看图辨词",
                              description="用具体生活场景区分英语易混词。", audience="英语初学者", skill_name=skill["id"])
        content.add_topic(topic_id="trial-topic", series_id="trial-series", source=TopicSource.MANUAL,
            title="look / see / watch / stare / glance / gaze：六种看有什么不同？",
            brief="只生成 1 张竖版 2×3 六格英语情景知识图。查证这六个词的常见视觉义，"
                  "每格包含英文词、统一美式 IPA、极短中文差异说明和能表达词义的场景。"
                  "对象是英语初学者，内容准确、对比清楚。按选中的 Skill 执行。"
                  "一次生图即可，不自动重画；记录有问题的地方供人工判断。不发布。")
        run = runs.create("trial-topic")
        metadata.write_text(json.dumps({"run_id": run.id, "skill_id": skill["id"]}, indent=2), encoding="utf-8")
    saved = json.loads(metadata.read_text(encoding="utf-8"))
    print(json.dumps({"directory": str(root), **saved}, ensure_ascii=False), flush=True)
    if args.action == "produce":
        result = runs.execute(saved["run_id"])
        (root / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(result.model_dump_json(indent=2), flush=True)
    elif args.action == "serve":
        import uvicorn
        from creatoros.web.app import create_app
        from creatoros.web.launcher import ensure_studio_build
        from creatoros.web.server import StudioServer
        app = create_app(database=db, run_service=runs, studio_dist=ensure_studio_build(PROJECT_ROOT),
                         chat_root=root / "chats")
        print(f"http://127.0.0.1:{args.port}/runs/{saved['run_id']}", flush=True)
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port)).run()
    else:
        run = runs.get(saved["run_id"])
        print(f"status={run.status.value}", flush=True)


if __name__ == "__main__":
    main()
