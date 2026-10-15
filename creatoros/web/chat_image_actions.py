"""Image-to-business adapters, all scoped to a persisted Agent conversation."""
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from sqlalchemy import func, select

from creatoros.content import SocialContentPack
from creatoros.integrations.atomic_file import atomic_write_text
from creatoros.runs.artifacts import validate_artifact
from creatoros.runs.models import ContentRunInput
from creatoros.runs.repository import ContentRunRepository
from creatoros.storage import (
    ContentRun, ContentRevision, ContentAttempt, ContentRunStatus,
    ContentAttemptStatus, ContentRunEventType, Series, Topic, TopicSource, TopicStatus,
)
from creatoros.tools.chat_images import ReadImageArgs, ExtractSkillImagesArgs, ImportContentImagesArgs


class ExtractRequest(ExtractSkillImagesArgs):
    request_id: str = Field(min_length=1, max_length=128)


class ImportRequest(ImportContentImagesArgs):
    request_id: str = Field(min_length=1, max_length=128)


def _identity(sid, request_id):
    return hashlib.sha256(f"chat-image:{sid}:{request_id}".encode()).hexdigest()


def _managed_path(root, target):
    """Never follow directory symlinks/junctions when creating managed artifacts."""
    root, target = Path(root).absolute(), Path(target).absolute()
    if not target.resolve().is_relative_to(root.resolve()) or not target.is_relative_to(root):
        raise HTTPException(403, "目标目录越过受管范围。")
    cursor = target
    while True:
        if cursor.is_symlink() or (cursor.exists() and getattr(cursor.lstat(), "st_file_attributes", 0) & 0x400):
            raise HTTPException(403, "受管目录不能使用链接或重解析点。")
        if cursor == root:
            return target
        cursor = cursor.parent


def extraction_owner_path(chat, job_id):
    if len(job_id) != 64 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("提炼任务标识无效。")
    return _managed_path(chat.root, chat.root / "image-extractions" / f"{job_id}.json")


def owns_extraction(chat, job_id, session_id):
    try:
        return json.loads(extraction_owner_path(chat, job_id).read_text(encoding="utf-8"))["session_id"] == session_id
    except (OSError, ValueError, KeyError, HTTPException):
        return False


def chat_image_action_routes(chat, catalog, extractions, runs, artifacts, scope):
    router = APIRouter(prefix="/api/agent/sessions/{session_id}/image-actions")

    def session(session_id, request):
        header = request.headers.get("x-creatoros-agent-session")
        if header and header != session_id:
            raise HTTPException(403, "不能访问其他对话的图片。")
        doc = chat.get(session_id)
        if doc.get("scope_kind") == "creator":
            chat.require_creator(doc["creator_id"])
            return doc["creator_id"]
        return None

    def require(allowed):
        if not allowed:
            raise HTTPException(403, "图片或栏目不属于当前账号。")

    @router.post("/read")
    def read(session_id: str, payload: ReadImageArgs, request: Request):
        creator_id = session(session_id, request)
        if payload.attachment_id:
            attachment = chat.attachments.get(session_id, payload.attachment_id)
        else:
            if payload.skill_id:
                require(not creator_id or scope._owns_skill(payload.skill_id, creator_id))
                try:
                    item = catalog.read_skill_file(payload.skill_id, payload.path)
                except FileNotFoundError as error:
                    raise HTTPException(404, "Skill 图片不存在。") from error
                except PermissionError as error:
                    raise HTTPException(403, "Skill 图片不可读取。") from error
                except (ValueError, TypeError, OSError, OverflowError) as error:
                    raise HTTPException(422, "Skill 图片路径或内容无效。") from error
                if item["kind"] != "image":
                    raise HTTPException(422, "所选文件不是图片。")
                raw, name = item["content"], Path(payload.path).name
            else:
                require(not creator_id or scope._owns_run(payload.run_id, creator_id))
                _root, digest, _data, validation = artifacts.locate(payload.run_id, payload.revision_id)
                info = next((i for i in validation.get("images", []) if i["order"] == payload.image_index), None)
                if info is None:
                    raise HTTPException(404, "该版本中不存在此图片。")
                raw, mime = artifacts.image(payload.run_id, payload.revision_id, payload.image_index,
                                            digest=digest, checksum=info["sha256"])
                name = f"page-{payload.image_index}." + {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}[mime]
            attachment = chat.attachments.put(session_id, name, base64.b64encode(raw).decode())
        return {"attachment": attachment, "message": "图片已加入当前轮次，可实际查看。"}

    @router.post("/extract", status_code=202)
    def extract(session_id: str, payload: ExtractRequest, request: Request):
        creator_id = session(session_id, request)
        if len(set(payload.attachment_ids)) != len(payload.attachment_ids):
            raise HTTPException(422, "参考图片不能重复。")
        key = _identity(session_id, payload.request_id)
        job_id = hashlib.sha256(key.encode()).hexdigest()
        owner_path = extraction_owner_path(chat, job_id)
        with extractions.lock:
            if owner_path.exists():
                owner = json.loads(owner_path.read_text(encoding="utf-8"))
                if owner["input"] != payload.model_dump():
                    raise HTTPException(409, "同一请求的提炼要求已改变。")
                uploads = owner["upload_ids"]
            else:
                uploads = []
                for aid in payload.attachment_ids:
                    info = chat.attachments.get(session_id, aid)
                    raw = chat.attachments.path(session_id, aid).read_bytes()
                    uploads.append(extractions.upload(info["name"], base64.b64encode(raw).decode())["id"])
                owner_path.parent.mkdir(parents=True, exist_ok=True)
                owner = {"session_id": session_id, "creator_id": creator_id,
                         "input": payload.model_dump(), "upload_ids": uploads}
                # The owner is durable before a worker may begin; interrupted submission is safely retryable.
                _managed_path(chat.root, owner_path)
                atomic_write_text(owner_path, json.dumps(owner, ensure_ascii=False))
            result = extractions.submit(key, uploads, payload.mode, payload.instruction)
        return {**result, "url": f"/skills?extraction={result['id']}"}

    @router.post("/import")
    def import_images(session_id: str, payload: ImportRequest, request: Request):
        creator_id = session(session_id, request)
        require(not creator_id or scope._owns_series(payload.series_id, creator_id))
        if len(set(payload.attachment_ids)) != len(payload.attachment_ids):
            raise HTTPException(422, "导入图片不能重复。")
        images = []
        for aid in payload.attachment_ids:
            info = chat.attachments.get(session_id, aid)
            images.append((info, chat.attachments.path(session_id, aid).read_bytes()))
        return import_pack(runs, session_id, payload, images)

    return router


