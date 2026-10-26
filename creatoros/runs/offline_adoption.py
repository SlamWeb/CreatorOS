"""Explicit adoption of an independently completed replay; never starts a producer."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from creatoros.content import SocialContentPack
from creatoros.integrations.codex import CodexUsage, ProductionSession
from creatoros.integrations.native_production import load_checkpoint, request_digest, safe_file
from creatoros.integrations.topic_references import frozen_references
from creatoros.storage import (ContentAttempt, ContentAttemptStatus, ContentRun, ContentRunEventType,
    ContentRunStatus, Creator, Database, Series, Topic, TopicRemoval, TopicStatus)
from creatoros.storage.models import ProductionQueueItem
from .artifacts import validate_artifact
from .models import ContentRunInput, RunExecutionResult
from .repository import ContentRunRepository
from .service import ContentRunError, ContentRunService


def _json(root, name):
    return json.loads(safe_file(root, root / name, 8_000_000).read_text(encoding="utf-8"))


def _plain_directory(path):
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
           for p in (path, *path.parents)):
        raise ValueError("恢复目录不能经过文件或目录链接。")


def _tree_hashes(root):
    _plain_directory(root)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("恢复证据不能含文件或目录链接。")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(safe_file(root, path).read_bytes()).hexdigest()
    return result


def inspect_source(service, run_id, source, expected_images):
    """Bind completed bytes to the original frozen request, not merely a manifest."""
    source = Path(source).absolute()
    _plain_directory(source)
    safe_file(source, source / "production_request.txt")
    run = service.get(run_id)
    revision = service.get_active_revision(run_id)
    data = ContentRunInput.model_validate(revision.production_input_json)
    if data.production_protocol != "native-v1":
        raise ValueError("只接受原生生产的完整离线证据。")
    attempts = service.repository.list_attempts(revision.id)
    original = next((Path(a.output_directory) for a in attempts if a.output_directory), None)
    if original is None or original.resolve() == source.resolve():
        raise ValueError("缺少原失败任务的冻结输入。")
    root = service.output_root / data.creator_id / data.series_id / run_id
    _plain_directory(root)
    safe_file(root, original / "production_request.txt")
    request = _json(source, "production_request.txt")
    if request != _json(original, "production_request.txt"):
        raise ValueError("重放输入与原任务不一致。")
    expected = {"pack_id": f"{run_id}-r{revision.revision_number:03d}",
        "creator_id": data.creator_id, "series_id": data.series_id,
        "topic_id": data.topic_id, "topic_title": data.topic_title,
        "topic_brief": data.topic_brief, "series_description": data.series_description,
        "audience": data.audience, "revision_instruction": revision.instruction}
    if any(request.get(key) != value for key, value in expected.items()):
        raise ValueError("重放输入归属与当前版本不一致。")
    checkpoint = load_checkpoint(source)
    if checkpoint is None or request_digest(original) != checkpoint.input_digest:
        raise ValueError("冻结输入、Skill、参考图或Checkpoint摘要不一致。")
    if data.composition is not None and checkpoint.skill_digests != {
            role: getattr(data.composition, role).digest for role in ("mind", "production")}:
        raise ValueError("重放Skill版本不一致。")
    if data.composition is None and checkpoint.skill_digests.get("production") != data.skill_digest:
        raise ValueError("重放单Skill版本不一致。")
    references = frozen_references(source)
    if (references.id if references else None) != (data.task_references.id if data.task_references else None):
        raise ValueError("重放参考图与当前版本不一致。")
    receipt = _json(source, "worker_receipt.json")
    session = ProductionSession.model_validate(_json(source, "production_session.json"))
    if (receipt.get("protocol") != "creatoros-worker-v1"
            or receipt.get("thread_id") != checkpoint.thread_id
            or session.thread_id != checkpoint.thread_id or session.pack_id != request["pack_id"]
            or session.status != "completed"
            or not any(t.get("phase") == "production" and t.get("status") == "completed"
                       for t in receipt.get("turns", []))):
        raise ValueError("缺少对应thread的SDK完成回执。")
    pack = SocialContentPack.load(source)
    for key in ("pack_id", "creator_id", "series_id", "topic_id", "topic_title"):
        if getattr(pack, key) != request[key]:
            raise ValueError("交付Manifest归属不一致。")
    validation = validate_artifact(source, composition=data.composition, production_protocol="native-v1")
    if validation.card_count != expected_images:
        raise ValueError("交付图片数量与本次核准数量不一致。")
    hashes = _tree_hashes(source)
    fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {"source": str(source), "fingerprint": fingerprint, "images": validation.card_count,
            "thread_id": checkpoint.thread_id, "input_digest": checkpoint.input_digest,
            "hashes": hashes, "original": str(original), "run_id": run_id,
            "revision_id": revision.id, "run_version": run.version}


def _copy_relocated(source, target):
    shutil.copytree(source, target)
    # Only host-managed local pointers move. Preserve raw model response, prompts,
    # SDK source paths and original request bytes as audit evidence.
    for name in ("native_checkpoint.json", "references/input.json"):
        path = target / name
        if not path.is_file():
            continue
        value = _json(target, name)
        if name.startswith("native"):
            for page in value["pages"]:
                original = Path(page["image_path"])
                page["image_path"] = str(target / original.relative_to(source))
        else:
            for image in value["images"]:
                image["path"] = str(target / Path(image["path"]).relative_to(source))
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def adopt_completed_replay(service, run_id, source, *, expected_version, expected_fingerprint,
                           expected_images):
    """Same Revision, new Attempt; caller must own a clean global execution guard."""
    with service._write_lock:
        service.guard.assert_clean()
        verified = inspect_source(service, run_id, source, expected_images)
        if verified["fingerprint"] != expected_fingerprint:
            raise ContentRunError("离线交付文件已变化，请重新检查。", code="artifact_changed")
        source = Path(verified["source"])
        with service.database.session() as session:
            repository = ContentRunRepository(service.database, session=session)
            run = service._require(repository, run_id)
            service._assert_current_run(session, run)
            revision = repository.get_revision_number(run_id, run.active_revision_number)
            # Repeat requests return the existing adoption without adding events.
            for attempt in repository.list_attempts(revision.id):
                marker = Path(attempt.output_directory or "") / "offline_adoption.json"
                if marker.is_file() and _json(marker.parent, marker.name).get("fingerprint") == expected_fingerprint:
                    if run.status not in {ContentRunStatus.AWAITING_APPROVAL, ContentRunStatus.APPROVED}:
                        raise ContentRunError("此前恢复未完成，保留证据等待核查。")
                    data = ContentRunInput.model_validate(revision.production_input_json)
                    current = validate_artifact(revision.artifact_directory,
                        composition=data.composition, production_protocol="native-v1")
                    if current.artifact_digest != revision.artifact_digest:
                        raise ContentRunError("已恢复产物发生变化。", code="artifact_changed")
                    return RunExecutionResult(run_id=run_id, revision_id=revision.id,
                        attempt_id=attempt.id, status=run.status.value,
                        artifact_directory=revision.artifact_directory, artifact_digest=revision.artifact_digest,
                        producer_thread_id=attempt.producer_thread_id)
            if run.version != expected_version:
                raise ContentRunError("运行状态已变化，请刷新。", code="version_conflict")
            if run.status is not ContentRunStatus.FAILED or run.lease_owner or run.lease_expires_at:
                raise ContentRunError("仅可恢复已结束且没有执行租约的失败任务。")
            if session.scalar(select(ContentRun.id).where(ContentRun.status.in_(
                    [ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING])).limit(1)):
                raise ContentRunError("仍有生产任务在执行。")
            if session.scalar(select(ProductionQueueItem.id).where(
                    ProductionQueueItem.status.in_(["pending", "running"])).limit(1)):
                raise ContentRunError("仍有任务等待执行。")
            topic = session.get(Topic, run.topic_id)
            series = session.get(Series, topic.series_id) if topic else None
            creator = session.get(Creator, series.creator_id) if series else None
            if (topic is None or series is None or creator is None or not series.is_active
                    or not creator.is_active or session.get(TopicRemoval, run.topic_id) is not None):
                raise ContentRunError("账号、栏目或选题已停用或移除。")
            number = repository.next_attempt_number(revision.id)
            data = ContentRunInput.model_validate(revision.production_input_json)
            target = (service.output_root / data.creator_id / data.series_id / run_id
                      / f"revision-{revision.revision_number:03d}" / f"attempt-{number:03d}")
            _plain_directory(target)
            if target.exists():
                raise ContentRunError("目标Attempt目录已存在，保留现场等待核查。")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                _copy_relocated(source, target)
                if _tree_hashes(source) != verified["hashes"]:
                    raise ValueError("复制期间源文件发生变化。")
                validation = validate_artifact(target, composition=data.composition, production_protocol="native-v1")
                if validation.card_count != expected_images:
                    raise ValueError("复制后页数不一致。")
                marker = {**verified, "source_usage": _json(source, "production_session.json")["usage"],
                          "adoption_usage": CodexUsage().model_dump(), "copied_to": str(target)}
                (target / "offline_adoption.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as error:
                target.mkdir(parents=True, exist_ok=True)
                (target / "offline_adoption_error.txt").write_text(str(error), encoding="utf-8")
                raise
            now = datetime.now(timezone.utc)
            attempt = ContentAttempt(id=str(uuid4()), revision_id=revision.id, attempt_number=number,
                status=ContentAttemptStatus.SUCCEEDED, output_directory=str(target),
                producer_thread_id=verified["thread_id"], usage_json=CodexUsage().model_dump(),
                trace_ref=str(target / "codex_trace.jsonl"), completed_at=now, duration_ms=0)
            repository.add_attempt(attempt)
            for kind, before, after in ((ContentRunEventType.RESUMED, ContentRunStatus.FAILED, ContentRunStatus.PRODUCING),
                    (ContentRunEventType.PRODUCED, ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING),
                    (ContentRunEventType.VALIDATED, ContentRunStatus.VALIDATING, ContentRunStatus.AWAITING_APPROVAL)):
                repository.add_event(run_id, kind, revision_id=revision.id, attempt_id=attempt.id,
                    from_status=before, to_status=after, payload={"offline_adoption": True,
                    "source": str(source), "fingerprint": expected_fingerprint, "card_count": expected_images})
            revision.artifact_directory, revision.manifest_path = str(target), str(target / "social_content_pack.json")
            revision.artifact_digest, revision.validation_json = validation.artifact_digest, validation.model_dump(mode="json")
            revision.validated_at = now
            run.status, run.producer_thread_id = ContentRunStatus.AWAITING_APPROVAL, verified["thread_id"]
            run.failure_stage = run.error_type = run.error_message = run.completed_at = None
            run.retryable = False
            if run.task_id is None:
                topic.status = TopicStatus.READY
            item = session.scalar(select(ProductionQueueItem).where(
                ProductionQueueItem.run_id == run_id, ProductionQueueItem.revision_id == revision.id))
            if item:
                item.status, item.completed_at, item.last_error = "succeeded", now, None
            session.flush()
            return RunExecutionResult(run_id=run_id, revision_id=revision.id, attempt_id=attempt.id,
                status=run.status.value, artifact_directory=str(target),
                artifact_digest=validation.artifact_digest, producer_thread_id=verified["thread_id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--images", type=int, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-version", type=int)
    parser.add_argument("--fingerprint")
    args = parser.parse_args()
    database = Database(args.database_url) if args.database_url else Database.from_defaults()
    service = ContentRunService(database, output_root=args.output_root)
    try:
        with service.guard:
            service.guard.assert_clean()
            if args.apply:
                if args.expected_version is None or not args.fingerprint:
                    parser.error("--apply requires --expected-version and --fingerprint")
                result = adopt_completed_replay(service, args.run_id, args.source,
                    expected_version=args.expected_version, expected_fingerprint=args.fingerprint,
                    expected_images=args.images).model_dump(mode="json")
            else:
                result = inspect_source(service, args.run_id, args.source, args.images)
                result.pop("hashes")
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        database.close()


if __name__ == "__main__":
    main()
