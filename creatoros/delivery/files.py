"""Safe local file access and historical input identity helpers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from creatoros.integrations.atomic_file import atomic_write_text
from .models import ProductionModel


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
