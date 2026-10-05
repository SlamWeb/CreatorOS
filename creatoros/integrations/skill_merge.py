"""Bounded, immutable snapshots used by native Skill fusion drafts."""
from __future__ import annotations

import hashlib
import json
import shutil
import stat
from pathlib import Path

from .producer_skills import _digest, _inspect_checkout

MAX_SKILLS = 8
MAX_FILES = 2_000
MAX_BYTES = 32 * 1024 * 1024


class UnsupportedMergeSource(ValueError):
    """A cataloged source contains a file type that the merge path cannot retain."""


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _validate_path_chain(base: Path, target: Path, *, allow_missing: bool) -> None:
    """Reject links/reparse points and resolved escapes before creating or opening a target."""
    base = Path(base)
    target = Path(target)
    if _is_link_or_reparse(base) or not base.is_dir():
        raise ValueError("融合草稿目录路径无效。")
    lexical_base = base.absolute()
    lexical_target = target.absolute()
    try:
        relative = lexical_target.relative_to(lexical_base)
    except ValueError as error:
        raise ValueError("融合草稿文件路径越界。") from error
    resolved_base = base.resolve(strict=True)
    current = base
    for index, part in enumerate(relative.parts):
        current = current / part
        final = index == len(relative.parts) - 1
        if _is_link_or_reparse(current):
            raise ValueError("融合草稿包含链接或重解析点，拒绝写入。")
        if not current.exists():
            if not allow_missing:
                raise ValueError("融合草稿目录缺失。")
            continue
        resolved = current.resolve(strict=True)
        if not resolved.is_relative_to(resolved_base):
            raise ValueError("融合草稿文件路径越界。")
        if not final and not current.is_dir():
            raise ValueError("融合草稿路径中间项不是目录。")


def _namespace(index: int, skill_id: str) -> str:
    safe_id = "".join(char for char in skill_id if char.isalnum() or char == "-")[:48]
    return f"source-{index:02d}-{safe_id}"


def _copy_snapshot(source: Path, target: Path, catalog, skill_id: str, counters: dict) -> dict:
    """Copy only files already exposed by the catalog's safe file browser."""
    before = _digest(source)
    listing = catalog.list_skill_files(skill_id)
    target.mkdir(parents=True)
    files = []
    for entry in listing["files"]:
        if entry["kind"] == "unsupported":
            raise UnsupportedMergeSource(
                "所选 Skill 含有当前融合流程不支持的文件类型（例如 PDF）；请先移除或转换后重试。")
        relative = Path(entry["path"])
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("Skill 来源包含无效路径。")
        source_file = source.joinpath(*relative.parts)
        if source_file.is_symlink() or not source_file.resolve().is_relative_to(source.resolve()):
            raise ValueError("Skill 来源包含链接或越界文件。")
        size = source_file.stat().st_size
        counters["files"] += 1
        counters["bytes"] += size
        if counters["files"] > MAX_FILES or counters["bytes"] > MAX_BYTES:
            raise ValueError("所选 Skill 的安全快照超过 2,000 个文件或 32 MiB。")
        output_relative = relative
        # The native loader treats an exact nested SKILL.md as another Skill.
        if output_relative.name.casefold() == "skill.md":
            if len(output_relative.parts) == 1:
                output_relative = output_relative.with_name("SOURCE-SKILL.md")
            else:
                suffix = hashlib.sha256(output_relative.as_posix().encode()).hexdigest()[:10]
                output_relative = output_relative.with_name(f"SOURCE-SKILL-{suffix}.md")
        destination = target.joinpath(*output_relative.parts)
        if destination.exists() or destination.is_symlink():
            raise ValueError("Skill 来源重命名后出现文件路径冲突，拒绝覆盖。")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_bytes = source_file.read_bytes()
        copied_digest = hashlib.sha256(source_bytes).hexdigest()
        destination.write_bytes(source_bytes)
        if hashlib.sha256(source_file.read_bytes()).hexdigest() != copied_digest:
            raise ValueError("Skill 来源在快照期间发生变化，请重新选择。")
        files.append({"path": output_relative.as_posix(), "kind": entry["kind"],
                      "size": size, "digest": copied_digest})
    after = _digest(source)
    if before != after:
        raise ValueError("Skill 来源在快照期间发生变化，请重新选择。")
    return {"digest": before, "files": files}


