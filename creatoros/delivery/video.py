"""Video receipt identity, full media decoding and artifact acceptance."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import Field, field_validator

from creatoros.integrations.media_tools import probe_media, decode_media
from .files import safe_file
from .models import ArtifactValidation, CodexUsage, ProductionModel, ValidatedVideo
from .task_contracts import TaskExecutionSpec
from .task_execution import file_sha256, verify_execution_spec

VIDEO_MANIFEST = "video_artifact.json"
VIDEO_CHECKPOINT = "video_checkpoint.json"
VIDEO_CORRECTION = "video_path_correction.json"
SOURCE_WORKER_RECEIPT = "source_worker_receipt.json"
SOURCE_WORKER_TASK = "source_worker_task.json"
SOURCE_EXECUTION_SPEC = "source_execution_spec.json"


class VideoDelivery(ProductionModel):
    title: str = Field(min_length=1)
    text: str
    video_path: str | None = Field(default=None, description="成功时为 work 内 MP4 相对路径，失败为 null")
    complete: bool
    error: str | None = None


def video_delivery_schema():
    # Structured outputs require every property, including nullable fields.
    # Local defaults still allow reading old successful delivery receipts.
    schema = VideoDelivery.model_json_schema()
    schema["required"] = list(schema["properties"])
    return schema


class VideoArtifact(ProductionModel):
    schema_version: Literal[1] = 1
    kind: Literal["video"] = "video"
    pack_id: str
    creator_id: str
    series_id: str
    topic_id: str
    topic_title: str
    title: str
    text: str
    generated_at: str
    video_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class VideoCheckpoint(ProductionModel):
    protocol: Literal["task-video-v1"] = "task-video-v1"
    input_digest: str
    thread_id: str
    turn_completed: bool = False
    response: str | None = None
    usage: CodexUsage = Field(default_factory=CodexUsage)
    handoff_protocol: Literal["final-json-v1", "submit-tool-v1"] = "final-json-v1"
    accepted_sha256: str | None = None
    submission_digest: str | None = None
    submission_attempts: int = 0


def normalize_video_work_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1000:
        raise ValueError("请选择原 Attempt/work 内的视频相对路径。")
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if (path.is_absolute() or PureWindowsPath(value).is_absolute() or ".." in path.parts
            or ":" in value or path.suffix.lower() != ".mp4" or str(path) != value):
        raise ValueError("纠正的视频必须是 work 内的规范 MP4 相对路径。")
    return value


class VideoPathCorrection(ProductionModel):
    """An explicit host selection, separate from the unmodified SDK response."""
    protocol: Literal["video-path-correction-v1"] = "video-path-correction-v1"
    source_run_id: str = Field(min_length=1)
    source_revision_id: str = Field(min_length=1)
    source_run_version: int = Field(ge=1)
    source_attempt_id: str = Field(min_length=1)
    source_attempt_number: int = Field(ge=1)
    pack_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_checkpoint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_response_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_worker_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_execution_spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_worker_task_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    selected_work_path: str = Field(min_length=1, max_length=1000)
    selected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    inspection_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("selected_work_path")
    @classmethod
    def safe_relative_mp4(cls, value):
        return normalize_video_work_path(value)

    def evidence_fingerprint(self) -> str:
        evidence = dict(run_id=self.source_run_id, revision_id=self.source_revision_id,
            run_version=self.source_run_version, source_attempt_id=self.source_attempt_id,
            source_attempt_number=self.source_attempt_number, selected_work_path=self.selected_work_path,
            input_digest=self.input_digest, thread_id=self.thread_id, checkpoint_sha256=self.source_checkpoint_sha256,
            response_sha256=self.source_response_sha256, worker_receipt_sha256=self.source_worker_receipt_sha256,
            request_sha256=self.source_request_sha256, execution_spec_sha256=self.source_execution_spec_sha256,
            worker_task_sha256=self.source_worker_task_sha256, selected_sha256=self.selected_sha256)
        raw = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(raw).hexdigest()


def completed_worker_receipt(path: Path, thread_id: str) -> bool:
    """Accept only an explicitly completed final production/repair turn."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        turns = value.get("turns", [])
        return bool(value.get("protocol") == "creatoros-worker-v1" and value.get("thread_id") == thread_id
                    and turns and turns[-1].get("id") and turns[-1].get("phase") in {"production", "delivery_repair"}
                    and turns[-1].get("status") == "completed")
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _identity(spec, payload):
    return hashlib.sha256(json.dumps({"spec": spec.public_dict(), "request": payload},
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _require_playable_mp4(metadata):
    if (not metadata["has_video"] or not metadata["has_audio"]
            or "mp4" not in metadata["format"].split(",")):
        raise ValueError("视频必须为真实 MP4，含可解码画面与音轨。")
    if metadata.get("video_codec") != "h264" or metadata.get("audio_codec") != "aac":
        raise ValueError("页面视频交付需要 H.264 画面与 AAC 音轨，请转换编码后交付。")


@lru_cache(maxsize=64)
def _decoded_video_metadata(path: str, checksum: str):
    """Decode a verified byte sequence once, not on every browser Range request.

    Callers hash the current file before using this cache; replacing its bytes
    cannot reuse the old entry. Exceptions are never cached.
    """
    metadata = probe_media(Path(path))
    decode_media(Path(path))
    _require_playable_mp4(metadata)
    return metadata


def validate_video_artifact(root: Path, execution_spec=None):
    artifact = VideoArtifact.model_validate_json(safe_file(root, root / VIDEO_MANIFEST).read_text(encoding="utf-8"))
    video = safe_file(root, root / artifact.video_path)
    if video.suffix.lower() != ".mp4" or file_sha256(video) != artifact.sha256:
        raise ValueError("视频文件已变化。")
    spec = TaskExecutionSpec.model_validate_json(safe_file(root, root / "execution_spec.json").read_text(encoding="utf-8"))
    if execution_spec and spec.public_dict() != TaskExecutionSpec.model_validate(execution_spec).public_dict():
        raise ValueError("视频执行规范与当前 Revision 不符。")
    verify_execution_spec(spec, root, ready=True)
    payload = json.loads(safe_file(root, root / "production_request.txt").read_text(encoding="utf-8"))
    checkpoint = VideoCheckpoint.model_validate_json(safe_file(root, root / VIDEO_CHECKPOINT).read_text(encoding="utf-8"))
    accepted = checkpoint.handoff_protocol == "submit-tool-v1" and checkpoint.accepted_sha256
    if (not checkpoint.turn_completed and not accepted) or checkpoint.input_digest != _identity(spec, payload):
        raise ValueError("视频缺少完整且匹配输入的生产回执。")
    delivery = VideoDelivery.model_validate_json(checkpoint.response or "")
    correction_path = root / VIDEO_CORRECTION
    correction = None
    if correction_path.exists() or correction_path.is_symlink():
        correction = VideoPathCorrection.model_validate_json(safe_file(root, correction_path, 256_000).read_text(encoding="utf-8"))
        receipt = safe_file(root, root / SOURCE_WORKER_RECEIPT, 256_000)
        source_spec = safe_file(root, root / SOURCE_EXECUTION_SPEC, 2_000_000)
        source_task = safe_file(root, root / SOURCE_WORKER_TASK, 256_000)
        task = json.loads(source_task.read_text(encoding="utf-8"))
        if (accepted or checkpoint.handoff_protocol != "final-json-v1" or not checkpoint.turn_completed
                or not delivery.complete or not delivery.video_path or delivery.error is not None
                or correction.thread_id != checkpoint.thread_id
                or correction.input_digest != checkpoint.input_digest or correction.pack_id != payload.get("pack_id")
                or correction.source_checkpoint_sha256 != file_sha256(root / VIDEO_CHECKPOINT)
                or correction.source_response_sha256 != hashlib.sha256((checkpoint.response or "").encode("utf-8")).hexdigest()
                or correction.source_worker_receipt_sha256 != file_sha256(receipt)
                or correction.source_request_sha256 != file_sha256(root / "production_request.txt")
                or correction.source_execution_spec_sha256 != file_sha256(source_spec)
                or correction.source_worker_task_sha256 != file_sha256(source_task)
                or correction.evidence_fingerprint() != correction.inspection_fingerprint
                or TaskExecutionSpec.model_validate_json(source_spec.read_text(encoding="utf-8")).public_dict() != spec.public_dict()
                or not completed_worker_receipt(receipt, checkpoint.thread_id)
                or task.get("protocol") != "creatoros-worker-v1" or task.get("kind") != "production"
                or task.get("input_ref") != "production_request.txt" or task.get("deliverable") != VIDEO_MANIFEST
                or task.get("scope") != {key: payload[key] for key in ("pack_id", "creator_id", "series_id", "topic_id")}
                or any(getattr(artifact, key) != payload.get(key) for key in
                       ("pack_id", "creator_id", "series_id", "topic_id", "topic_title"))):
            raise ValueError("视频纠正证据与原回执或任务身份不一致。")
        original = safe_file(root / "work", root / "work" / correction.selected_work_path)
        if file_sha256(original) != correction.selected_sha256:
            raise ValueError("人工选定的视频文件发生变化。")
    else:
        original = (safe_file(root, root / "received-video.mp4") if accepted else
                    safe_file(root / "work", root / "work" / delivery.video_path))
    if accepted and file_sha256(original) != checkpoint.accepted_sha256:
        raise ValueError("已接收视频发生变化。")
    if file_sha256(original) != artifact.sha256 or delivery.title != artifact.title or delivery.text != artifact.text:
        raise ValueError("视频与 SDK 已完成回执不一致。")
    metadata = _decoded_video_metadata(str(video), artifact.sha256)
    digest = hashlib.sha256(b"creatoros-video-artifact-v2\0" if correction else b"creatoros-video-artifact-v1\0")
    files = [root / VIDEO_MANIFEST, root / VIDEO_CHECKPOINT, root / "production_request.txt", root / "execution_spec.json", video,
             *[file for folder in (root / "skills", root / "materials") for file in folder.rglob("*") if file.is_file()]]
    if accepted:
        files.append(original)
    if correction:
        files.extend([correction_path, receipt, source_spec, source_task, original])
    for path in sorted(files):
        safe_file(root, path)
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(bytes.fromhex(file_sha256(path)))
    return ArtifactValidation(artifact_digest=digest.hexdigest(), card_count=0, total_image_bytes=0,
        images=[], video=ValidatedVideo(path=artifact.video_path, byte_size=video.stat().st_size,
            sha256=artifact.sha256, **{key: metadata[key] for key in
            ("duration_seconds", "width", "height", "has_audio", "video_codec", "audio_codec")}))
