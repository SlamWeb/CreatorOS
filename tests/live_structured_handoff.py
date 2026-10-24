"""Opt-in real SDK/SQLite/HTTP one-image probe of the structured handoff.

Run with --run --source-attempt outputs/.../revision-001/attempt-001.
The failed source is read only. Every new application record and copied Skill
stays in a fresh tmp directory. This does not resume, approve, or publish.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

import httpx
from sqlalchemy.engine import make_url

from creatoros.config import DATABASE_URL, PROJECT_ROOT
from creatoros.integrations.atomic_file import atomic_write_text
from creatoros.integrations.codex import CODEX_EFFORT, CODEX_MODEL, CodexSdkProducer
from creatoros.integrations.native_image_assets import ImageAssetStore
from creatoros.integrations.native_production import load_checkpoint, safe_file
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest, freeze_skill, skills_root_for
from creatoros.integrations.topic_references import TopicReferenceStore, frozen_references
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, CreatorPlatform, Database, TopicSource, upgrade_database
from creatoros.web import create_app
from creatoros.web.composition import SeriesCompositionService
from tests.agent_studio_support import serve


ONE_IMAGE = (
    "\n\n本次为独立隔离验证，明确只交付1张实际图片。保留上述选题与必要教学内容，"
    "用本次冻结的两份Skill完成一张图。原生生图工具只生成一次，不自动重画。"
    "不要恢复或读取任何旧生产会话，不批准、不发布。"
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def linked(path):
    return path.is_symlink() or (path.exists() and bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400))


def check_path(path, root):
    path, root = Path(path).absolute(), Path(root).resolve()
    require(path.resolve().is_relative_to(root), "Path is outside the allowed root")
    require(not any(linked(part) for part in (path, *path.parents)), "Linked paths are forbidden")
    return path


def sha_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def attempt_hashes(source):
    """Scan only the explicitly selected Attempt, never all formal outputs."""
    result = {}
    for path in sorted(source.rglob("*")):
        check_path(path, source)
        if path.is_file():
            result[path.relative_to(source).as_posix()] = sha_file(path)
    return result


def source_row(database_path, run_id):
    """Read precisely one existing row with SQLite's read-only connection mode."""
    require(database_path.is_file(), "Source SQLite database does not exist")
    connection = sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute("SELECT * FROM content_runs WHERE id = ?", (run_id,)).fetchone()
        require(row is not None, "The selected source Run does not exist")
        return dict(row)
    finally:
        connection.close()


