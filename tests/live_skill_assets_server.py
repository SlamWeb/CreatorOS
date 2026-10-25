"""Isolated real DeepSeek/browser Skill asset probe; no formal data or production.

Run with --root <fresh ignored tmp dir> --original-png <PNG> --replacement-png <different PNG>.
The SPA and the normal account Agent use the real local HTTP and DeepSeek provider.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

import uvicorn
from sqlalchemy import text

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService
from creatoros.integrations.skill_extraction import SkillExtractionService
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, Database, Series, upgrade_database
from creatoros.web.app import create_app
from creatoros.web.server import StudioServer


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8896)
    parser.add_argument("--original-png", type=Path, required=True)
    parser.add_argument("--replacement-png", type=Path, required=True)
    parser.add_argument("--old-reference-count", type=int, choices=range(1, 7), default=None,
                        help="Optional numbered old references for a real frontend batch replacement probe")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()):
        raise ValueError("Live probe root must be inside ignored project tmp")
    if root.exists():
        raise FileExistsError(f"Use a fresh, nonexistent probe root: {root}")
    images = [value.resolve(strict=True) for value in (args.original_png, args.replacement_png)]
    for image in images:
        if not image.is_file() or not image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"Expected an existing PNG: {image}")
    image_hashes = [sha256(image) for image in images]
    if image_hashes[0] == image_hashes[1]:
        raise ValueError("Original and replacement PNGs must have different bytes")
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise RuntimeError("DEEPSEEK_API_KEY missing; use local .env or environment")

    root.mkdir(parents=True)
    db_url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    upgrade_database(db_url)
    db = Database(db_url)
    repository = ContentRepository(db)
    repository.create_creator(creator_id="skill-assets-a", display_name="[隔离验证] 主账号")
    repository.create_creator(creator_id="skill-assets-b", display_name="[隔离验证] 其他账号")
    forbidden_calls: list[str] = []

    def denied(*_args, **_kwargs):
        forbidden_calls.append("blocked-research-production-publication")
        raise AssertionError("Skill asset probe forbids Codex/research/production/publication")

    class Forbidden:
        produce_to = denied
        research = denied
        preflight = denied
        __getattr__ = lambda self, _name: denied

    catalog = ProducerSkillCatalog(root / "skills")
    sources = root / "sources"
    records = {}
    for name, description, with_assets in (
        ("asset-live-main", "主账号的参考图 Skill", True),
        ("asset-live-other", "另一账号的独立 Skill", False),
        ("asset-live-unused", "尚未绑定的 Skill", False),
    ):
        source = sources / name
        source.mkdir(parents=True)
        body = (f"---\nname: {name}\ndescription: {description}\n"
                "creatoros-output: social-content-pack.image-carousel\n---\n\n"
                f"# {name}\n\n")
        if with_assets:
            (source / "assets").mkdir()
            if args.old_reference_count:
                for index in range(1, args.old_reference_count + 1):
                    filename = f"reference-{index:02d}.png"
                    shutil.copy2(images[(index - 1) % 2], source / "assets" / filename)
                    body += f"参考{index}：![参考{index}](assets/{filename})\n"
            else:
                shutil.copy2(images[0], source / "assets" / "reference.png")
                shutil.copy2(images[1], source / "assets" / "unused.png")
                body += "保留这张原始参考图：![原始参考图](assets/reference.png)\n"
        else:
            body += "独立验证用，勿生产。\n"
        (source / "SKILL.md").write_text(body, encoding="utf-8", newline="")
        records[name] = catalog.register_local(source, role="legacy_end_to_end")
    with db.session() as session:
        session.add(Series(id="skill-assets-series-a", creator_id="skill-assets-a", name="参考图栏目",
                           description="隔离资产验证", audience="测试", skill_name=records["asset-live-main"]["id"]))
        session.add(Series(id="skill-assets-series-b", creator_id="skill-assets-b", name="其他账号栏目",
                           description="隔离作用域验证", audience="测试", skill_name=records["asset-live-other"]["id"]))

    installs = SkillInstallService(catalog, installer=Forbidden())
    extractions = SkillExtractionService(catalog, extractor=denied, reviser=denied, producer_factory=denied)
    research = TopicResearchService(db, catalog, researcher=Forbidden())
    runs = ContentRunService(db, output_root=root / "outputs", producer_factory=denied)
    app = create_app(database=db, run_service=runs, chat_root=root / "chats", skill_install_service=installs,
                     skill_extraction_service=extractions, topic_research_service=research,
                     publication_client=Forbidden(), eval_root=root / "eval")
    setup = {"root": str(root), "url": f"http://127.0.0.1:{args.port}",
             "creator_a": "skill-assets-a", "creator_b": "skill-assets-b",
             "series_a": "skill-assets-series-a", "series_b": "skill-assets-series-b",
             "skill_ids": {name: item["id"] for name, item in records.items()},
             "old_reference_count": args.old_reference_count,
             "input_pngs": [{"path": str(path), "sha256": digest} for path, digest in zip(images, image_hashes)]}
    (root / "setup.json").write_text(json.dumps(setup, ensure_ascii=False, indent=2), encoding="utf-8")

    @app.get("/api/skill-assets-probe/evidence")
    def evidence():
        with db.engine.connect() as connection:
            series = [dict(row._mapping) for row in connection.execute(text(
                "SELECT id,creator_id,name,description,audience,skill_name FROM series ORDER BY id"))]
            runs_count = connection.execute(text("SELECT COUNT(*) FROM content_runs")).scalar()
        files = []
        for file in sorted((root / "skills").rglob("*")):
            if file.is_file() and (file.name == "SKILL.md" or "assets" in file.parts):
                files.append({"path": file.relative_to(root).as_posix(), "sha256": sha256(file)})
        return {"setup": setup, "series": series, "runs_count": runs_count,
                "forbidden_calls": forbidden_calls, "files": files}

    # create_app mounts the SPA last. The diagnostic route must precede that mount.
    app.router.routes.insert(0, app.router.routes.pop())
    print(f"isolated_skill_assets_server={args.port} root={root}", flush=True)
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning",
                                   access_log=False, timeout_graceful_shutdown=3)).run()
    finally:
        (root / "forbidden-calls.json").write_text(json.dumps(forbidden_calls), encoding="utf-8")
        db.close()


if __name__ == "__main__":
    main()
