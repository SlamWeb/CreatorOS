"""Video adapter using the same SDK lifecycle and Run queue as image production."""
from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic

from creatoros.delivery.files import safe_file
from creatoros.delivery.task_contracts import TaskExecutionSpec
from creatoros.delivery.task_execution import file_sha256, freeze_execution_spec, verify_execution_spec
from creatoros.delivery.video import (VIDEO_MANIFEST, VIDEO_CHECKPOINT, VideoArtifact, VideoCheckpoint,
    VideoDelivery, _decoded_video_metadata, _identity, _require_playable_mp4,
    validate_video_artifact, video_delivery_schema)
from creatoros.delivery.video_receipt import (VideoRecoveryEvidenceConflict, VideoRecoveryUnconfirmed,
                                               VideoSubmission, _delivery_source, _recover_work,
                                               known_video_failure)

from .codex import (CODEX_EFFORT, CODEX_MODEL, PRODUCTION_RULES, SESSION_FILENAME,
                    CodexProducerError, CodexUsage, ProductionSession,
                    _bounded_sdk, _production_client)
from .codex_turn_guard import require_completed_turn
from .media_tools import media_tool, probe_media, decode_media
from .production_progress import ProgressWriter, collect_observed_turn
from .visual_production import atomic_json
from .worker_protocol import record_task, record_thread


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
            + ("已提供该音频的分析文件 bgm_analysis，请先读取并复用其中 analysis、receipt 和 onsets；"
               "音频文件另以 audio 提供。分析结果是候选，注意片段起点；按任务需要使用，不要重复分析。\n"
               if any(item.kind == "bgm_analysis" for item in spec.materials) else "")
            + ("这是同 Revision 的技术恢复，先检查已有工程和渲染文件，再继续；沿用固定素材。\n" if recovery else "")
            + json.dumps({"task": payload, "skills": skills, "materials": materials,
                          "tools": {name: media_tool(name) for name in ("ffmpeg", "ffprobe")}}, ensure_ascii=False))


@dataclass(frozen=True)
class ProducedVideo:
    directory: Path
    pack: VideoArtifact
    session: ProductionSession


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
    submission_stop = asyncio.Event()
    stop_result = None
    async def submit(arguments):
        nonlocal stop_result
        async with submission_lock:
            if submission_stop.is_set():
                return stop_result
            result = await asyncio.to_thread(VideoSubmission(directory, checkpoint), arguments)
            if result.get("terminal") or result.get("outcome_unknown"):
                stop_result = result
                submission_stop.set()
            return result
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
            if use_submit_tool:
                # Persist uncertainty before dispatch: no model turn is started if
                # this write fails, while a later crash can never look like proof
                # that an interrupted turn was safe to start again.
                checkpoint.submission_guard = "armed"
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
            terminal_observed = False
            def observe_terminal(event):
                nonlocal terminal_observed
                if event.method != "turn/completed":
                    return
                payload = event.payload
                reported = getattr(payload, "turn", None)
                status = getattr(reported, "status", None)
                status = getattr(status, "value", status)
                if (turn.id and turn.thread_id and getattr(payload, "thread_id", None) == turn.thread_id
                        and getattr(reported, "id", None) == turn.id
                        and status in {"completed", "interrupted", "failed"}):
                    terminal_observed = True
            class TerminalBoundTurn:
                id, thread_id = turn.id, turn.thread_id
                async def stream(self):
                    source = turn.stream()
                    try:
                        async for event in source:
                            yield event
                            if submission_stop.is_set() and terminal_observed:
                                return
                    finally:
                        await source.aclose()
            collector = asyncio.create_task(collect_observed_turn(
                TerminalBoundTurn(), progress, observe_terminal, public_observer_is_diagnostic=False))
            stop_waiter = asyncio.create_task(submission_stop.wait())
            completed = False
            try:
                await _bounded_sdk(asyncio.wait({collector, stop_waiter}, return_when=asyncio.FIRST_COMPLETED),
                                   deadline, cancel)
                if submission_stop.is_set():
                    # The callback has already saved the failure receipt. Stop the
                    # current turn rather than asking the model to retry rendering.
                    if collector.done():
                        await asyncio.gather(collector, return_exceptions=True)
                        # The SDK also raises for an observed failed turn.  Only
                        # an explicit terminal event for this thread/turn proves
                        # that a subsequent interrupt is unnecessary.
                        completed = terminal_observed
                    if stop_result.get("terminal"):
                        raise CodexProducerError(stop_result["error"], error_type="video_generation_failed")
                    raise CodexProducerError(stop_result["error"], error_type="video_delivery_unconfirmed")
                result = await collector
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
                    raise CodexProducerError("Codex 已结束但未确认视频交付；现有文件保留，请核对原任务，不会自动重启。",
                                             error_type="video_delivery_unconfirmed")
            finally:
                stop_waiter.cancel()
                await asyncio.gather(stop_waiter, return_exceptions=True)
                if not completed:
                    interrupt_error = None
                    try:
                        await asyncio.wait_for(turn.interrupt(), 5)
                    except Exception as error:
                        interrupt_error = error
                    if not collector.done():
                        collector.cancel()
                    await asyncio.gather(collector, return_exceptions=True)
                    # The turn may have ended while interrupt() was in flight.
                    # Its matching terminal event is stronger evidence than a
                    # rejected interrupt for an already-finished turn.
                    if interrupt_error is not None and not terminal_observed and cancel is not None and cancel.is_set():
                        raise CodexProducerError("停止请求未得到 Codex 确认，请核对线程状态后再重试。", error_type="codex_interrupt_unconfirmed") from interrupt_error
                    if interrupt_error is not None and not terminal_observed and submission_stop.is_set():
                        raise CodexProducerError(stop_result["error"] + "；停止请求未得到 Codex 确认，请核对线程状态。",
                                                 error_type="codex_interrupt_unconfirmed") from interrupt_error
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
    try:
        checkpoint = _recover_work(directory, _identity(spec, payload))
    except VideoRecoveryUnconfirmed as error:
        raise CodexProducerError(str(error), error_type="video_delivery_unconfirmed") from error
    except VideoRecoveryEvidenceConflict as error:
        raise CodexProducerError(str(error), error_type="video_delivery_evidence_conflict") from error
    if checkpoint is not None:
        failure = known_video_failure(checkpoint)
        if failure is not None:
            raise CodexProducerError(failure.error or failure.text or "Codex 未完成视频制作。",
                                     error_type="video_generation_failed")
    accepted = checkpoint is not None and checkpoint.handoff_protocol == "submit-tool-v1" and checkpoint.accepted_sha256
    if checkpoint is not None and checkpoint.turn_completed and checkpoint.handoff_protocol == "submit-tool-v1" and not accepted:
        raise CodexProducerError("视频回合已结束但未确认交付；现有文件保留，请核对原任务，不会自动重启。",
                                 error_type="video_delivery_unconfirmed")
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