def import_pack(runs, session_id, payload, images):
    """Commit a verified local import atomically; no worker, generation or approval."""
    key = "external-import:" + _identity(session_id, payload.request_id)
    fingerprint = hashlib.sha256(json.dumps(payload.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with runs._write_lock, runs.database.session() as db:
        old = db.scalar(select(ContentRun).where(ContentRun.idempotency_key == key))
        if old:
            if old.context_snapshot_ref != fingerprint or old.origin_session_id != session_id:
                raise HTTPException(409, "同一导入请求的内容已改变。")
            return {"run_id": old.id, "status": old.status.value, "url": f"/runs/{old.id}", "deduplicated": True}
        series = db.get(Series, payload.series_id)
        if not series or not series.creator_id or not series.is_active or not series.creator.is_active:
            raise HTTPException(422, "请使用已分配有效账号的栏目。")
        run_id, revision_id, attempt_id = str(uuid4()), str(uuid4()), str(uuid4())
        topic_id = "import-" + uuid4().hex
        now = datetime.now(timezone.utc)
        snapshot = ContentRunInput(production_protocol="external-import", creator_id=series.creator_id,
            creator_name=series.creator.display_name, series_id=series.id, series_name=series.name,
            topic_id=topic_id, topic_title=payload.title, topic_brief=payload.body)
        directory = _managed_path(runs.output_root, runs.output_root / series.creator_id / series.id / run_id / "revision-001" / "attempt-001")
        directory.mkdir(parents=True, exist_ok=False)
        _managed_path(runs.output_root, directory)
        cards = []
        for order, (info, raw) in enumerate(images, 1):
            suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}[info["mime_type"]]
            name = f"image-{order:03d}{suffix}"
            (directory / name).write_bytes(raw)
            cards.append(dict(order=order, kind="cover" if order == 1 else "content", headline=payload.title, image_path=name))
        pack = SocialContentPack(pack_id=run_id, creator_id=series.creator_id, series_id=series.id,
            topic_id=topic_id, topic_title=payload.title, skill_name="external-import", generated_at=now.isoformat(),
            content_summary=payload.body or payload.title, cards=cards,
            publish_copy=dict(title=payload.title, body=payload.body or payload.title, hashtags=[]))
        manifest = directory / "social_content_pack.json"
        manifest.write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        checked = validate_artifact(directory, production_protocol="external-import")
        position = (db.scalar(select(func.max(Topic.position)).where(Topic.series_id == series.id)) or 0) + 1
        db.add(Topic(id=topic_id, series_id=series.id, title=payload.title, brief=payload.body,
                     position=position, source=TopicSource.MANUAL, status=TopicStatus.READY))
        db.flush()
        repository = ContentRunRepository(runs.database, session=db)
        repository.add_run(ContentRun(id=run_id, topic_id=topic_id, idempotency_key=key,
            status=ContentRunStatus.AWAITING_APPROVAL, input_snapshot_json=snapshot.model_dump(mode="json"),
            origin_session_id=session_id, context_snapshot_ref=fingerprint, retryable=False))
        repository.add_revision(ContentRevision(id=revision_id, content_run_id=run_id, revision_number=1,
            instruction="用户导入已有成品；未调用模型。", production_input_json=snapshot.model_dump(mode="json"),
            artifact_directory=str(directory), manifest_path=str(manifest), artifact_digest=checked.artifact_digest,
            validation_json=checked.model_dump(mode="json"), validated_at=now))
        repository.add_attempt(ContentAttempt(id=attempt_id, revision_id=revision_id, attempt_number=1,
            status=ContentAttemptStatus.SUCCEEDED, output_directory=str(directory), completed_at=now,
            usage_json={"source": "external-import", "model_calls": 0}, duration_ms=0))
        repository.add_event(run_id, ContentRunEventType.CREATED, revision_id=revision_id,
            payload={"source": "external-import", "origin_session_id": session_id})
        repository.add_event(run_id, ContentRunEventType.VALIDATED, revision_id=revision_id, attempt_id=attempt_id,
            to_status=ContentRunStatus.AWAITING_APPROVAL, payload={"source": "external-import", "card_count": len(cards)})
        return {"run_id": run_id, "revision_id": revision_id, "status": "awaiting_approval",
                "url": f"/runs/{run_id}", "message": "已导入，等待人工验收；未生成、未批准、未发布。"}
