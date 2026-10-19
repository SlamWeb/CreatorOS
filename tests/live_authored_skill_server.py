"""Opt-in real DeepSeek + real browser, isolated author/import/bind fixture.

All Codex, research, production and external publication boundaries fail closed.
Run with --root <new ignored directory> --port 8896; never points to formal data.
"""
import argparse
import hashlib
import json
from pathlib import Path

import uvicorn
from sqlalchemy import text

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService
from creatoros.integrations.skill_extraction import SkillExtractionService
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, Database, upgrade_database
from creatoros.web.app import create_app
from creatoros.web.server import StudioServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8896)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()):
        raise ValueError("Live author probe must be below ignored project tmp")
    root.mkdir(parents=True, exist_ok=False)
    url = f"sqlite:///{(root / 'review.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)
    content = ContentRepository(db)
    content.create_creator(creator_id="author-live-a", display_name="[隔离验证] 创作实验室")
    content.create_creator(creator_id="author-live-b", display_name="[隔离验证] 其他账号")
    forbidden = []

    def denied(*args, **kwargs):
        forbidden.append("blocked-paid-or-external-boundary")
        raise AssertionError("This authored Skill probe must not call Codex/research/production/publishing")

    class Forbidden:
        produce_to = denied
        research = denied
        preflight = denied
        __getattr__ = lambda self, name: denied

    catalog = ProducerSkillCatalog(root / "skills")
    installs = SkillInstallService(catalog, installer=Forbidden())
    extractions = SkillExtractionService(catalog, extractor=denied, reviser=denied, producer_factory=denied)
    research = TopicResearchService(db, catalog, researcher=Forbidden())
    runs = ContentRunService(db, output_root=root / "outputs", producer_factory=denied)
    app = create_app(database=db, run_service=runs, chat_root=root / "chats", skill_install_service=installs,
                     skill_extraction_service=extractions, topic_research_service=research,
                     publication_client=Forbidden(), eval_root=root / "eval")

    @app.get("/api/author-probe/evidence")
    def evidence():
        with db.engine.connect() as connection:
            series = [dict(row._mapping) for row in connection.execute(text(
                "SELECT id,creator_id,name,skill_name FROM series ORDER BY id"))]
            runs_count = connection.execute(text("SELECT COUNT(*) FROM content_runs")).scalar()
        files = []
        for file in (root / "skills").rglob("*"):
            if file.is_file() and (file.name == "SKILL.md" or "assets" in file.parts):
                files.append({"path": file.relative_to(root).as_posix(), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
        return {"root": str(root), "series": series, "runs_count": runs_count,
                "forbidden_calls": forbidden, "files": files}

    # create_app mounts the SPA last; keep this diagnostic GET ahead of it.
    app.router.routes.insert(0, app.router.routes.pop())

    print(f"isolated_root={root}", flush=True)
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning",
                                   timeout_graceful_shutdown=3)).run()
    finally:
        (root / "forbidden-calls.json").write_text(json.dumps(forbidden), encoding="utf-8")
        db.close()


if __name__ == "__main__":
    main()
