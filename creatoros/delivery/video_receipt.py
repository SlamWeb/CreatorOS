"""Receive video submissions and restore prior Attempt files without a producer."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from creatoros.integrations.media_tools import decode_media, probe_media
from .files import atomic_json, safe_file
from .task_execution import file_sha256
from .video import VIDEO_CHECKPOINT, VideoCheckpoint, VideoDelivery, _require_playable_mp4


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
