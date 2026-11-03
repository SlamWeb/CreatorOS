"""Safe local file access and historical input identity helpers."""
from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path

from creatoros.integrations.atomic_file import atomic_write_text
from .models import ProductionModel


class AttemptDirectoryConflict(ValueError):
    """An Attempt name or numeric identity cannot be safely resolved."""


def attempt_order(name: str) -> tuple[int, str] | None:
    """Numeric ordering without a fixed digit width or integer conversion limit."""
    match = re.fullmatch(r"attempt-([0-9]+)", name)
    if not match:
        return None
    digits = match.group(1).lstrip("0")
    return (len(digits), digits) if digits else None


def earlier_attempt_levels(current: Path) -> list[tuple[Path, ...]]:
    """Group earlier Attempts by numeric identity, newest first."""
    current = Path(current)
    current_key = attempt_order(current.name)
    if current_key is None:
        if current.name.startswith("attempt-"):
            raise AttemptDirectoryConflict("当前 Attempt 目录名称或编号无效，无法安全恢复。")
        return []
    known = {}
    for candidate in current.parent.glob("attempt-*"):
        key = attempt_order(candidate.name)
        if key is None or key > current_key:
            continue
        if key == current_key:
            if current.name != candidate.name:
                raise AttemptDirectoryConflict("当前 Attempt 目录编号存在多个别名，无法安全恢复。")
            continue
        known.setdefault(key, []).append(candidate)
    return [tuple(known[key]) for key in sorted(known, reverse=True)]


def require_single_attempt(level: tuple[Path, ...]) -> Path:
    """Reject aliases only when traversal reaches their numeric level."""
    if len(level) != 1:
        raise AttemptDirectoryConflict("Attempt 目录编号存在多个别名，无法安全选择恢复证据。")
    return level[0]


def is_unsafe_attempt_directory(path: Path) -> bool:
    info = path.lstat()
    return bool(stat.S_ISLNK(info.st_mode) or
                getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                or not stat.S_ISDIR(info.st_mode))


def safe_file(root: Path, path: Path, limit: int | None = None) -> Path:
    root, path = root.resolve(), path.absolute()
    if not path.resolve().is_relative_to(root) or any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
            for p in [path, *path.parents] if p != root):
        raise ValueError("文件越界或为链接。")
    if not path.is_file() or (limit is not None and path.stat().st_size > limit):
        raise ValueError("文件缺失或过大。")
    return path


def atomic_json(path: Path, value: ProductionModel) -> None:
    if path.is_symlink():
        raise ValueError("checkpoint 写入路径不能是符号链接。")
    atomic_write_text(path, value.model_dump_json(indent=2))


def input_digest(prompt: str, skills: list[tuple[str, Path]]) -> str:
    from creatoros.integrations.producer_skills import _digest
    payload = [prompt, *[_digest(path.parent) for _, path in skills]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
