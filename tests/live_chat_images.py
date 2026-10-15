"""Explicit isolated server for real browser -> DeepSeek image/Skill/import checks.

Start with --serve; requests are made only by the real frontend, not this script.
No formal account, production Skill or database is used.
"""
import argparse
import json
import os
from pathlib import Path

import uvicorn
from PIL import Image, ImageDraw

from creatoros.config import PROJECT_ROOT
from creatoros.storage import ContentRepository, Database, Series, upgrade_database
from creatoros.runs import ContentRunService
from creatoros.web.app import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8905)
    args = parser.parse_args()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY missing")
    root = args.root.resolve()
    if not root.is_relative_to(PROJECT_ROOT / "tmp"):
        raise ValueError("Use an isolated project tmp directory")
    root.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    upgrade_database(database_url)
    db = Database(database_url)
    repo = ContentRepository(db)
    if repo.get_creator("image-lab") is None:
        repo.create_creator(creator_id="image-lab", display_name="[隔离验证] 图片工作室")
    runs = ContentRunService(db, output_root=root / "outputs")
    app = create_app(database=db, run_service=runs, chat_root=root / "sessions")
    source = root / "test-skill"
    (source / "assets").mkdir(parents=True, exist_ok=True)
    for filename, color, label in (("reference.png", "#da2222", "ALPHA 73"), ("result.png", "#225cda", "BETA 29")):
        image = Image.new("RGB", (640, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((110, 130, 530, 550), fill=color)
        draw.text((190, 610), label, fill="black", font_size=48)
        image.save(root / filename)
    (source / "assets" / "reference.png").write_bytes((root / "reference.png").read_bytes())
    if not (source / "SKILL.md").exists():
        (source / "SKILL.md").write_text("---\nname: image-lab-skill\ndescription: 隔离图片编辑验证\n---\n\n# 图片测试\n\n制作一张色块说明图，参考 assets/reference.png。\n", encoding="utf-8")
    registered = app.state.skill_installs.catalog.register_local(source, role="legacy_end_to_end")
    with db.session() as session:
        if session.get(Series, "image-series") is None:
            session.add(Series(id="image-series", creator_id="image-lab", name="图片验证栏目",
                               description="仅用于真实图片链路验收", audience="测试", skill_name=registered["id"]))
    (root / "setup.json").write_text(json.dumps({"root": str(root), "creator_id": "image-lab", "series_id": "image-series",
        "skill_id": registered["id"], "model": "deepseek-flash", "url": f"http://127.0.0.1:{args.port}"}, ensure_ascii=False), encoding="utf-8")
    print(f"isolated_image_server={args.port} root={root}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
