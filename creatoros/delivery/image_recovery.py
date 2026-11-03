"""Restore a verified native delivery into a new Attempt without starting a turn."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from creatoros.integrations.worker_protocol import completed_delivery_turn

from .files import (AttemptDirectoryConflict, atomic_json, attempt_order,
                    earlier_attempt_directories, is_unsafe_attempt_directory, safe_file)
from .image import (CHECKPOINT, RESPONSE, SUBMISSION, SUBMISSION_INTENT, Artifact, Checkpoint, Delivery,
                    accepted_submission, ingest, load_checkpoint, skill_refs)
from .image_assets import ImageAssetStore
from .image_materials import accept_delivery_value, accept_response, prepare_materials
from .image_submission_check import inspect_submission


class ImageSubmissionUnconfirmed(ValueError):
    """Saved submission or intent cannot safely authorize another turn."""


def _copy_evidence(source: Path, target: Path) -> None:
    """Persist the guard first; a partial copy must remain visible to recovery."""
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())


def recover_checkpoint(directory: Path, digest: str, generated_root: Path | None = None) -> Checkpoint | None:
    if attempt_order(directory.name) is None:
        return None
    try:
        previous_attempts = earlier_attempt_directories(directory)
    except AttemptDirectoryConflict as error:
        raise ImageSubmissionUnconfirmed(str(error)) from error
    for previous in previous_attempts:
        if is_unsafe_attempt_directory(previous):
            raise ImageSubmissionUnconfirmed("原 Attempt 不是可安全核对的普通目录。")
        evidence = inspect_submission(previous)
        if evidence["status"] == "unconfirmed":
            raise ImageSubmissionUnconfirmed("图片提交结果待核对；原 Attempt 回执和图片已保留，不会启动新回合。")
        if evidence["intent_present"]:
            _copy_evidence(safe_file(previous, previous / SUBMISSION_INTENT, 32_000), directory / SUBMISSION_INTENT)
            _copy_evidence(safe_file(previous, previous / "worker_receipt.json", 256_000), directory / "worker_receipt.json")
        if not (previous / CHECKPOINT).exists():
            continue
        value = load_checkpoint(previous, digest)
        if value is None:
            raise ValueError("上次生产输入或证据变化，拒绝自动恢复。")
        ImageAssetStore(directory, generated_root or Path(), value.thread_id, digest).restore_from(previous)
        if value.handoff_protocol == "tool-v1" and value.submission_sha256:
            accepted_submission(previous, value)
            shutil.copy2(previous / SUBMISSION, directory / SUBMISSION)
        (directory / "partial-images").mkdir(exist_ok=True)
        for page in value.pages:
            target = directory / "partial-images" / Path(page.image_path).name
            shutil.copy2(page.image_path, target)
            page.image_path = str(target.resolve())
        # Preserve this Revision's original working notes, not other task memory.
        if (previous / "work").is_dir():
            for source in (previous / "work").rglob("*"):
                if source.is_symlink():
                    raise ValueError("恢复工作文件不能包含链接。")
                if source.is_file():
                    safe_file(previous / "work", source, 8_000_000)
                    target = directory / "work" / source.relative_to(previous / "work")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        # Rebase only the verified index to the new cwd; retain raw prior index
        # for a producer to recover any not-yet-verified delivery on request.
        prior_index = directory / "work/delivery.json"
        if prior_index.is_file():
            shutil.copy2(prior_index, directory / "work/previous-delivery.json")
        if (value.handoff_protocol == "tool-v1" and value.submission_sha256
                and not value.turn_completed and completed_delivery_turn(previous, value.thread_id)):
            saved = accepted_submission(directory, value)
            bundle = prepare_materials(directory, skill_refs(directory),
                                       (directory / "production_request.txt").read_text(encoding="utf-8"))
            accept_delivery_value(directory, value, generated_root or Path(), bundle, saved["submission"])
            ingest(directory, value, generated_root or Path())
            value.turn_completed = True
            if not evidence["intent_present"]:
                shutil.copy2(previous / "worker_receipt.json", directory / "worker_receipt.json")
        if (value.handoff_protocol == "structured-v1" and not value.turn_completed
                and (directory / "work" / RESPONSE).is_file()
                and completed_delivery_turn(previous, value.thread_id)):
            shutil.copy2(previous / "worker_receipt.json", directory / "worker_receipt.json")
            bundle = prepare_materials(directory, skill_refs(directory),
                                       (directory / "production_request.txt").read_text(encoding="utf-8"))
            try:
                accept_response(directory, value, generated_root or Path(), bundle)
                ingest(directory, value, generated_root or Path())
                value.turn_completed = True
            except ValueError:
                pass  # Invalid model output still needs the bounded index-only turn.
        if value.turn_completed:
            value.delivery.artifacts = [Artifact(**p.model_dump(include=set(Artifact.model_fields))) for p in value.pages]
            atomic_json(prior_index, value.delivery)
        elif value.handoff_protocol == "file-v1" and prior_index.is_file():
            # Latest work may be newer than the preview. Preserve and revalidate it,
            # rebasing only paths inside the exact prior attempt, never arbitrary text.
            try:
                latest = Delivery.model_validate_json(prior_index.read_text(encoding="utf-8-sig"))
                for item in latest.artifacts:
                    item.reference_assets = [Path(ref).relative_to(previous).as_posix()
                        if Path(ref).is_absolute() and Path(ref).is_relative_to(previous) else ref
                        for ref in item.reference_assets]
                    if item.content_file and Path(item.content_file).is_absolute():
                        item.content_file = Path(item.content_file).relative_to(previous / "work").as_posix()
                    if item.source_image_path:
                        source = Path(item.source_image_path)
                        if source.is_absolute() and source.is_relative_to(previous / "work"):
                            item.source_image_path = str(directory / "work" / source.relative_to(previous / "work"))
                atomic_json(prior_index, latest)
                if generated_root is not None:
                    ingest(directory, value, generated_root)
                if (generated_root is not None and value.delivery.complete and value.pages
                        and completed_delivery_turn(previous, value.thread_id)):
                    value.turn_completed = True
                    shutil.copy2(previous / "worker_receipt.json", directory / "worker_receipt.json")
            except (ValueError, OSError):
                pass  # Keep the last verified preview; the worker can repair the index.
        atomic_json(directory / CHECKPOINT, value)
        return value
    return None
