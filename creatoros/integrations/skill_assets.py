"""Bounded image edits in a registered Skill working copy."""
from __future__ import annotations

import io
import os
import re
import shutil
import warnings
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from .producer_skills import MAX_LOCAL_SKILL_BYTES, SkillDigestConflict, _digest


MAX_UPLOAD_BYTES = 4 * 1024 * 1024


class SkillAssetExistsError(ValueError):
    """Adding to an occupied path or replacing an absent path is a conflict."""


class SkillReferencedError(ValueError):
    def __init__(self, referencing_files: list[str]):
        super().__init__("图片仍被 Skill 文本引用，请先修改引用再删除。")
        self.referencing_files = referencing_files


def _target(catalog, skill_id: str, relative_path: str):
    if skill_id == catalog.BUILTIN_ID:
        raise PermissionError("内置 Skill 只读。")
    if (not isinstance(relative_path, str) or not relative_path or len(relative_path) > 512
            or "\\" in relative_path or ":" in relative_path or "\x00" in relative_path):
        raise ValueError("图片路径无效。")
    parts = relative_path.split("/")
    if (len(parts) < 2 or parts[0] != "assets" or len(parts) > 12
            or any(part in {"", ".", ".."} for part in parts)):
        raise ValueError("图片必须位于 assets 内的相对路径。")
    relative = Path(*parts)
    if catalog._sensitive_skill_path(relative) or relative.suffix.lower() not in catalog._IMAGE_SUFFIXES:
        raise ValueError("仅允许 assets 内的 PNG、JPEG、WebP 或 GIF 图片路径。")
    directory, item, _skill = catalog._readable_skill_directory(skill_id)
    if not item["editable"]:
        raise PermissionError("内置 Skill 只读。")
    target = directory.joinpath(*parts)
    for index in range(1, len(parts) + 1):
        candidate = directory.joinpath(*parts[:index])
        if catalog._is_link_or_reparse(candidate) or not candidate.resolve().is_relative_to(directory.resolve()):
            raise ValueError("图片路径包含链接或越界。")
        if index < len(parts) and candidate.exists() and not candidate.is_dir():
            raise ValueError("图片父路径不是目录。")
    if target.exists() and not target.is_file():
        raise ValueError("目标路径不是普通文件。")
    return directory, relative, target


def _check_digest(directory: Path, expected_digest: str):
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_digest):
        raise ValueError("expected_digest 必须是文件列表返回的 Skill digest。")
    current = _digest(directory)
    if current != expected_digest:
        raise SkillDigestConflict(current)


def _validate_image(catalog, relative: Path, raw: bytes):
    if not isinstance(raw, bytes):
        raise TypeError("图片内容必须是字节。")
    if not raw:
        raise ValueError("图片内容不能为空。")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise OverflowError("单张图片最多 4 MiB。")
    expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG",
                ".webp": "WEBP", ".gif": "GIF"}[relative.suffix.lower()]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != expected:
                    raise ValueError("图片内容格式与文件扩展名不一致。")
                frames = getattr(image, "n_frames", 1)
                if frames > 100:
                    raise ValueError("图片动画帧数超过 100。")
                for index in range(frames):
                    image.seek(index)
                    if image.width * image.height > catalog.MAX_IMAGE_PIXELS:
                        raise ValueError("图片尺寸超过预览上限。")
                    image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as error:
        raise ValueError("图片内容无效。") from error


def _backup(catalog, skill_id: str, relative: Path, source: Path):
    destination = catalog.root
    for part in ("trash", "asset-backups", skill_id, uuid4().hex, *relative.parts[:-1]):
        candidate = destination / part
        if catalog._is_link_or_reparse(candidate) or (candidate.exists() and not candidate.is_dir()):
            raise ValueError("Skill 回收目录路径无效。")
        candidate.mkdir(exist_ok=True)
        if not candidate.resolve().is_relative_to(catalog.root):
            raise ValueError("Skill 回收目录路径越界。")
        destination = candidate
    backup = destination / relative.name
    temporary = None
    try:
        with NamedTemporaryFile(mode="wb", prefix=".backup-", dir=backup.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with source.open("rb") as original:
                shutil.copyfileobj(original, stream)
        temporary.replace(backup)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return backup


def _projected_size(catalog, directory: Path, target: Path, incoming: int):
    total = 0
    for file in directory.rglob("*"):
        if catalog._is_link_or_reparse(file) or not file.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Skill 包含不支持的链接或越界文件。")
        if file.is_file():
            total += file.stat().st_size
    if total - (target.stat().st_size if target.exists() else 0) + incoming > MAX_LOCAL_SKILL_BYTES:
        raise OverflowError("Skill 工作目录总量最多 96 MiB。")


def _referencing_files(catalog, directory: Path, relative: Path) -> list[str]:
    target = directory / relative
    references = []
    for document in directory.rglob("*"):
        if catalog._is_link_or_reparse(document) or not document.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Skill 包含不支持的链接或越界文件。")
        if not document.is_file() or document.suffix.lower() not in catalog._TEXT_SUFFIXES:
            continue
        raw = document.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeError as error:
            raise ValueError("Skill 文本文件不是有效 UTF-8，无法确认图片引用。") from error
        from_document = os.path.relpath(target, document.parent).replace("\\", "/")
        candidates = {relative.as_posix(), from_document, "./" + from_document}
        candidates |= {quote(candidate, safe="/.") for candidate in candidates}
        if any(candidate in content for candidate in candidates):
            references.append(document.relative_to(directory).as_posix())
    return sorted(references)


def save_skill_image(catalog, skill_id: str, path: str, raw: bytes,
                     expected_digest: str, *, overwrite: bool = False) -> dict:
    directory, relative, target = _target(catalog, skill_id, path)
    _check_digest(directory, expected_digest)
    if target.exists() != overwrite:
        raise SkillAssetExistsError("新增路径已存在；请明确选择替换。" if target.exists()
                                    else "替换目标不存在；请先刷新文件列表。")
    _validate_image(catalog, relative, raw)
    _projected_size(catalog, directory, target, len(raw))
    if not target.exists() and len(catalog._list_skill_files_locked(skill_id)["files"]) >= catalog.MAX_BROWSE_FILES:
        raise ValueError("Skill 文件数量已达上限。")
    if overwrite:
        _backup(catalog, skill_id, relative, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if catalog._is_link_or_reparse(target.parent) or not target.parent.resolve().is_relative_to(directory.resolve()):
        raise ValueError("图片父路径无效。")
    temporary = None
    try:
        with NamedTemporaryFile(mode="wb", prefix=".skill-image-", dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        if catalog._is_link_or_reparse(target):
            raise ValueError("图片路径包含链接。")
        if target.exists() != overwrite:
            raise SkillAssetExistsError("图片目标已变化，请刷新后重试。")
        temporary.replace(target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"skill_id": skill_id, "path": relative.as_posix(), "kind": "image",
            "digest": _digest(directory), "editable": True,
            "operation": "replace" if overwrite else "add"}


def delete_skill_image(catalog, skill_id: str, path: str, expected_digest: str) -> dict:
    directory, relative, target = _target(catalog, skill_id, path)
    _check_digest(directory, expected_digest)
    if not target.is_file():
        raise SkillAssetExistsError("图片不存在，请刷新文件列表。")
    referenced = _referencing_files(catalog, directory, relative)
    if referenced:
        raise SkillReferencedError(referenced)
    backup = _backup(catalog, skill_id, relative, target)
    try:
        target.unlink()
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return {"skill_id": skill_id, "path": relative.as_posix(), "kind": "image",
            "digest": _digest(directory), "editable": True, "operation": "delete"}
