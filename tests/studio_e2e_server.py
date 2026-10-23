"""Isolated browser-E2E server; the producer is deterministic and never calls Codex."""
from __future__ import annotations

import argparse
import os
import json
import hashlib
from pathlib import Path
from time import sleep

from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack
from creatoros.integrations.codex import CodexUsage, ProducedPack, ProductionSession
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web.app import create_app


execution_counts = {"producer": 0, "extractor": 0}


class E2EProducer:
    def produce_to(self, **request) -> ProducedPack:
        execution_counts["producer"] += 1
        request["on_thread_started"]("studio-e2e-thread")
        sleep(1.5)
        directory = Path(request["directory"])
        (directory / "images").mkdir(parents=True)
        if request.get("task_references"):
            ref = request["task_references"]
            (directory / "reference-transport.json").write_text(json.dumps({
                "instructions": ref["instructions"], "reference_presentation": ref["reference_presentation"],
                "reference_id": ref["id"],
                "images": [{"name": image["name"], "sha256": hashlib.sha256(Path(image["path"]).read_bytes()).hexdigest()}
                           for image in ref["images"]]}, ensure_ascii=False), encoding="utf-8")
        cards = []
        for order, color in enumerate(("#292837", "#48536c", "#77719a"), 1):
            relative = f"images/{order:02d}.png"
            Image.new("RGB", (720, 960), color).save(directory / relative)
            cards.append(CarouselCard(order=order, kind="cover" if order == 1 else "content", headline=f"E2E 卡片 {order}", image_path=relative))
        pack = SocialContentPack(
            pack_id=request["pack_id"], creator_id=request["creator_id"], series_id=request["series_id"],
            topic_id=request["topic_id"], topic_title=request["topic_title"], skill_name="knowledge-to-carousel",
            generated_at="2026-09-05T12:00:00+08:00", content_summary="[隔离 E2E] 验证生产、返工和批准流程。",
            cards=cards, publish_copy=PublicationCopy(title="Agent 状态怎么理解", body="浏览器 E2E 测试内容。", hashtags=["#Agent"]),
        )
        (directory / "social_content_pack.json").write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        session = ProductionSession(thread_id="studio-e2e-thread", pack_id=pack.pack_id, created_at=pack.generated_at, usage=CodexUsage())
        (directory / "production_session.json").write_text(session.model_dump_json(indent=2), encoding="utf-8")
        return ProducedPack(directory=directory, pack=pack, session=session)


root = Path(os.environ["CREATOROS_E2E_ROOT"]).resolve()
root.mkdir(parents=True, exist_ok=True)
database_url = f"sqlite:///{(root / 'studio.db').as_posix()}"
upgrade_database(database_url)
database = Database(database_url)
runs = ContentRunService(database, producer_factory=E2EProducer, output_root=root / "outputs",
                         production_protocol="legacy")
app = create_app(database=database, run_service=runs, eval_root=root / "eval-runs", studio_dist=os.environ.get("CREATOROS_E2E_DIST"))
# Browser tests exercise real uploads/snapshots without accidentally starting paid Codex extraction.
from tests.smoke_skill_extraction import controlled as controlled_extraction
def counted_extraction(*args, **kwargs):
    execution_counts["extractor"] += 1
    return controlled_extraction(*args, **kwargs)
app.state.skill_extractions.extractor = counted_extraction

# Only the reference branch uses this explicit transport fixture. It proves
# Web -> real HTTP/SQLite/files, not Codex vision or image-generation quality.
original_researcher = app.state.topic_research.researcher
class ReferenceResearchFixture:
    def research(self, snapshot, count, instructions, workspace, cancel):
        if not snapshot.get("task_references"):
            return original_researcher.research(snapshot, count, instructions, workspace, cancel)
        from types import SimpleNamespace
        from creatoros.integrations.topic_research import ResearchReceipt
        from creatoros.integrations.topic_references import TaskReferences, prepare_references
        ref = TaskReferences.model_validate(snapshot["task_references"])
        prepare_references(ref, workspace)
        return SimpleNamespace(thread_id="controlled-reference-research", usage=CodexUsage(), receipt=ResearchReceipt(
            note="[受控浏览器夹具] 原图与完整要求已接收，尚未生产。", candidates=[
                {"title": f"参考选题 {n}", "angle": f"借鉴作品切入点 {n}", "rationale": "隔离接线验证",
                 "sources": [{"title": "Python文档（受控来源）", "url": "https://docs.python.org/3/"}]}
                for n in range(1, min(count, 2) + 1)]))
app.state.topic_research.researcher = ReferenceResearchFixture()

