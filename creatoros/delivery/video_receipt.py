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


def _check_unfinished_submission(previous, checkpoint):
    if checkpoint.handoff_protocol != "submit-tool-v1" or checkpoint.accepted_sha256 or checkpoint.turn_completed:
        return
    path = previous / "worker_receipt.json"
    if not path.exists() and not path.is_symlink():
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
        return
    last = turns[-1]
    if (not isinstance(last, dict) or not isinstance(last.get("id"), str) or not last["id"].strip()
            or not isinstance(last.get("phase"), str) or not last["phase"].strip()
            or not isinstance(last.get("status"), str)
            or last["status"] not in {"running", "completed", "failed", "interrupted"}):
        raise VideoRecoveryEvidenceConflict("视频执行回执不完整，请核对原任务；不会自动重启。")
    if last["phase"] in {"production", "delivery_repair"} and last["status"] == "completed":
        raise VideoRecoveryUnconfirmed("视频回合已结束但未确认交付；现有文件保留，请核对原任务，不会自动重启。")


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
        _check_unfinished_submission(previous, checkpoint)
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