def row_digest(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def save(root, report):
    atomic_write_text(root / "report.json", json.dumps(report, ensure_ascii=False, indent=2))


def copy_references(source, database, series_id):
    reference = frozen_references(source)
    if reference is None:
        return None
    store = TopicReferenceStore(database)
    ids = []
    for item in reference.images:
        path = safe_file(source / "references", Path(item.path), 4 * 1024 * 1024)
        require(sha_file(path) == item.sha256, "Source reference bytes changed")
        uploaded = store.upload(series_id, item.name, base64.b64encode(path.read_bytes()).decode("ascii"))
        ids.append(uploaded["id"])
    copied = store.freeze(series_id, ids, instructions=reference.instructions,
        reference_presentation=reference.reference_presentation, source_user_text=reference.source_user_text)
    require([image.sha256 for image in copied.images] == [image.sha256 for image in reference.images],
            "Copied task references differ from the source")
    return copied.id


def verify_delivery(attempt, source_thread):
    # Delayed import lets --help work before the implementation lands; an actual
    # probe must use the production validator, not a local approximation.
    from creatoros.integrations.native_production import StructuredDelivery

    checkpoint = load_checkpoint(attempt)
    require(checkpoint is not None and checkpoint.turn_completed, "No valid completed Checkpoint")
    require(checkpoint.handoff_protocol == "structured-v1", "Structured protocol was not enabled")
    require(checkpoint.thread_id != source_thread, "The source thread was resumed")
    materials_path = safe_file(attempt, attempt / "input-materials.json", 8_000_000)
    materials = json.loads(materials_path.read_text(encoding="utf-8-sig"))
    require(isinstance(materials, dict), "Input materials must be an object")
    envelope_path = safe_file(attempt / "work", attempt / "work/structured-response.json", 8_000_000)
    envelope = json.loads(envelope_path.read_text(encoding="utf-8-sig"))
    require(envelope["thread_id"] == checkpoint.thread_id, "Response envelope thread mismatch")
    require(envelope["input_digest"] == checkpoint.input_digest, "Response envelope input mismatch")
    require(isinstance(envelope["turn_id"], str) and bool(envelope["turn_id"]), "Response has no turn identity")
    require(envelope["phase"] in {"production", "delivery_repair"}, "Invalid delivery phase")
    require(isinstance(envelope["response"], str), "Response envelope has no raw SDK text")
    response_name = "production_repair_response.txt" if envelope["phase"] == "delivery_repair" else "production_response.txt"
    raw_response = safe_file(attempt, attempt / response_name, 8_000_000).read_text(encoding="utf-8")
    require(raw_response == envelope["response"], "Raw SDK response and host envelope differ")
    structured = StructuredDelivery.model_validate_json(raw_response)
    raw = json.loads(raw_response)
    require(structured.complete and len(structured.artifacts) == 1, "Expected one selected image")
    require(all(set(item) == {"order", "asset_id", "image_prompt", "reference_ids", "content"}
                for item in raw["artifacts"]), "SDK output contains path fields or unexpected artifact fields")
    require(len(checkpoint.pages) == 1, "Expected one validated page")
    chosen = structured.artifacts[0]
    page = checkpoint.pages[0]
    require(chosen.asset_id == page.asset_id and chosen.image_prompt == page.image_prompt,
            "Structured selection differs from the accepted page")
    require(chosen.content == page.content, "Structured content differs from host-saved content")
    store = ImageAssetStore(attempt, Path(), checkpoint.thread_id, checkpoint.input_digest)
    received = store.read().assets
    require(len(received) == 1, "The model generated more or fewer than one received candidate")
    asset, copied = store.resolve(chosen.asset_id)
    require(asset.sha256 == page.sha256 == sha_file(copied), "Selected image checksum mismatch")
    receipt = json.loads(safe_file(attempt, attempt / "worker_receipt.json", 256_000).read_text(encoding="utf-8"))
    turns = receipt["turns"]
    require(receipt["thread_id"] == checkpoint.thread_id and turns[-1]["id"] == envelope["turn_id"]
            and turns[-1]["phase"] == envelope["phase"] and turns[-1]["status"] == "completed",
            "Structured response lacks a matching successful final SDK turn")
    return checkpoint, asset, {
        "handoff_protocol": checkpoint.handoff_protocol, "input_materials_sha256": sha_file(materials_path),
        "input_materials_keys": sorted(materials), "response_envelope_sha256": sha_file(envelope_path),
        "response_turn_id": envelope["turn_id"], "response_phase": envelope["phase"],
        "raw_sdk_artifact_fields": sorted(raw["artifacts"][0]), "raw_sdk_has_path_fields": False,
        "candidate_images": len(received), "selected_images": len(checkpoint.pages),
        "candidate_asset_ids": [item.asset_id for item in received], "selected_asset_ids": [chosen.asset_id],
        "selected_sha256": asset.sha256, "repair_attempted": checkpoint.repair_attempted,
    }


def run(args):
    tmp = (PROJECT_ROOT / "tmp").resolve()
    root = (args.directory or tmp / ("structured-handoff-live-" + uuid4().hex[:12])).absolute()
    check_path(root, tmp)
    require(root != tmp and not root.exists(), "Use a fresh directory below project tmp")
    # Ordinary mkdir inherits parent ACLs, unlike Windows tempfile directories.
    root.mkdir(parents=True)
    report = {"status": "starting", "directory": str(root), "codex_transport": "real SDK", "execution_submitted": False,
        "model": CODEX_MODEL, "effort": CODEX_EFFORT, "formal_data_written": False,
        "approved": False, "published": False, "started_at": datetime.now(timezone.utc).isoformat()}
    save(root, report)
    print(json.dumps({"directory": str(root), "report": str(root / "report.json")}), flush=True)
    database, source, before, old_row = None, None, None, None
    try:
        from creatoros.integrations.native_production import Checkpoint, StructuredDelivery

        require("handoff_protocol" in Checkpoint.model_fields and "artifacts" in StructuredDelivery.model_fields,
                "Structured handoff implementation must be loaded before any paid execution")
        source = check_path(args.source_attempt, PROJECT_ROOT / "outputs")
        require(re.fullmatch(r"attempt-\d{3}", source.name) is not None
                and re.fullmatch(r"revision-\d{3}", source.parent.name) is not None,
                "Source must be a formal outputs Revision/Attempt directory")
        source = source.resolve(strict=True)
        run_id = source.parent.parent.name
        source_database = args.source_database
        if source_database is None:
            configured = make_url(DATABASE_URL)
            require(configured.get_backend_name() == "sqlite" and configured.database not in {None, ":memory:"},
                    "Source row protection requires a file-backed SQLite database")
            source_database = Path(configured.database)
        source_database = source_database.resolve(strict=True)
        require(not source_database.is_relative_to(root), "Source database must predate the isolated probe")
        old_row = source_row(source_database, run_id)
        require(old_row["status"] == "failed", "Only a failed source Run may be used")
        before = attempt_hashes(source)
        prior = load_checkpoint(source)
        require(prior is not None, "Source frozen inputs or Checkpoint are invalid")
        require(old_row["producer_thread_id"] in {None, prior.thread_id}, "Source Run/thread evidence differs")
        payload = json.loads(safe_file(source, source / "production_request.txt", 2_000_000).read_text(encoding="utf-8"))
        require(isinstance(payload.get("topic_title"), str) and bool(payload["topic_title"].strip()), "Source has no title")
        require(payload.get("topic_brief") is None or isinstance(payload["topic_brief"], str), "Invalid source brief")
        report.update(source_attempt=str(source), source_run_id=run_id, source_thread_id=prior.thread_id,
            source_database=str(source_database), source_run_row_sha256=row_digest(old_row), source_files_before=before,
            source_topic_title=payload["topic_title"], source_topic_brief=payload.get("topic_brief"),
            explicit_additional_instruction=ONE_IMAGE, status="preparing")
        save(root, report)
        url = "sqlite:///" + (root / "isolated.sqlite").as_posix()
        upgrade_database(url)
        database = Database(url)
        catalog_root = skills_root_for(database)
        require(catalog_root.resolve().is_relative_to(root.resolve()), "Catalog is not isolated")
        catalog = ProducerSkillCatalog(catalog_root, project_root=PROJECT_ROOT)
        skills = {}
        for role in ("mind", "production"):
            original = source / "skills" / role
            safe_file(source / "skills", original / "SKILL.md", 2_000_000)
            copied = root / "authoring" / role
            freeze_skill(original, copied, _digest(original))
            skills[role] = catalog.register_local(copied, role=role, source_note="Frozen failed input; independent isolated SDK probe")
            require(skills[role]["digest"] == _digest(original), "Private Skill copy changed frozen content")
        repository = ContentRepository(database)
        repository.create_creator(creator_id="structured-probe", display_name="结构化交接隔离验证", platform=CreatorPlatform.XIAOHONGSHU)
        series_id, _ = SeriesCompositionService(database, catalog).create_series(
            name="结构化交接单图验证", description=payload.get("series_description") or "",
            audience=payload.get("audience") or "", creator_id="structured-probe", skill_name=None,
            mind_skill_id=skills["mind"]["id"], production_skill_id=skills["production"]["id"],
            request_id=uuid4().hex, origin="web")
        reference_id = copy_references(source, database, series_id)
        repository.add_topic(topic_id="structured-probe-topic", series_id=series_id, source=TopicSource.MANUAL,
            title=payload["topic_title"], brief=(payload.get("topic_brief") or "") + ONE_IMAGE, reference_id=reference_id)
        producer = CodexSdkProducer.from_defaults()
        producer.timeout_seconds = args.timeout
        service = ContentRunService(database, output_root=root / "outputs", producer_factory=lambda: producer)
        created = service.create("structured-probe-topic")
        require(created.producer_thread_id is None and created.id != run_id, "Probe must start a new Run without a thread")
        report.update(status="running", run_id=created.id, series_id=series_id,
            skill_ids={role: item["id"] for role, item in skills.items()}, task_reference_id=reference_id,
            execution_submitted=True)
        save(root, report)
        result = service.execute(created.id)  # One explicit execution; never retry/resume the source.
        report["result"] = result.model_dump(mode="json")
        save(root, report)
        require(result.artifact_directory is not None, "No accepted artifact directory; inspect isolated Run errors")
        attempt = Path(result.artifact_directory)
        check_path(attempt, root / "outputs")
        report["artifact_directory"] = str(attempt)
        current = load_checkpoint(attempt)
        if current is not None:
            report.update(thread_id=current.thread_id, checkpoint_cumulative_usage=current.usage.model_dump(),
                          selected_images=len(current.pages), selected_asset_ids=[page.asset_id for page in current.pages])
            candidates = ImageAssetStore(attempt, Path(), current.thread_id, current.input_digest).read().assets
            report.update(candidate_images=len(candidates), candidate_asset_ids=[item.asset_id for item in candidates])
        session_path = attempt / "production_session.json"
        if session_path.is_file():
            session = json.loads(safe_file(attempt, session_path, 256_000).read_text(encoding="utf-8"))
            report["sdk_usage"] = session["usage"]
        save(root, report)
        checkpoint, asset, checks = verify_delivery(attempt, prior.thread_id)
        report.update(checks, thread_id=checkpoint.thread_id, new_thread_is_distinct=True,
            artifact_directory=str(attempt), checkpoint_cumulative_usage=checkpoint.usage.model_dump())
        session = json.loads(safe_file(attempt, attempt / "production_session.json", 256_000).read_text(encoding="utf-8"))
        require(session["thread_id"] == checkpoint.thread_id, "Session thread mismatch")
        report["sdk_usage"] = session["usage"]
        app = create_app(database=database, run_service=service, chat_root=root / "chats",
                         eval_root=root / "eval", defer_background_start=True)
        with serve(app) as base, httpx.Client(base_url=base, timeout=20, trust_env=False) as client:
            detail_response = client.get("/api/runs/" + created.id)
            detail_response.raise_for_status()
            detail = detail_response.json()
            require(detail["status"] == "awaiting_approval", "New Run is not awaiting human approval")
            require(len(detail["revisions"]) == 1 and len(detail["revisions"][0]["attempts"]) == 1,
                    "Expected one new Revision and one Attempt")
            revision = detail["revisions"][0]
            require(revision["approved_at"] is None and detail.get("publication") is None, "Unexpected approval or publication")
            require(len(revision["cards"]) == 1, "HTTP does not expose exactly one selected card")
            image_url = revision["cards"][0]["url"]
            require(image_url.startswith("/api/") and not image_url.startswith("//"), "Artifact URL must be local")
            image_response = client.get(image_url)
            image_response.raise_for_status()
            require(hashlib.sha256(image_response.content).hexdigest() == asset.sha256, "HTTP image bytes differ from received image")
            report.update(http_detail=detail_response.status_code, http_image=image_response.status_code,
                http_image_bytes=len(image_response.content), status="passed", run_status=detail["status"])
    except BaseException as error:
        # Do not echo SDK errors, env/config contents, or authentication material.
        # Detailed production evidence remains in the isolated Attempt.
        report.update(status="failed", error_type=type(error).__name__,
                      production_error_type=getattr(error, "error_type", None))
        if isinstance(error, AssertionError):
            report["failed_check"] = str(error)  # Only this runner's fixed assertion messages.
    finally:
        if before is not None and source is not None:
            try:
                after = attempt_hashes(source)
                report["source_files_after"] = after
                report["source_attempt_unchanged"] = after == before
                row_after = source_row(source_database, run_id)
                report["source_run_row_sha256_after"] = row_digest(row_after)
                report["source_run_row_unchanged"] = row_after == old_row
                if not report["source_attempt_unchanged"] or not report["source_run_row_unchanged"]:
                    report["status"] = "failed"
            except BaseException as error:
                report.update(status="failed", source_verification_error_type=type(error).__name__)
        if database is not None:
            database.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save(root, report)
        print(json.dumps({"status": report["status"], "error_type": report.get("error_type"),
                          "report": str(root / "report.json")}), flush=True)
    return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly permit real Codex usage and one native image")
    parser.add_argument("--source-attempt", type=Path, help="Existing failed Attempt below project outputs")
    parser.add_argument("--source-database", type=Path, help="Source SQLite file; defaults to configured DATABASE_URL (read only)")
    parser.add_argument("--directory", type=Path, help="Fresh tmp evidence directory; generated automatically when omitted")
    parser.add_argument("--timeout", type=float, default=1800, help="Production timeout in seconds, without automatic resubmission")
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    if args.source_attempt is None or args.timeout <= 0:
        parser.error("--run requires --source-attempt and a positive --timeout")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