@app.get("/test/topic-references/{run_id}")
def reference_evidence(run_id: str):
    from creatoros.storage import ContentRun, Topic, ContentRevision, ContentAttempt
    from sqlalchemy import select
    with database.session() as session:
        run = session.get(ContentRun, run_id)
        topic = session.get(Topic, run.topic_id)
        revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run_id))
        attempt = session.scalar(select(ContentAttempt).where(ContentAttempt.revision_id == revision.id))
        path = Path(attempt.output_directory) / "reference-transport.json" if attempt else None
        return {"topic_reference_id": topic.reference_id, "producer": json.loads(path.read_text(encoding="utf-8"))
                if path and path.is_file() else None}

# create_app already registered the SPA catch-all; test evidence must precede it.
app.router.routes.insert(0, app.router.routes.pop())

# Only this isolated test executable exposes fixture creation, never the production app.
@app.post("/test/series/{series_id}/research")
def seed_research(series_id: str):
    from tests.smoke_topic_research import seed_batch
    from uuid import uuid4
    return seed_batch(app.state.topic_research, series_id, uuid4().hex)


@app.post("/test/skill-files")
def seed_skill_files():
    from tests.skill_browser_fixture import seed_skill_files as seed
    return seed(app.state.skill_installs.catalog, root / "skill-fixtures")


@app.get("/test/composer-evidence")
def composer_evidence(name: str = ""):
    """Read-only SQLite/file evidence; exists only in this isolated executable."""
    from creatoros.storage import Series, Topic, ContentRun, ContentAttempt
    from sqlalchemy import select, func
    with database.session() as session:
        query = select(Series)
        if name:
            query = query.where(Series.name == name)
        series = [{key: getattr(row, key) for key in (
            "id", "name", "skill_name", "mind_skill_id", "production_skill_id")}
            for row in session.scalars(query).all()]
        counts = {model.__tablename__: session.scalar(select(func.count()).select_from(model))
                  for model in (Topic, ContentRun, ContentAttempt)}
    files = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
             for item in app.state.skill_installs.catalog.list()
             if item["id"] != "knowledge-to-carousel"
             for path in Path(item["local_path"]).rglob("*") if path.is_file()}
    return {"series": series, "counts": counts, "files": files,
            "execution_counts": dict(execution_counts),
            "extraction_jobs": len(app.state.skill_extractions.list())}


app.router.routes.insert(0, app.router.routes.pop())


@app.post("/test/calibration")
def seed_calibration_route(payload: dict):
    from tests.skill_calibration_fixture import seed_calibration
    return seed_calibration(app, root)


@app.get("/test/calibration-evidence")
def calibration_evidence_route():
    from tests.skill_calibration_fixture import calibration_evidence
    return calibration_evidence(app)


app.router.routes.insert(0, app.router.routes.pop())
app.router.routes.insert(0, app.router.routes.pop())


@app.post("/test/eval-runs")
def seed_eval_runs(payload: dict):
    """Synthetic reports for browser tests, kept in the isolated server's eval root."""
    import json
    from tests.test_eval_store import fixture_report, save_report

    report = fixture_report()
    report["fixture_version"] = "browser-e2e-controlled-v1"
    report["checks"][0].update(label="完整隔离证据", detail="受控自动检查通过；仍需人工核对证据。")
    report["manual_checks"] = ["核对完整原始证据、账号作用域及回复是否准确；此记录为浏览器受控夹具。"]
    report["evidence_files"][0]["label"] = "完整请求与工具结果"
    directory = save_report(root / "eval-runs", report)
    if payload.get("browser_steps"):
        report["entrypoint"] = "browser"
        report["evidence_files"].append({"name": "browser.json", "label": "受控长步骤排版"})
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        (directory / "browser.json").write_text(json.dumps({"steps": payload["browser_steps"],
            "session_posts": 1, "turn_posts": 1, "posts_after_refresh": 0}, ensure_ascii=False), encoding="utf-8")
    long_text = "受控请求与工具结果原文，仅验证界面读取。" * 180 + "证据末尾验收标记。"
    (directory / "trace.json").write_text(json.dumps({"messages": [{"role": "user", "content": long_text}],
                                                    "api_key": "synthetic-e2e-value"}, ensure_ascii=False), encoding="utf-8")
    failed = fixture_report(auto_status="failed")
    failed["fixture_version"] = "browser-e2e-controlled-v1"
    failed["checks"][0].update(label="证据完整性", detail="受控缺证据负例。", evidence=["missing.txt"])
    failed["evidence_files"] = [{"name": "missing.txt", "label": "未保存的证据"}]
    save_report(root / "eval-runs", failed)
    return {"run_id": report["run_id"], "failed_run_id": failed["run_id"], "long_text": long_text}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
