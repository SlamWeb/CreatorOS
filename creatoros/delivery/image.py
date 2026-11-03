"""Native image delivery receipts, input identity and evidence verification.

This module receives and checks produced local files. It never executes a producer.
The native-v1 evidence file set and byte hashing are persisted protocol contracts.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import Field

from creatoros.integrations.atomic_file import atomic_write_text
from creatoros.integrations.producer_skills import _digest
from creatoros.integrations.topic_references import frozen_references

from .files import atomic_json, input_digest, safe_file
from .image_assets import INVENTORY, VIEW, ImageAssetStore
from .models import CodexUsage, ProductionModel

CHECKPOINT = "native_checkpoint.json"
MATERIALS = "input-materials.json"
RESPONSE = "structured-response.json"
SUBMISSION = "artifact-submission.json"
SUBMISSION_INTENT = "artifact-submission-intent.json"


class CompositionReview(ProductionModel):
    status: Literal["ready", "needs_input", "blocked"]
    note: str = Field(min_length=1, max_length=3000)


class Artifact(ProductionModel):
    order: int = Field(ge=1)
    asset_id: str | None = Field(default=None, pattern=r"^img_[a-f0-9]{24}$")
    source_image_path: str | None = Field(default=None, min_length=1)
    image_prompt: str = Field(min_length=1)
    reference_assets: list[str] = Field(default_factory=list)
    content_file: str | None = None


class Delivery(ProductionModel):
    title: str = ""
    text: str = ""
    hashtags: list[str] = Field(default_factory=list)
    complete: bool = False
    artifacts: list[Artifact] = Field(default_factory=list)


class SavedArtifact(Artifact):
    source_image_path: str = Field(min_length=1)
    image_path: str
    sha256: str
    content: str = ""
    warnings: list[str] = Field(default_factory=list)


class Checkpoint(ProductionModel):
    protocol: Literal["native-v1"] = "native-v1"
    handoff_protocol: Literal["file-v1", "structured-v1", "tool-v1"] = "file-v1"
    response_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    submission_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    submission_failures: int = Field(default=0, ge=0, le=3)
    input_digest: str
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    skill_digests: dict[str, str]
    pages: list[SavedArtifact] = Field(default_factory=list)
    delivery: Delivery = Field(default_factory=Delivery)
    turn_completed: bool = False
    repair_attempted: bool = False
    usage: CodexUsage = Field(default_factory=CodexUsage)
    preexisting_thread_images: dict[str, str] = Field(default_factory=dict)
    composition_review: CompositionReview | None = None
    prompt_provenance: Literal["producer_reported_not_image_service_verified"] = "producer_reported_not_image_service_verified"


def accepted_submission(directory, checkpoint):
    raw = safe_file(directory, directory / SUBMISSION, 8_000_000).read_bytes()
    if not checkpoint.submission_sha256 or hashlib.sha256(raw).hexdigest() != checkpoint.submission_sha256:
        raise ValueError("提交回执缺少宿主凭证或已改变。")
    state = json.loads(raw)
    if (state.get("thread_id") != checkpoint.thread_id or state.get("input_digest") != checkpoint.input_digest
            or not state.get("accepted") or not state.get("turn_id")):
        raise ValueError("提交回执不属于本次任务。")
    return state


def skill_refs(directory: Path) -> list[tuple[str, Path]]:
    return [(p.parent.name, p) for p in sorted((directory / "skills").glob("*/SKILL.md"))]


def request_digest(directory: Path) -> str:
    refs = skill_refs(directory)
    if not refs:
        raise ValueError("缺少本次冻结的 Skill。")
    request = safe_file(directory, directory / "production_request.txt", 2_000_000)
    digest = input_digest(request.read_text(encoding="utf-8"), refs)
    references = frozen_references(directory)
    # Old tasks keep byte-for-byte compatible digests. The bundle identity covers
    # original instructions, presentation mode, ordering and every original hash.
    return (hashlib.sha256((digest + "\nreferences:" + references.id).encode()).hexdigest()
            if references else digest)


def safe_reference(directory: Path, path: Path) -> Path:
    if path.resolve().is_relative_to((directory / "skills").resolve()):
        return safe_file(directory / "skills", path)
    references = frozen_references(directory)
    if references and path.absolute() in {Path(item.path).absolute() for item in references.images}:
        return safe_file(directory / "references", path)
    # A known producer mistake omits exactly the Attempt segment. Only this
    # Revision's precise frozen-skill suffix is recoverable, never a global name.
    misplaced = directory.parent / "skills"
    if path.absolute().is_relative_to(misplaced):
        target = safe_file(directory / "skills", directory / "skills" / path.absolute().relative_to(misplaced))
        if path.exists() or path.is_symlink():
            original = safe_file(misplaced, path)
            if original.read_bytes() != target.read_bytes():
                raise ValueError("错层参考文件与本次冻结 Skill 不一致。")
        return target
    raise ValueError("参考文件必须属于本次冻结 Skill 或本次任务参考图。")


def verified_pages(directory: Path, checkpoint: Checkpoint) -> list[SavedArtifact]:
    if checkpoint.handoff_protocol == "tool-v1" and checkpoint.submission_sha256:
        accepted_submission(directory, checkpoint)
    if checkpoint.handoff_protocol == "structured-v1" and checkpoint.turn_completed:
        raw = safe_file(directory / "work", directory / "work" / RESPONSE, 8_000_000).read_bytes()
        if not checkpoint.response_sha256 or hashlib.sha256(raw).hexdigest() != checkpoint.response_sha256:
            raise ValueError("已完成的SDK原始响应发生变化，不能继续交付。")
    if checkpoint.composition_review and checkpoint.composition_review.status != "ready" and checkpoint.pages:
        raise ValueError("Skill 组合尚待澄清，不能接受图片交付。")
    orders = [p.order for p in checkpoint.pages]
    if orders != list(range(1, len(orders) + 1)) or len({p.sha256 for p in checkpoint.pages}) != len(orders):
        raise ValueError("图片顺序或重复内容无效。")
    for page in checkpoint.pages:
        path = safe_file(directory / "partial-images", Path(page.image_path))
        if hashlib.sha256(path.read_bytes()).hexdigest() != page.sha256:
            raise ValueError("已保存图片发生变化。")
        with Image.open(path) as image:
            image.verify()
        if page.asset_id is not None:
            asset, _ = ImageAssetStore(directory, Path(), checkpoint.thread_id, checkpoint.input_digest).resolve(page.asset_id)
            if asset.sha256 != page.sha256 or asset.source_image_path != page.source_image_path:
                raise ValueError("最终图片与SDK接收记录不一致。")
        for ref in page.reference_assets:
            safe_reference(directory, directory / ref)
    return checkpoint.pages


def load_checkpoint(directory: Path, digest: str | None = None) -> Checkpoint | None:
    try:
        path = safe_file(directory, directory / CHECKPOINT, 8_000_000)
        value = Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))
        current = request_digest(directory)
        if value.input_digest != current or (digest is not None and digest != current):
            return None
        if value.skill_digests != {role: _digest(path.parent) for role, path in skill_refs(directory)}:
            return None
        verified_pages(directory, value)
        return value
    except (ValueError, OSError):
        return None


def evidence_files(directory: Path, composition=None) -> list[Path]:
    value = load_checkpoint(directory)
    if value is None or not value.turn_completed or not value.delivery.complete or not value.pages:
        raise ValueError("原生生产证据不完整。")
    if len(value.delivery.artifacts) != len(value.pages):
        raise ValueError("最终图片与交付索引不一致。")
    if composition is not None and value.skill_digests != {
            role: getattr(composition, role).digest for role in ("mind", "production")}:
        raise ValueError("产物与栏目 Skill 版本不一致。")
    # This evidence set is part of native-v1's persisted digest contract. New
    # evidence must get a versioned protocol, not invalidate completed receipts.
    return [directory / CHECKPOINT, directory / "production_request.txt",
            *([directory / MATERIALS, directory / "artifact-submission.json"]
              if value.handoff_protocol == "tool-v1" else []),
            *([directory / MATERIALS, directory / "work" / RESPONSE]
              if value.handoff_protocol == "structured-v1" else []),
            *([directory / INVENTORY] if (directory / INVENTORY).is_file() else []),
            *sorted(p for p in (directory / "skills").rglob("*") if p.is_file()),
            *sorted(p for p in (directory / "references").rglob("*") if p.is_file())]


def native_image_source(directory: Path, generated_thread: Path, raw: str) -> Path:
    """Accept a work copy only when it proves the exact current-thread bytes."""
    path = Path(raw)
    if not path.is_absolute():
        path = directory / "work" / path
    if path.resolve().is_relative_to(generated_thread.resolve()):
        return safe_file(generated_thread, path)
    copy = safe_file(directory / "work", path)
    digest = hashlib.sha256(copy.read_bytes()).hexdigest()
    if generated_thread.is_dir():
        for candidate in sorted(generated_thread.iterdir()):
            if candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            original = safe_file(generated_thread, candidate)
            if hashlib.sha256(original.read_bytes()).hexdigest() == digest:
                return original
    raise ValueError("工作目录图片未匹配当前 thread 的原始生图文件，拒绝接收；不会重新生图。")


def ingest(directory: Path, checkpoint: Checkpoint, generated_root: Path) -> Delivery:
    path = safe_file(directory / "work", directory / "work/delivery.json", 8_000_000)
    raw = path.read_bytes()
    delivery = Delivery.model_validate_json(raw.decode("utf-8-sig"))
    generated_thread = generated_root / checkpoint.thread_id
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", checkpoint.thread_id) or not generated_thread.resolve().is_relative_to(generated_root.resolve()):
        raise ValueError("生成 thread 目录越界。")
    if [p.order for p in delivery.artifacts] != list(range(1, len(delivery.artifacts) + 1)):
        raise ValueError("交付图片必须从 1 连续编号。")
    if len(delivery.artifacts) < len(checkpoint.pages):
        raise ValueError("交付不能移除已保存图片。")
    # While the worker is running these are previews, not immutable deliverables.
    # Validate the whole new index before publishing it, retaining previous images
    # by hash so a failed/partial write cannot corrupt a visible checkpoint.
    pages, resolutions = [], []
    for item in delivery.artifacts:
        original_item = item.model_dump()
        refs = []
        for ref in item.reference_assets:
            candidate = Path(ref) if Path(ref).is_absolute() else directory / ref
            refs.append(safe_reference(directory, candidate).relative_to(directory).as_posix())
        content, content_file = "", None
        if item.content_file:
            file = Path(item.content_file)
            file = file if file.is_absolute() else directory / "work" / file
            file = safe_file(directory / "work", file, 2_000_000)
            content = file.read_text(encoding="utf-8-sig")
            content_file = file.relative_to(directory / "work").as_posix()
        store = ImageAssetStore(directory, generated_root, checkpoint.thread_id, checkpoint.input_digest)
        if store.path.exists() or store.path.is_symlink():
            if item.asset_id:
                asset, source = store.resolve(item.asset_id)
            elif item.source_image_path:
                asset, source = store.resolve_path(item.source_image_path)
            else:
                raise ValueError(f"交付缺少图片编号；请读取 {VIEW} 后选择，不要重新生图。")
            item.asset_id, item.source_image_path = asset.asset_id, asset.source_image_path
        else:
            if item.asset_id or not item.source_image_path:
                raise ValueError("该图片编号没有本次SDK接收记录。")
            source = native_image_source(directory, generated_thread, item.source_image_path)
            item.source_image_path = str(source)
        if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("不支持的图片格式。")
        with Image.open(source) as image:
            image.verify()
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        if original_item["source_image_path"] != item.source_image_path or original_item["reference_assets"] != refs:
            resolutions.append({"order": item.order, "source_image_path": original_item["source_image_path"],
                "verified_source_image_path": item.source_image_path, "asset_id": item.asset_id, "sha256": sha,
                "reference_assets": original_item["reference_assets"], "verified_reference_assets": refs})
        if sha in {p.sha256 for p in pages}:
            raise ValueError("多个页重复交付同一图片。")
        target = directory / "partial-images" / f"{item.order:02d}-{sha}{source.suffix.lower()}"
        target.parent.mkdir(exist_ok=True)
        page = SavedArtifact(**{**item.model_dump(), "reference_assets": refs, "content_file": content_file},
                             image_path=str(target.resolve()), sha256=sha, content=content)
        if item.order <= len(checkpoint.pages):
            saved = checkpoint.pages[item.order - 1]
            # Reuse previously checked local bytes when the source is unchanged.
            if page.model_dump(exclude={"image_path", "warnings"}) == saved.model_dump(exclude={"image_path", "warnings"}):
                page = saved
            elif checkpoint.turn_completed:
                raise ValueError("最终交付已冻结，不能隐式修改图片、Prompt、引用或内容。")
        if not Path(page.image_path).exists():
            shutil.copy2(source, page.image_path)
        if hashlib.sha256(Path(page.image_path).read_bytes()).hexdigest() != sha:
            raise ValueError("已保存图片发生变化。")
        pages.append(page)
    if checkpoint.turn_completed and delivery != checkpoint.delivery:
        raise ValueError("最终交付已冻结，不能修改交付索引。")
    if resolutions:
        audit = {"thread_id": checkpoint.thread_id, "raw_index_sha256": hashlib.sha256(raw).hexdigest(),
                 "resolutions": resolutions}
        audit_path = directory / "delivery_path_resolution.json"
        if audit_path.is_symlink() or getattr(audit_path, "is_junction", lambda: False)():
            raise ValueError("路径解析证据不能为链接。")
        if not audit_path.is_file() or json.loads(audit_path.read_text(encoding="utf-8")) != audit:
            atomic_write_text(audit_path, json.dumps(audit, ensure_ascii=False, indent=2))
    changed = [p.order for p in pages if p.order > len(checkpoint.pages)
               or p != checkpoint.pages[p.order - 1]]
    if changed:
        with (directory / "codex_trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "delivery.updated", "orders": changed,
                                    "at": datetime.now().astimezone().isoformat()}) + "\n")
    checkpoint.pages = pages
    checkpoint.delivery = delivery
    atomic_json(directory / CHECKPOINT, checkpoint)
    return delivery
