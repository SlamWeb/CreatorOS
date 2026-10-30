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


def _video_prompt(spec, payload, directory, *, recovery=False, submit_tool=False):
    skills = [{"role": skill.role, "skill_id": skill.skill_id,
               "content": (Path(skill.path) / "SKILL.md").read_text(encoding="utf-8-sig"),
               "resource_root": str(Path(skill.path).relative_to(directory))}
              for skill in spec.skills]
    materials = [{**item.model_dump(exclude={"path"}), "path": str(Path(item.path).relative_to(directory))}
                 for item in spec.materials]
    return (PRODUCTION_RULES + "\n本次交付视频 MP4。" + video_skill_rules(spec) +
            "所有资源路径相对当前 cwd 的父目录，按本次输入选择并剪辑。只读输入材料，输出仅写当前 work。"
            "视频输出用 H.264 + AAC、yuv420p MP4，设置 faststart 以便网页播放。" +
            ("完成后调用 submit_artifacts，参数 complete=true、video_path 为当前 work 下真实 MP4 相对路径。"
             "必须收到 accepted=true 才能结束；工具拒绝时按具体错误修复现有交付，不要重新渲染已有合格视频。"
             "工具返回 retryable=false 时停止并说明原因。最终回复只需简短说明，不再重复交付 JSON。不要发布。"
             if submit_tool else "最终返回 JSON，成功时 complete=true、video_path 为当前 work 下真实 MP4 的相对路径。不要发布。") +
            "缺失工具或素材时 complete=false、video_path=null，并在 error 中写明实际原因，不伪造文件。\n"
            + ("用户提供的 BGM 分享原文如下，请按选中剪辑 Skill 的指引自行获取和使用音频；无需 CreatorOS 预先解析：\n"
               + spec.bgm_reference + "\n" if spec.bgm_reference else "")
            + ("这是同 Revision 的技术恢复，先检查已有工程和渲染文件，再继续；沿用固定素材。\n" if recovery else "")
            + json.dumps({"task": payload, "skills": skills, "materials": materials,
                          "tools": {name: media_tool(name) for name in ("ffmpeg", "ffprobe")}}, ensure_ascii=False))


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


def _delivery_source(directory, delivery):
    if not delivery.complete:
        raise ValueError(delivery.error or delivery.text or "Codex 未完成视频制作。")
    if not delivery.video_path:
        raise ValueError("缺少视频路径。请提交已生成的 MP4，不要重新渲染。")
    relative = Path(delivery.video_path)
    if relative.is_absolute() or relative.suffix.lower() != ".mp4":
        raise ValueError("视频交付必须是 work 内 MP4 相对路径。")
    source = safe_file(directory / "work", directory / "work" / relative)
    metadata = probe_media(source)
    decode_media(source)
    _require_playable_mp4(metadata)
    return source


class VideoSubmission:
    """Validate and durably acknowledge delivery while the producer is active."""
    def __init__(self, directory, checkpoint):
        self.directory, self.checkpoint = directory, checkpoint

    def __call__(self, arguments):
        checkpoint = self.checkpoint
        try:
            if not checkpoint.accepted_sha256:
                if checkpoint.submission_attempts >= 3:
                    return {"accepted": False, "retryable": False, "error": "交付校验已达到三次上限，文件保留。"}
                checkpoint.submission_attempts += 1
                atomic_json(self.directory / VIDEO_CHECKPOINT, checkpoint)
            delivery = VideoDelivery.model_validate(arguments)
            canonical = delivery.model_dump_json()
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            if checkpoint.accepted_sha256:
                if digest != checkpoint.submission_digest:
                    return {"accepted": False, "retryable": False, "error": "已接收另一份交付，不能覆盖。"}
                source = safe_file(self.directory, self.directory / "received-video.mp4")
                if file_sha256(source) != checkpoint.accepted_sha256:
                    raise ValueError("已接收视频发生变化。")
                return {"accepted": True, "receipt_id": digest, "reused": True}
            source = _delivery_source(self.directory, delivery)
            received = self.directory / "received-video.mp4"
            if received.exists() or received.is_symlink():
                safe_file(self.directory, received)
            shutil.copyfile(source, received)
            accepted = checkpoint.model_copy(update={"accepted_sha256": file_sha256(received),
                "submission_digest": digest, "response": canonical})
            atomic_json(self.directory / VIDEO_CHECKPOINT, accepted)
            checkpoint.accepted_sha256 = accepted.accepted_sha256
            checkpoint.submission_digest = digest
            checkpoint.response = canonical
            return {"accepted": True, "receipt_id": digest, "reused": False}
        except ValueError as error:
            atomic_json(self.directory / VIDEO_CHECKPOINT, checkpoint)
            return {"accepted": False, "retryable": not checkpoint.accepted_sha256 and checkpoint.submission_attempts < 3,
                    "error": str(error), "instruction": "修正交付参数或现有文件；不要重新渲染已有合格视频。"}
        except OSError as error:
            return {"accepted": False, "retryable": False, "error": "宿主保存交付失败：" + str(error),
                    "instruction": "现有视频保留；停止，不要重新渲染。"}


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
        if checkpoint.handoff_protocol == "submit-tool-v1" and checkpoint.accepted_sha256:
            accepted = safe_file(previous, previous / "received-video.mp4")
            if file_sha256(accepted) != checkpoint.accepted_sha256:
                raise ValueError("已接收视频发生变化。")
            shutil.copyfile(accepted, directory / "received-video.mp4")
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


