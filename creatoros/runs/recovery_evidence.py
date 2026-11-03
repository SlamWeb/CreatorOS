"""Read-only classification of the latest usable Revision delivery evidence.

The caller must hold the database execution guard and have verified that no
registered writer survives. This module neither mutates Attempts nor starts a
producer. A returned pair is an unretryable (error_type, message); None keeps
the historical recovery policy. Absence of evidence cannot prove that an
external process never ran or that a failed disk persisted every marker.
"""
from __future__ import annotations

import hashlib
import stat
from pathlib import Path

from creatoros.delivery.files import safe_file
from creatoros.delivery.image import CHECKPOINT, SUBMISSION, Checkpoint, accepted_submission
from creatoros.delivery.image_submission_check import inspect_submission
from creatoros.delivery.video import VIDEO_CHECKPOINT, VideoCheckpoint, VideoDelivery
from creatoros.delivery.video_receipt import (
    VideoRecoveryEvidenceConflict, VideoRecoveryUnconfirmed,
    _check_unfinished_submission, known_video_failure,
)


def inspect_recovery_evidence(output_directory: str | Path | None) -> tuple[str, str] | None:
    """Inspect current and earlier Attempts newest-first, stopping at evidence.

    An empty/missing current Attempt cannot mask an earlier guarded Attempt.
    A valid newer checkpoint is the same recovery boundary as image_recovery /
    video_receipt; genuinely old Attempts without submission evidence retain
    their existing behavior. Never open or follow a linked Attempt directory.
    """
    if not output_directory:
        return None
    current = Path(output_directory)
    try:
        candidates = set(current.parent.glob("attempt-*"))
        candidates.add(current)
        for directory in sorted(candidates, reverse=True):
            if directory.name > current.name:
                continue
            try:
                info = directory.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(info.st_mode) or
                    getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                    or not stat.S_ISDIR(info.st_mode)):
                return "delivery_evidence_conflict", "原 Attempt 是链接、重解析点或非目录，无法核对执行证据。"

            image_result = inspect_submission(directory)
            if image_result["status"] == "unconfirmed":
                return "image_delivery_unconfirmed", "图片提交结果待核对，原 Attempt 已保留，不会自动恢复。"
            image_path = directory / CHECKPOINT
            if image_path.exists() or image_path.is_symlink():
                try:
                    image = Checkpoint.model_validate_json(safe_file(directory, image_path, 8_000_000).read_text(encoding="utf-8"))
                    if image.submission_sha256:
                        accepted_submission(directory, image)
                except (OSError, ValueError, TypeError, AttributeError) as error:
                    return "image_delivery_unconfirmed", f"图片 checkpoint 或回执无法核对：{error}"
                # image_recovery stops at the first checkpoint, even for legacy.
                return None

            video_path = directory / VIDEO_CHECKPOINT
            if video_path.exists() or video_path.is_symlink():
                try:
                    video = VideoCheckpoint.model_validate_json(safe_file(directory, video_path, 8_000_000).read_text(encoding="utf-8"))
                    _check_unfinished_submission(directory, video)
                    failure = known_video_failure(video)
                    if failure is not None:
                        return "video_generation_failed", failure.error or failure.text or "Codex 未完成视频制作。"
                    if video.handoff_protocol == "submit-tool-v1" and video.accepted_sha256:
                        received = safe_file(directory, directory / "received-video.mp4")
                        digest = hashlib.sha256()
                        with received.open("rb") as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(chunk)
                        if digest.hexdigest() != video.accepted_sha256:
                            raise VideoRecoveryEvidenceConflict("已接收视频发生变化。")
                        if not video.response or not video.submission_digest or hashlib.sha256(video.response.encode("utf-8")).hexdigest() != video.submission_digest:
                            raise VideoRecoveryEvidenceConflict("视频回执缺失或摘要不一致。")
                        delivery = VideoDelivery.model_validate_json(video.response)
                        if not delivery.complete or not delivery.video_path:
                            raise VideoRecoveryEvidenceConflict("视频接收回执与完成状态不一致。")
                    elif video.handoff_protocol == "submit-tool-v1" and video.turn_completed:
                        raise VideoRecoveryUnconfirmed("视频回合已结束但未确认交付。")
                except VideoRecoveryUnconfirmed as error:
                    return "video_delivery_unconfirmed", str(error)
                except (VideoRecoveryEvidenceConflict, OSError, ValueError, TypeError) as error:
                    return "video_delivery_evidence_conflict", f"视频执行证据无法核对：{error}"
                return None
        return None
    except OSError as error:
        return "delivery_evidence_conflict", f"原 Attempt 无法读取：{error}"
