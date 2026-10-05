"""One CLI discovery seam; reuse the pinned SDK runtime without changing PATH."""
from __future__ import annotations

import os
from pathlib import Path
import shutil


def resolve_codex_executable(executable: str | None = None) -> str:
    override = executable or os.environ.get("CREATOROS_CODEX_EXECUTABLE")
    if override:
        found = shutil.which(override) or override
        path = Path(found)
        if not path.is_file() or (os.name == "nt" and path.suffix.lower() != ".exe"):
            raise FileNotFoundError("指定的 Codex 执行文件不存在或不是可直接启动的程序。")
        return str(path.resolve())
    try:
        # Compatibility seam for the pinned openai-codex==0.157.1 dependency.
        from openai_codex.client import CodexConfig, _resolve_codex_bin
        path = Path(_resolve_codex_bin(CodexConfig()))
        if path.is_file():
            return str(path.resolve())
    except (ImportError, FileNotFoundError, OSError):
        pass
    found = shutil.which("codex")
    if found:
        return resolve_codex_executable(found)
    raise FileNotFoundError("未找到 Codex 执行程序；请安装项目声明的 openai-codex 依赖，或设置 CREATOROS_CODEX_EXECUTABLE。")


def default_codex_executable() -> str:
    """App construction must remain possible so health/preflight can explain failure."""
    try:
        return resolve_codex_executable()
    except FileNotFoundError:
        return os.environ.get("CREATOROS_CODEX_EXECUTABLE") or "codex"
