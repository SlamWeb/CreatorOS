"""Isolated browser-E2E server; the producer is deterministic and never calls Codex."""
from __future__ import annotations

import argparse
import os
import json
import hashlib
from pathlib import Path
from time import sleep

# creatoros.web.app creates a module-level app while it is imported. Point that
# app at the test database too, before any CreatorOS module reads defaults.
root = Path(os.environ["CREATOROS_E2E_ROOT"]).resolve()
root.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'studio.db').as_posix()}"

from PIL import Image

from creatoros.content import CarouselCard, PublicationCopy, SocialContentPack
from creatoros.integrations.codex import CodexUsage, ProducedPack, ProductionSession
from creatoros.runs import ContentRunService
from creatoros.storage import Database, upgrade_database
from creatoros.web.app import create_app


execution_counts = {"producer": 0, "extractor": 0}


class E2EProducer:
    native_skill_inputs = True

    @staticmethod
    def _freeze_task_evidence(request, directory, pack):
        """Create controlled images with the same frozen-input receipt contract as task-v1.

        This fixture does not claim that an SDK, model, or image service was used.
        """
        from creatoros.delivery.image import Artifact, Checkpoint, Delivery, SavedArtifact, request_digest
        from creatoros.delivery.task_execution import freeze_execution_spec
        from creatoros.integrations.producer_skills import ProducerSkillCatalog
        from creatoros.integrations.topic_references import prepare_references

        references = request.get("task_references")
        if references:
            prepare_references(references, directory)
        spec = freeze_execution_spec(request["execution_spec"], ProducerSkillCatalog(request["skills_root"]),
                                     directory, trusted_skill_roots=[directory.parent.parent / "skill-snapshot",
                                                                     directory.parent.parent / "revision-inputs"])
        (directory / "execution_spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        (directory / "production_request.txt").write_text(
            json.dumps({"fixture": "controlled-e2e", "topic_title": request["topic_title"]}, ensure_ascii=False),
            encoding="utf-8")
        partial = directory / "partial-images"
        partial.mkdir()
        artifacts, pages = [], []
        for card in pack.cards:
            image = directory / card.image_path
            raw = image.read_bytes()
            saved = partial / f"{card.order:02d}.png"
            saved.write_bytes(raw)
            artifact = Artifact(order=card.order, source_image_path=str(image.resolve()),
                                image_prompt="隔离测试固定图片，不调用模型")
            artifacts.append(artifact)
            pages.append(SavedArtifact(**artifact.model_dump(), image_path=str(saved.resolve()),
                                       sha256=hashlib.sha256(raw).hexdigest()))
        checkpoint = Checkpoint(input_digest=request_digest(directory), thread_id="studio-e2e-thread",
            skill_digests={Path(skill.path).name: skill.digest for skill in spec.skills}, pages=pages,
            delivery=Delivery(title=pack.publish_copy.title, text=pack.publish_copy.body,
                              hashtags=pack.publish_copy.hashtags, complete=True, artifacts=artifacts),
            turn_completed=True)
        (directory / "native_checkpoint.json").write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")

    def produce_to(self, **request) -> ProducedPack:
        execution_counts["producer"] += 1
        if getattr(request.get("execution_spec"), "kind", None) == "video":
            from tests.staged_ui_fixture import produce_controlled_video
            return produce_controlled_video(request, root / "staged-fixtures")
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
            topic_id=request["topic_id"], topic_title=request["topic_title"], skill_name=request.get("skill_name") or "knowledge-to-carousel",
            generated_at="2026-09-05T12:00:00+08:00", content_summary="[隔离 E2E] 验证生产、返工和批准流程。",
            cards=cards, publish_copy=PublicationCopy(title="Agent 状态怎么理解", body="浏览器 E2E 测试内容。", hashtags=["#Agent"]),
        )
        (directory / "social_content_pack.json").write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        if getattr(request.get("execution_spec"), "kind", None) == "image":
            self._freeze_task_evidence(request, directory, pack)
        session = ProductionSession(thread_id="studio-e2e-thread", pack_id=pack.pack_id, created_at=pack.generated_at, usage=CodexUsage())
        (directory / "production_session.json").write_text(session.model_dump_json(indent=2), encoding="utf-8")
        return ProducedPack(directory=directory, pack=pack, session=session)


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


@app.post("/test/benchmark-batches")
def seed_benchmark_batches(payload: dict):
    """Controlled files only: exercise the real read API, never models or grading."""
    from tests.benchmark_surface_fixture import seed_surface_batch
    return seed_surface_batch(root / "eval-runs", legacy=bool(payload.get("legacy")))


@app.post("/test/staged-video")
def staged_video_fixture():
    from tests.staged_ui_fixture import seed_staged_video
    return seed_staged_video(app.state.skill_installs.catalog, root / "staged-fixtures")


app.router.routes.insert(0, app.router.routes.pop())


@app.post("/test/waiting-task")
def waiting_task_fixture():
    from tests.staged_visibility_fixture import seed_waiting_task
    return seed_waiting_task(database, runs)


app.router.routes.insert(0, app.router.routes.pop())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
