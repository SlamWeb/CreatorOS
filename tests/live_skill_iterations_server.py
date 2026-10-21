"""Opt-in real DS/Codex RSI host, restricted to a fresh ignored tmp workspace."""
import argparse
import json
import shutil
from pathlib import Path

import uvicorn

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService
from creatoros.integrations.skill_extraction import SkillExtractionService
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, Database, Series, TopicSource, upgrade_database
from creatoros.web import create_app
from creatoros.web.server import StudioServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8891)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()) or root.exists():
        raise ValueError("Use a fresh ignored tmp directory, never formal data")
    root.mkdir(parents=True)
    url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    upgrade_database(url)
    database = Database(url)
    catalog = ProducerSkillCatalog(root / "studio-producer-skills")
    runs = ContentRunService(database, output_root=root / "outputs")
    source = root / "source"
    (source / "assets").mkdir(parents=True)
    reference = args.reference.resolve(strict=True)
    shutil.copyfile(reference, source / "assets" / ("reference" + reference.suffix))
    (source / "SKILL.md").write_text(
        "---\nname: iteration-live-vocabulary\ndescription: 单页四格英语近义词教学\n"
        "creatoros-display-name: 四格词汇（隔离RSI验证）\n"
        "creatoros-output: social-content-pack.image-carousel\n---\n\n"
        "只制作一张2×2英语近义词漫画。每格一个词、音标、中英文释义及一个具体情境。"
        "保持四格文字清楚、不额外添加全图大标题。\n"
        f"实际查看并将assets/reference{reference.suffix}作为生图参考输入，借鉴角色画风及词汇教学呈现，"
        "不照搬参考图中的选题、单词数量或排版。\n", encoding="utf-8")
    skill = catalog.register_local(source, role="legacy_end_to_end")
    repository = ContentRepository(database)
    repository.create_creator(creator_id="iteration-live-account", display_name="[隔离验证] RSI账号")
    with database.session() as session:
        session.add(Series(id="iteration-live-series", creator_id="iteration-live-account", name="四格英语验证",
            description="", audience="", skill_name=skill["id"]))
    repository.add_topic(topic_id="iteration-live-topic", series_id="iteration-live-series",
        title="look / see / watch / stare：看见与观看",
        brief="单张2×2四格图。每格用一个词，英文简释+中文释义，场景体现区别。不要额外全图标题。",
        source=TopicSource.MANUAL)
    app = create_app(database=database, run_service=runs, chat_root=root / "chats",
        skill_install_service=SkillInstallService(catalog), skill_extraction_service=SkillExtractionService(catalog),
        eval_root=root / "eval")
    setup = dict(creator_id="iteration-live-account", series_id="iteration-live-series",
        topic_id="iteration-live-topic", skill_id=skill["id"], digest=skill["digest"],
        url=f"http://127.0.0.1:{args.port}")
    (root / "setup.json").write_text(json.dumps(setup, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(setup, ensure_ascii=False), flush=True)
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning",
            access_log=False, timeout_graceful_shutdown=3)).run()
    finally:
        database.close()


if __name__ == "__main__":
    main()
