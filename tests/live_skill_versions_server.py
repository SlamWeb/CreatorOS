"""Opt-in real Codex version trial; never writes the formal catalog or database."""
import argparse
import json
import shutil
from pathlib import Path

import uvicorn
from creatoros.config import PROJECT_ROOT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService
from creatoros.integrations.skill_extraction import SkillExtractionService
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, Database, Series, upgrade_database
from creatoros.web.app import create_app
from creatoros.web.server import StudioServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()):
        raise ValueError("Use a fresh ignored tmp root")
    if args.resume and not (root / "setup.json").is_file():
        raise ValueError("Resume requires original setup")
    root.mkdir(parents=True, exist_ok=args.resume)
    url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)

    def denied(*_args, **_kwargs):
        raise AssertionError("Only explicitly submitted Skill version trials are authorized")

    class Forbidden:
        research = denied
        preflight = denied
        __getattr__ = lambda self, name: denied

    # Match normal skills_root_for(database) so queued production snapshots can be checked too.
    catalog = ProducerSkillCatalog(root / "studio-producer-skills")
    extractions = SkillExtractionService(catalog)
    runs = ContentRunService(db, output_root=root / "outputs", producer_factory=denied)
    app = create_app(database=db, run_service=runs, chat_root=root / "chats",
        skill_install_service=SkillInstallService(catalog, installer=Forbidden()),
        skill_extraction_service=extractions, topic_research_service=TopicResearchService(db, catalog, researcher=Forbidden()),
        publication_client=Forbidden(), eval_root=root / "eval")
    if args.resume:
        setup = json.loads((root / "setup.json").read_text(encoding="utf-8"))
    else:
        reference = args.reference.resolve(strict=True)
        source = root / "source"
        (source / "assets").mkdir(parents=True)
        (source / "scripts").mkdir()
        shutil.copyfile(reference, source / "assets" / ("reference" + reference.suffix))
        (source / "scripts" / "notes.txt").write_text("版本测试：脚本目录也应完整保留。", encoding="utf-8")
        (source / "SKILL.md").write_text(
            "---\nname: version-live-vocabulary\ndescription: 英语词汇四格教学\n"
            "creatoros-display-name: 英语词汇（隔离版本验证）\n"
            "creatoros-output: social-content-pack.image-carousel\n---\n\n"
            "制作单张 2×2 英语词汇漫画。每格一个常见近义词、英文释义和中文释义，配具体情境。"
            "图片内文字清晰。只交付一张图。\n"
            f"原始参考：[作品](assets/reference{reference.suffix})。采用其风格时实际读取原图并传给生图工具。\n",
            encoding="utf-8", newline="")
        skill = catalog.register_local(source, role="legacy_end_to_end")
        repository = ContentRepository(db)
        repository.create_creator(creator_id="version-live-account", display_name="[隔离验证] Skill 版本")
        with db.session() as session:
            for identifier, name in (("version-live-series", "版本试用栏目"), ("version-live-other", "不应被切换的栏目")):
                session.add(Series(id=identifier, creator_id="version-live-account", name=name,
                    description="", audience="", skill_name=skill["id"]))
        setup = dict(skill_id=skill["id"], original_digest=skill["digest"], series_id="version-live-series",
                     other_series_id="version-live-other", url=f"http://127.0.0.1:{args.port}")
        (root / "setup.json").write_text(json.dumps(setup, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(setup, ensure_ascii=False), flush=True)
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port,
            log_level="warning", access_log=False, timeout_graceful_shutdown=3)).run()
    finally:
        db.close()


if __name__ == "__main__":
    main()