def snapshot_skills(catalog, skill_ids: list[str], job_directory: Path) -> tuple[list[dict], dict]:
    """Freeze registered local sources before a fusion operation starts."""
    source_root = job_directory / "sources"
    source_root.mkdir(parents=True)
    counters = {"files": 0, "bytes": 0}
    metadata, context = [], []
    output_kind = "text"
    for index, skill_id in enumerate(skill_ids, 1):
        source, item, skill = catalog._readable_skill_directory(skill_id)
        namespace = _namespace(index, skill_id)
        copied = _copy_snapshot(source, source_root / namespace, catalog, skill_id, counters)
        role = item.get("role") or "legacy_end_to_end"
        description = {"id": skill_id, "name": skill.name, "role": role,
                       "digest": copied["digest"]}
        metadata.append(description)
        context.append({**description, "directory": namespace, "files": copied["files"]})
        if role != "mind":
            try:
                if _inspect_checkout(source, ".").carousel_compatible:
                    output_kind = "image-carousel"
            except ValueError:
                pass
    return metadata, {"sources": context, "output_kind": output_kind,
                      "source_bytes": counters["bytes"], "source_files": counters["files"]}


def seed_model_sources(job_directory: Path, operation_directory: Path) -> None:
    """Make a disposable model-readable copy; the authoritative snapshot stays outside cwd."""
    context = json.loads((job_directory / "merge_context.json").read_text(encoding="utf-8"))
    source_root = job_directory / "sources"
    target_root = operation_directory / "_source_inputs"
    target_root.mkdir(parents=True, exist_ok=True)
    for source in context["sources"]:
        source_dir = source_root / source["directory"]
        for entry in source["files"]:
            relative = Path(entry["path"])
            origin = source_dir.joinpath(*relative.parts)
            target = target_root / source["directory"] / relative
            if origin.is_symlink() or not origin.resolve().is_relative_to(source_dir.resolve()):
                raise ValueError("融合来源快照路径无效。")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)


def retain_source_material(job_directory: Path, result) -> None:
    """Copy source files into the single Skill draft under source-specific names."""
    context_path = job_directory / "merge_context.json"
    if not context_path.is_file():
        return
    context = json.loads(context_path.read_text(encoding="utf-8"))
    result_directory = result._directories.get("legacy_end_to_end")
    if result_directory is None or not result_directory.is_dir():
        raise ValueError("融合草稿缺少可写的单 Skill 目录。")
    result_directory = Path(result_directory)
    _validate_path_chain(job_directory, result_directory, allow_missing=False)
    source_root = job_directory / "sources"
    destination_root = result_directory / "assets" / "fusion-sources"
    for source in context["sources"]:
        origin_root = source_root / source["directory"]
        target_root = destination_root / source["directory"]
        for entry in source["files"]:
            relative = Path(entry["path"])
            origin = origin_root.joinpath(*relative.parts)
            target = target_root.joinpath(*relative.parts)
            _validate_path_chain(job_directory, origin, allow_missing=False)
            if origin.is_symlink() or not origin.resolve().is_relative_to(origin_root.resolve()):
                raise ValueError("融合来源快照路径无效。")
            if hashlib.sha256(origin.read_bytes()).hexdigest() != entry["digest"]:
                raise ValueError("融合来源快照已变化，无法确保保留原资源。")
            _validate_path_chain(result_directory, target, allow_missing=True)
            if target.exists():
                # Never replace model output with source bytes silently.
                if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != entry["digest"]:
                    raise ValueError("融合草稿与保留的来源文件发生冲突，请改稿处理该路径。")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _validate_path_chain(result_directory, target, allow_missing=True)
            with target.open("xb") as output, origin.open("rb") as source:
                shutil.copyfileobj(source, output)


def prompt_context(operation_directory: Path, output_directory: Path, instruction: str) -> str:
    """Return a compact, user-facing source brief for the Codex request."""
    context_path = operation_directory / "merge_context.json"
    if not context_path.is_file():
        return ""
    context = json.loads(context_path.read_text(encoding="utf-8"))
    rows = []
    for source in context["sources"]:
        folder = output_directory / "_source_inputs" / source["directory"]
        rows.append(
            f"- {source['name']}\n"
            f"  读取说明：{(folder / 'SOURCE-SKILL.md').resolve()}\n"
            f"  配套文件目录：{folder.resolve()}\n"
            f"  成稿中的资源目录：assets/fusion-sources/{source['directory']}/"
        )
    return (
        "你要把用户已经选中的多个本机 Skill 融合成一个新的、可独立使用的 Skill。"
        "逐个读取下列 SOURCE-SKILL.md 和其目录中的配套文件，提炼兼容的做法；不要把原文简单拼接。"
        "_source_inputs 只读，绝不修改；成稿应使用宿主为资源保留的相对路径，不引用本机绝对路径。"
        "当来源规则互相冲突时，不要自行静默取舍，请在最终说明中列出冲突和待用户决定的问题。"
        "将完整、可编辑的 Skill 写到当前工作区 skill/SKILL.md；只修改该新草稿，不入库、不试产、不生图、不发布。"
        f"\n用户融合要求：{json.dumps(instruction, ensure_ascii=False)}\n所选来源：\n"
        + "\n".join(rows)
    )


def default_output_kind(operation_directory: Path) -> str:
    path = operation_directory / "merge_context.json"
    if not path.is_file():
        return "text"
    return json.loads(path.read_text(encoding="utf-8")).get("output_kind", "text")
