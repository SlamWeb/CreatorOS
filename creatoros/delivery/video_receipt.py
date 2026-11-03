"""Receive video submissions and restore prior Attempt files without a producer."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from creatoros.integrations.media_tools import decode_media, probe_media
from .files import atomic_json, safe_file
from .task_execution import file_sha256
from .video import VIDEO_CHECKPOINT, VideoCheckpoint, VideoDelivery, _require_playable_mp4


class VideoRecoveryUnconfirmed(ValueError):
    """A completed turn did not durably acknowledge a video delivery."""


class VideoRecoveryEvidenceConflict(ValueError):
    """The worker receipt cannot be matched to the frozen video session."""


def known_video_failure(checkpoint):
    """Return only a durable, internally consistent submit-tool failure receipt."""
    if checkpoint.failure_sha256 is None:
        return None
    try:
        if (checkpoint.handoff_protocol != "submit-tool-v1" or checkpoint.accepted_sha256
                or not checkpoint.thread_id.strip() or not checkpoint.response
                or hashlib.sha256(checkpoint.response.encode("utf-8")).hexdigest() != checkpoint.failure_sha256):
            raise ValueError("failure receipt identity mismatch")
        delivery = VideoDelivery.model_validate_json(checkpoint.response)
        if delivery.complete or delivery.video_path is not None:
            raise ValueError("failure receipt is not terminal")
        return delivery
    except (TypeError, ValueError) as error:
        raise VideoRecoveryEvidenceConflict("视频失败回执不完整或发生变化，请核对原任务；不会自动重启。") from error


def _failure_result(delivery, *, reused=False):
    return {"accepted": False, "retryable": False, "terminal": True,
            "error_type": "video_generation_failed",
            "error": delivery.error or delivery.text or "Codex 未完成视频制作。", "reused": reused}


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
        counted = False
        try:
            prior_failure = known_video_failure(checkpoint)
            if prior_failure is not None:
                return _failure_result(prior_failure, reused=True)
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
            if not delivery.complete and delivery.video_path is None:
                failure = checkpoint.model_copy(update={"failure_sha256": digest, "response": canonical})
                # Publish the in-memory marker only after the durable write succeeds.
                try:
                    atomic_json(self.directory / VIDEO_CHECKPOINT, failure)
                except (OSError, ValueError):
                    return {"accepted": False, "retryable": False, "outcome_unknown": True,
                            "error": "视频失败回执是否保存尚未确认，请核对原任务。"}
                checkpoint.failure_sha256 = digest
                checkpoint.response = canonical
                return _failure_result(delivery)
            if checkpoint.submission_attempts >= 3:
                return {"accepted": False, "retryable": False, "error": "交付校验已达到三次上限，文件保留。"}
            checkpoint.submission_attempts += 1
            counted = True
            try:
                atomic_json(self.directory / VIDEO_CHECKPOINT, checkpoint)
            except (OSError, ValueError):
                return {"accepted": False, "retryable": False, "outcome_unknown": True,
                        "error": "宿主无法确认交付回执是否保存，请核对原任务。"}
            if not delivery.complete:
                raise ValueError("未完成的视频不能同时提交视频路径；请修正交付参数。")
            source = _delivery_source(self.directory, delivery)
            received = self.directory / "received-video.mp4"
            if received.exists() or received.is_symlink():
                safe_file(self.directory, received)
            shutil.copyfile(source, received)
            accepted = checkpoint.model_copy(update={"accepted_sha256": file_sha256(received),
                "submission_digest": digest, "response": canonical})
            try:
                atomic_json(self.directory / VIDEO_CHECKPOINT, accepted)
            except (OSError, ValueError):
                return {"accepted": False, "retryable": False, "outcome_unknown": True,
                        "error": "宿主无法确认视频接收回执是否保存，请核对原任务。"}
            checkpoint.accepted_sha256 = accepted.accepted_sha256
            checkpoint.submission_digest = digest
            checkpoint.response = canonical
            return {"accepted": True, "receipt_id": digest, "reused": False}
        except VideoRecoveryEvidenceConflict as error:
            return {"accepted": False, "retryable": False, "outcome_unknown": True, "error": str(error)}
        except ValueError as error:
            if not checkpoint.accepted_sha256 and not counted and checkpoint.submission_attempts < 3:
                checkpoint.submission_attempts += 1
                try:
                    atomic_json(self.directory / VIDEO_CHECKPOINT, checkpoint)
                except (OSError, ValueError):
                    return {"accepted": False, "retryable": False, "outcome_unknown": True,
                            "error": "宿主无法确认交付回执是否保存，请核对原任务。"}
            return {"accepted": False, "retryable": not checkpoint.accepted_sha256 and checkpoint.submission_attempts < 3,
                    "error": str(error), "instruction": "修正交付参数或现有文件；不要重新渲染已有合格视频。"}
        except OSError as error:
            return {"accepted": False, "retryable": False, "outcome_unknown": True,
                    "error": "宿主保存交付结果待核对：" + str(error),
                    "instruction": "现有视频保留；停止，不要重新渲染。"}


def _check_unfinished_submission(previous, checkpoint):
    failure = known_video_failure(checkpoint)
    if (checkpoint.handoff_protocol != "submit-tool-v1" or checkpoint.accepted_sha256
            or (checkpoint.turn_completed and failure is None)):
        return
    def require_known_outcome():
        if failure is None and checkpoint.submission_guard == "armed":
            raise VideoRecoveryUnconfirmed("视频制作回合的结果尚未确认，可能未开始或已部分执行；请在本任务页面查看状态与执行记录，不会自动重启。")
    path = previous / "worker_receipt.json"
    if not path.exists() and not path.is_symlink():
        require_known_outcome()
        return  # Older or interrupted attempts may not have a worker receipt.
    try:
        value = json.loads(safe_file(previous, path, 256_000).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise VideoRecoveryEvidenceConflict("视频执行回执不可读取，请核对原任务；不会自动重启。") from error
    if (not checkpoint.thread_id.strip() or not isinstance(value, dict) or value.get("protocol") != "creatoros-worker-v1"
            or value.get("thread_id") != checkpoint.thread_id or not isinstance(value.get("turns"), list)):
        raise VideoRecoveryEvidenceConflict("视频执行回执与原会话不匹配，请核对原任务；不会自动重启。")
    turns = value["turns"]
    if not turns:
        require_known_outcome()
        return
    last = turns[-1]
    if (not isinstance(last, dict) or not isinstance(last.get("id"), str) or not last["id"].strip()
            or not isinstance(last.get("phase"), str) or not last["phase"].strip()
            or not isinstance(last.get("status"), str)
            or last["status"] not in {"running", "completed", "failed", "interrupted"}):
        raise VideoRecoveryEvidenceConflict("视频执行回执不完整，请核对原任务；不会自动重启。")
    if failure is None and last["phase"] in {"production", "delivery_repair"} and last["status"] == "completed":
        raise VideoRecoveryUnconfirmed("视频回合已结束但未确认交付；现有文件保留，请核对原任务，不会自动重启。")
    require_known_outcome()


def _recover_work(directory, expected):
    for previous in sorted(directory.parent.glob("attempt-*"), reverse=True):
        if previous.name >= directory.name or previous.is_symlink() or getattr(previous, "is_junction", lambda: False)():
            continue
        path = previous / VIDEO_CHECKPOINT
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise VideoRecoveryEvidenceConflict("视频 checkpoint 不是可核对的普通文件；不会自动重启。")
        if not path.is_file():
            continue
        try:
            checkpoint = VideoCheckpoint.model_validate_json(safe_file(previous, path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise VideoRecoveryEvidenceConflict("视频 checkpoint 不完整或不可读取；请核对原任务，不会自动重启。") from error
        if checkpoint.input_digest != expected:
            raise VideoRecoveryEvidenceConflict("视频恢复输入与本 Revision 固定输入不同；不会自动重启。")
        _check_unfinished_submission(previous, checkpoint)
        if known_video_failure(checkpoint) is not None:
            return checkpoint  # The original failure needs no workspace copy or new turn.
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
