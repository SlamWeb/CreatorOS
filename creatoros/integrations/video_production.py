"""Video adapter using the same SDK lifecycle and Run queue as image production."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from functools import lru_cache
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import Field

from .codex import (CODEX_EFFORT, CODEX_MODEL, PRODUCTION_RULES, SESSION_FILENAME,
                    CodexProducerError, CodexUsage, ProductionModel, ProductionSession,
                    _bounded_sdk, _production_client)
from .codex_turn_guard import require_completed_turn
from .native_production import safe_file
from .production_progress import ProgressWriter, collect_observed_turn
from .visual_production import atomic_json
from .worker_protocol import record_task, record_thread
from .media_tools import media_tool, probe_media, decode_media
from creatoros.runs.task_contracts import TaskExecutionSpec
from creatoros.runs.task_execution import file_sha256, freeze_execution_spec, verify_execution_spec

VIDEO_MANIFEST = "video_artifact.json"
VIDEO_CHECKPOINT = "video_checkpoint.json"


def video_skill_rules(spec):
    if all(skill.role == "selected" for skill in spec.skills):
        return "结合本次任务要求共同参考所有选中的 Skill 和资源，它们没有预设主次；不要另写融合 Skill。"
    return "完整使用以下原始主/辅助 Skill，辅助不得替换主 Skill；不要另写融合 Skill。"


class VideoDelivery(ProductionModel):
    title: str = Field(min_length=1)
    text: str
    video_path: str = Field(min_length=1, description="Relative MP4 path inside current work directory")
    complete: Literal[True]


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


@dataclass(frozen=True)
class ProducedVideo:
    directory: Path
    pack: VideoArtifact
    session: ProductionSession


def _identity(spec, payload):
    return hashlib.sha256(json.dumps({"spec": spec.public_dict(), "request": payload},
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _require_playable_mp4(metadata):
    if (not metadata["has_video"] or not metadata["has_audio"]
            or "mp4" not in metadata["format"].split(",")):
        raise ValueError("视频必须为真实 MP4，含可解码画面与音轨。")
    if metadata.get("video_codec") != "h264" or metadata.get("audio_codec") != "aac":
        raise ValueError("页面视频交付需要 H.264 画面与 AAC 音轨，请转换编码后交付。")


def _recover_work(directory, expected):
    for previous in sorted(directory.parent.glob("attempt-*"), reverse=True):
        if previous.name >= directory.name or previous.is_symlink() or getattr(previous, "is_junction", lambda: False)():
            continue
        path = previous / VIDEO_CHECKPOINT
        if not path.is_file():
            continue
        checkpoint = VideoCheckpoint.model_validate_json(safe_file(previous, path).read_text(encoding="utf-8"))
        if checkpoint.input_digest != expected:
            raise ValueError("视频恢复输入与本 Revision 固定输入不同。")
        for file in (previous / "work").rglob("*"):
            if file.is_file():
                safe_file(previous / "work", file)
        shutil.copytree(previous / "work", directory / "work", dirs_exist_ok=True)
        return checkpoint
    return None


def _usage_baseline(directory, thread_id):
    if thread_id:
        for path in sorted(directory.parent.parent.glob("revision-*/attempt-*/" + VIDEO_CHECKPOINT), reverse=True):
            if path.parent == directory:
                continue
            prior = VideoCheckpoint.model_validate_json(safe_file(directory.parent.parent, path).read_text(encoding="utf-8"))
            if prior.thread_id == thread_id:
                return prior.usage
    return CodexUsage()


async def _execute_video(producer, directory, spec, payload, checkpoint, request):
    from openai_codex import ApprovalMode, Sandbox, TextInput

    deadline = monotonic() + producer.timeout_seconds
    cancel = request.get("cancel_event")
    progress = ProgressWriter(directory, "production")
    thread_id = checkpoint.thread_id if checkpoint else request.get("thread_id")
    baseline = checkpoint.usage if checkpoint else _usage_baseline(directory, thread_id)
    try:
        async with _production_client(deadline, cancel) as client:
            options = dict(approval_mode=ApprovalMode.deny_all, cwd=str(directory / "work"),
                           model=CODEX_MODEL, sandbox=Sandbox.workspace_write,
                           developer_instructions=PRODUCTION_RULES)
            thread = await _bounded_sdk(client.thread_resume(thread_id, **options) if thread_id else
                                        client.thread_start(**options), deadline, cancel)
            if thread_id and thread.id != thread_id:
                raise ValueError("视频恢复会话不匹配。")
            checkpoint = checkpoint or VideoCheckpoint(input_digest=_identity(spec, payload), thread_id=thread.id)
            atomic_json(directory / VIDEO_CHECKPOINT, checkpoint)
            record_thread(directory, thread.id)
            if request.get("on_thread_started"):
                request["on_thread_started"](thread.id)
            skills = [{"role": skill.role, "skill_id": skill.skill_id,
                       "content": (Path(skill.path) / "SKILL.md").read_text(encoding="utf-8-sig"),
                       "resource_root": str(Path(skill.path).relative_to(directory))}
                      for skill in spec.skills]
            materials = [{**item.model_dump(exclude={"path"}), "path": str(Path(item.path).relative_to(directory))}
                         for item in spec.materials]
            skill_rules = video_skill_rules(spec)
            prompt = (PRODUCTION_RULES + "\n本次交付视频 MP4。" + skill_rules +
                      "所有资源路径相对当前 cwd 的父目录，按本次输入选择并剪辑。只读输入材料，输出仅写当前 work。"
                      "视频输出用 H.264 + AAC、yuv420p MP4，设置 faststart 以便网页播放。"
                      "最终返回 JSON，video_path 是当前 work 下真实 MP4 的相对路径。不要发布。"
                      "如发现缺失工具或素材，明确失败，不伪造已渲染的文件。\n"
                      + ("这是同 Revision 的技术恢复，先检查已有工程和渲染文件，再继续；沿用固定素材。\n" if checkpoint.response or thread_id else "")
                      + json.dumps({"task": payload, "skills": skills, "materials": materials,
                                    "tools": {name: media_tool(name) for name in ("ffmpeg", "ffprobe")}}, ensure_ascii=False))
            (directory / "production_instructions.txt").write_text(prompt, encoding="utf-8")
            turn = await _bounded_sdk(thread.turn([TextInput(text=prompt)], cwd=str(directory / "work"),
                effort=CODEX_EFFORT, model=CODEX_MODEL, sandbox=Sandbox.workspace_write,
                output_schema=VideoDelivery.model_json_schema()), deadline, cancel)
            collector = asyncio.create_task(collect_observed_turn(turn, progress))
            completed = False
            try:
                result = await _bounded_sdk(collector, deadline, cancel)
                require_completed_turn(result)
                completed = True
                checkpoint.response = result.final_response or ""
                checkpoint.turn_completed = True
                checkpoint.usage = producer._sdk_usage(result.usage) if result.usage else baseline
                # Host receipt is saved before file validation; recovery can finish offline.
                atomic_json(directory / VIDEO_CHECKPOINT, checkpoint)
                progress.record_usage({key: max(0, getattr(checkpoint.usage, key) - getattr(baseline, key))
                                       for key in CodexUsage.model_fields})
            finally:
                if not completed:
                    try:
                        await asyncio.wait_for(turn.interrupt(), 5)
                    except Exception:
                        pass
                    if not collector.done():
                        collector.cancel()
                    await asyncio.gather(collector, return_exceptions=True)
            progress.finish("completed")
            return checkpoint
    except Exception:
        progress.finish("interrupted" if cancel and cancel.is_set() else "failed")
        raise


def produce_video(producer, **request):
    try:
        return _produce_video(producer, **request)
    except CodexProducerError:
        raise
    except Exception as error:
        raise CodexProducerError(f"视频生产未完成，已保存的会话和文件保留：{error}",
                                 error_type="video_delivery_failed") from error


def _produce_video(producer, **request):
    from .producer_skills import ProducerSkillCatalog

    if not producer.native_skill_inputs:
        raise CodexProducerError("视频制作需要原生 Codex SDK。", error_type="unsupported_video_production")
    directory = Path(request["directory"]).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "work").mkdir()
    spec = TaskExecutionSpec.model_validate(request["execution_spec"])
    spec = freeze_execution_spec(spec, ProducerSkillCatalog(request["skills_root"]), directory,
                                  trusted_skill_roots=[directory.parent.parent / "skill-snapshot",
                                                       directory.parent.parent / "revision-inputs"])
    verify_execution_spec(spec, directory, ready=True)
    payload = {key: request.get(key) for key in ("pack_id", "creator_id", "series_id", "topic_id", "topic_title",
               "topic_brief", "series_description", "audience", "revision_instruction")}
    (directory / "production_request.txt").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (directory / "execution_spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    record_task(directory, kind="production", scope={key: payload[key] for key in
                ("pack_id", "creator_id", "series_id", "topic_id")},
                input_ref="production_request.txt", deliverable=VIDEO_MANIFEST)
    checkpoint = _recover_work(directory, _identity(spec, payload))
    if checkpoint is None or not checkpoint.turn_completed:
        checkpoint = asyncio.run(_execute_video(producer, directory, spec, payload, checkpoint, request))
    elif request.get("on_thread_started"):
        request["on_thread_started"](checkpoint.thread_id)
    if request.get("cancel_event") and request["cancel_event"].is_set():
        raise CodexProducerError("视频制作已取消。", error_type="codex_interrupted")
    delivery = VideoDelivery.model_validate_json(checkpoint.response or "")
    relative = Path(delivery.video_path)
    if relative.is_absolute() or relative.suffix.lower() != ".mp4":
        raise ValueError("视频交付必须是 work 内 MP4 相对路径。")
    source = safe_file(directory / "work", directory / "work" / relative)
    metadata = probe_media(source)
    decode_media(source)
    _require_playable_mp4(metadata)
    target = directory / "video.mp4"
    shutil.copyfile(source, target)
    artifact = VideoArtifact(**{key: payload[key] for key in
        ("pack_id", "creator_id", "series_id", "topic_id", "topic_title")},
        title=delivery.title, text=delivery.text, generated_at=datetime.now().astimezone().isoformat(),
        video_path="video.mp4", sha256=file_sha256(target))
    atomic_json(directory / VIDEO_MANIFEST, artifact)
    atomic_json(directory / VIDEO_CHECKPOINT, checkpoint)
    session = ProductionSession(thread_id=checkpoint.thread_id, pack_id=payload["pack_id"],
        created_at=artifact.generated_at,
        usage=CodexUsage.model_validate_json((directory / "production_usage.json").read_text(encoding="utf-8"))
              if (directory / "production_usage.json").is_file() else CodexUsage())
    atomic_json(directory / SESSION_FILENAME, session)
    return ProducedVideo(directory, artifact, session)


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
    from creatoros.runs.models import ArtifactValidation, ValidatedVideo

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
    if not checkpoint.turn_completed or checkpoint.input_digest != _identity(spec, payload):
        raise ValueError("视频缺少完整且匹配输入的生产回执。")
    delivery = VideoDelivery.model_validate_json(checkpoint.response or "")
    original = safe_file(root / "work", root / "work" / delivery.video_path)
    if file_sha256(original) != artifact.sha256 or delivery.title != artifact.title or delivery.text != artifact.text:
        raise ValueError("视频与 SDK 已完成回执不一致。")
    metadata = _decoded_video_metadata(str(video), artifact.sha256)
    digest = hashlib.sha256(b"creatoros-video-artifact-v1\0")
    files = [root / VIDEO_MANIFEST, root / VIDEO_CHECKPOINT, root / "production_request.txt", root / "execution_spec.json", video,
             *[file for folder in (root / "skills", root / "materials") for file in folder.rglob("*") if file.is_file()]]
    for path in sorted(files):
        safe_file(root, path)
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(bytes.fromhex(file_sha256(path)))
    return ArtifactValidation(artifact_digest=digest.hexdigest(), card_count=0, total_image_bytes=0,
        images=[], video=ValidatedVideo(path=artifact.video_path, byte_size=video.stat().st_size,
            sha256=artifact.sha256, **{key: metadata[key] for key in
            ("duration_seconds", "width", "height", "has_audio", "video_codec", "audio_codec")}))
