"""Opt-in isolated frontend -> real DeepSeek/Codex reference workflow server.

python -m tests.live_topic_references --serve --root tmp/reference-live-NEW --port 8895
No production database, shared Skills, approval or platform publishing is used.
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw
import uvicorn

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, Database, upgrade_database
from creatoros.web.app import create_app
from creatoros.web.server import StudioServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8895)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to(PROJECT_ROOT / "tmp"):
        parser.error("Use a dedicated project tmp directory, never production data.")
    root.mkdir(parents=True, exist_ok=True)  # Inherit normal sandbox-readable workspace ACLs.
    url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)
    repo = ContentRepository(db)
    source = root / "probe-skill"
    source.mkdir(exist_ok=True)
    skill_file = source / "SKILL.md"
    if not skill_file.exists():
        skill_file.write_text("---\nname: reference-workflow-probe\ndescription: 隔离验证参考作品输入的单页英语知识图\ncreatoros-output: social-content-pack.image-carousel\n---\n\n"
            "根据本次选题与参考内容，使用原生生图工具只生成一张清晰的英语知识图，含中英文解释。"
            "采用白底黑字简洁排版；参考图片默认只作为内容来源，不模仿配色或角色。"
            "保持用户指定范围；一次生图即可，不自动重画。不批准、不发布。\n", encoding="utf-8")
    skill = ProducerSkillCatalog(skills_root_for(db)).register_local(source, role="legacy_end_to_end")
    if not repo.get_creator("reference-live"):
        repo.create_creator(creator_id="reference-live", display_name="[隔离实测] 参考作品")
        repo.create_series(series_id="reference-live-series", creator_id="reference-live", name="参考英语单页",
            description="参考内容转为简洁中英双语知识图，只制作一张。", audience="英语初学者", skill_name=skill["id"])
    for name, heading, line, color in [
        ("reference-1.png", "BORROW vs LEND", "borrow: receive temporarily / lend: give temporarily", "#c84a48"),
        ("reference-2.png", "TEACH vs LEARN", "teach: help others know / learn: gain knowledge", "#2677ad"),
    ]:
        path = root / name
        if not path.exists():
            picture = Image.new("RGB", (1000, 600), "white")
            draw = ImageDraw.Draw(picture)
            draw.rectangle((30, 30, 970, 570), outline=color, width=15)
            draw.text((65, 150), heading, fill=color, font_size=58)
            draw.text((65, 310), line, fill="black", font_size=26)
            picture.save(path)
    (root / "setup.json").write_text(json.dumps({"creator_id": "reference-live", "series_id": "reference-live-series",
        "skill_id": skill["id"], "references": [str(root / "reference-1.png"), str(root / "reference-2.png")],
        "url": f"http://127.0.0.1:{args.port}"}, ensure_ascii=False, indent=2), encoding="utf-8")
    app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs"), chat_root=root / "chats")
    print(f"isolated_reference_server={args.port} root={root}", flush=True)
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")).run()
    finally:
        db.close()


if __name__ == "__main__":
    main()