def _thread_has_submission_tool(directory, thread_id):
    if not thread_id:
        return True
    for path in sorted(directory.parent.parent.glob("revision-*/attempt-*/" + VIDEO_CHECKPOINT), reverse=True):
        if path.parent == directory:
            continue
        prior = VideoCheckpoint.model_validate_json(safe_file(directory.parent.parent, path).read_text(encoding="utf-8"))
        if prior.thread_id == thread_id:
            return prior.handoff_protocol == "submit-tool-v1"
    return False


async def _execute_video(producer, directory, spec, payload, checkpoint, request):
    from openai_codex import ApprovalMode, Sandbox, TextInput
    from .codex import CodexDynamicTool

    deadline = monotonic() + producer.timeout_seconds
    cancel = request.get("cancel_event")
    progress = ProgressWriter(directory, "production")
    thread_id = checkpoint.thread_id if checkpoint else request.get("thread_id")
    baseline = checkpoint.usage if checkpoint else _usage_baseline(directory, thread_id)
    use_submit_tool = ((checkpoint is None and _thread_has_submission_tool(directory, thread_id)) or
                       (checkpoint is not None and checkpoint.handoff_protocol == "submit-tool-v1"))
    submission_lock = asyncio.Lock()
    async def submit(arguments):
        async with submission_lock:
            return await asyncio.to_thread(VideoSubmission(directory, checkpoint), arguments)
    tools = [CodexDynamicTool("submit_artifacts", "提交已完成的 MP4 及文案，立即返回验收结果；收到 accepted=true 后才完成。",
        video_delivery_schema(), submit)] if use_submit_tool else None
    try:
        client_options = {"dynamic_tools": tools} if tools else {}
        async with _production_client(deadline, cancel, **client_options) as client:
            # A supplied external BGM may require browser access. Let the SDK's
            # reviewer evaluate those requests instead of denying them outright.
            options = dict(approval_mode=ApprovalMode.auto_review if spec.bgm_reference else ApprovalMode.deny_all, cwd=str(directory / "work"),
                           model=CODEX_MODEL, sandbox=Sandbox.workspace_write,
                           developer_instructions=PRODUCTION_RULES)
            thread = await _bounded_sdk(client.thread_resume(thread_id, **options) if thread_id else
                                        client.thread_start(**options), deadline, cancel)
            if thread_id and thread.id != thread_id:
                raise ValueError("视频恢复会话不匹配。")
            checkpoint = checkpoint or VideoCheckpoint(input_digest=_identity(spec, payload), thread_id=thread.id,
                handoff_protocol="submit-tool-v1" if use_submit_tool else "final-json-v1")
            atomic_json(directory / VIDEO_CHECKPOINT, checkpoint)
            record_thread(directory, thread.id)
            if request.get("on_thread_started"):
                request["on_thread_started"](thread.id)
            prompt = _video_prompt(spec, payload, directory, recovery=bool(checkpoint.response or thread_id),
                                   submit_tool=use_submit_tool)
            (directory / "production_instructions.txt").write_text(prompt, encoding="utf-8")
            turn_options = {} if use_submit_tool else {"output_schema": video_delivery_schema()}
            turn = await _bounded_sdk(thread.turn([TextInput(text=prompt)], cwd=str(directory / "work"),
                effort=CODEX_EFFORT, model=CODEX_MODEL, sandbox=Sandbox.workspace_write,
                **turn_options), deadline, cancel)
            collector = asyncio.create_task(collect_observed_turn(turn, progress))
            completed = False
            try:
                result = await _bounded_sdk(collector, deadline, cancel)
                require_completed_turn(result)
                completed = True
                if not use_submit_tool:
                    checkpoint.response = result.final_response or ""
                checkpoint.turn_completed = True
                checkpoint.usage = producer._sdk_usage(result.usage) if result.usage else baseline
                # Host receipt is saved before file validation; recovery can finish offline.
                atomic_json(directory / VIDEO_CHECKPOINT, checkpoint)
                progress.record_usage({key: max(0, getattr(checkpoint.usage, key) - getattr(baseline, key))
                                       for key in CodexUsage.model_fields})
                if use_submit_tool and not checkpoint.accepted_sha256:
                    raise ValueError("Codex 已结束但未通过 submit_artifacts 交付；现有文件保留。")
            finally:
                if not completed:
                    interrupt_error = None
                    try:
                        await asyncio.wait_for(turn.interrupt(), 5)
                    except Exception as error:
                        interrupt_error = error
                    if not collector.done():
                        collector.cancel()
                    await asyncio.gather(collector, return_exceptions=True)
                    if interrupt_error is not None and cancel is not None and cancel.is_set():
                        raise CodexProducerError("停止请求未得到 Codex 确认，请核对线程状态后再重试。", error_type="codex_interrupt_unconfirmed") from interrupt_error
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
    accepted = checkpoint is not None and checkpoint.handoff_protocol == "submit-tool-v1" and checkpoint.accepted_sha256
    if not accepted and (checkpoint is None or not checkpoint.turn_completed or
                         checkpoint.handoff_protocol == "submit-tool-v1"):
        checkpoint = asyncio.run(_execute_video(producer, directory, spec, payload, checkpoint, request))
    elif request.get("on_thread_started"):
        request["on_thread_started"](checkpoint.thread_id)
    if request.get("cancel_event") and request["cancel_event"].is_set():
        raise CodexProducerError("视频制作已取消。", error_type="codex_interrupted")
    delivery = VideoDelivery.model_validate_json(checkpoint.response or "")
    if not delivery.complete:
        raise CodexProducerError(delivery.error or delivery.text or "Codex 未完成视频制作。",
                                 error_type="video_generation_failed")
    if not delivery.video_path:
        raise ValueError("Codex 未交付视频：" + (delivery.error or delivery.text or "缺少 MP4 路径。")[:1500])
    relative = Path(delivery.video_path)
    if relative.is_absolute() or relative.suffix.lower() != ".mp4":
        raise ValueError("视频交付必须是 work 内 MP4 相对路径。" + (delivery.error or delivery.text or "")[:1500])
    source = (safe_file(directory, directory / "received-video.mp4") if accepted or checkpoint.accepted_sha256 else
              safe_file(directory / "work", directory / "work" / relative))
    if checkpoint.accepted_sha256 and file_sha256(source) != checkpoint.accepted_sha256:
        raise ValueError("已接收视频发生变化。")
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
    accepted = checkpoint.handoff_protocol == "submit-tool-v1" and checkpoint.accepted_sha256
    if (not checkpoint.turn_completed and not accepted) or checkpoint.input_digest != _identity(spec, payload):
        raise ValueError("视频缺少完整且匹配输入的生产回执。")
    delivery = VideoDelivery.model_validate_json(checkpoint.response or "")
    original = (safe_file(root, root / "received-video.mp4") if accepted else
                safe_file(root / "work", root / "work" / delivery.video_path))
    if accepted and file_sha256(original) != checkpoint.accepted_sha256:
        raise ValueError("已接收视频发生变化。")
    if file_sha256(original) != artifact.sha256 or delivery.title != artifact.title or delivery.text != artifact.text:
        raise ValueError("视频与 SDK 已完成回执不一致。")
    metadata = _decoded_video_metadata(str(video), artifact.sha256)
    digest = hashlib.sha256(b"creatoros-video-artifact-v1\0")
    files = [root / VIDEO_MANIFEST, root / VIDEO_CHECKPOINT, root / "production_request.txt", root / "execution_spec.json", video,
             *[file for folder in (root / "skills", root / "materials") for file in folder.rglob("*") if file.is_file()]]
    if accepted:
        files.append(original)
    for path in sorted(files):
        safe_file(root, path)
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(bytes.fromhex(file_sha256(path)))
    return ArtifactValidation(artifact_digest=digest.hexdigest(), card_count=0, total_image_bytes=0,
        images=[], video=ValidatedVideo(path=artifact.video_path, byte_size=video.stat().st_size,
            sha256=artifact.sha256, **{key: metadata[key] for key in
            ("duration_seconds", "width", "height", "has_audio", "video_codec", "audio_codec")}))
