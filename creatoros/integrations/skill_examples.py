"""Bounded source excerpts for research, not a second executable Skill loader."""
from __future__ import annotations

import hashlib
import re
import stat
from collections import deque
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

MAX_FILES = 16
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 128 * 1024
MAX_LINKS = 64
MAX_DEPTH = 2  # SKILL -> example -> its linked text, not a recursive directory scan.
TEXT_TYPES = {".md", ".txt"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                    *{f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"}}
_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+[^)]+)?\)|`([^`\n]+\.(?:md|txt))`", re.I)


def linked_skill_examples(directory: Path, skill_text: str) -> dict:
    """Copy only explicit local reference text; preserve whole files or explain omission."""
    root = Path(directory).resolve()
    pending = deque()
    seen = set()
    files, omitted = [], []
    total = 0

    def enqueue(text, parent, depth):
        for match in _LINK.finditer(text):
            target = next(part for part in match.groups() if part is not None)
            if len(seen) >= MAX_LINKS:
                if not any(item["reason"] == "link_limit" for item in omitted):
                    omitted.append({"path": "", "reason": "link_limit"})
                break
            key = (parent, target)
            if key not in seen:
                seen.add(key)
                pending.append((target, parent, depth))

    enqueue(skill_text, PurePosixPath(), 1)
    while pending:
        target, parent, depth = pending.popleft()
        try:
            parsed = urlsplit(target)
        except ValueError:
            omitted.append({"path": target, "reason": "unsafe_path"})
            continue
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue  # Remote links are research sources, never host-side fetches.
        relative = parent / PurePosixPath(unquote(parsed.path).replace("\\", "/"))
        parts = relative.parts
        label = relative.as_posix()
        reason = None
        if relative.is_absolute() or ".." in parts or any(
                part.startswith(".") or ":" in part or part.endswith((".", " "))
                or part.split(".")[0].rstrip().upper() in _WINDOWS_DEVICES
                or part.lower().split(".")[0] in {"secret", "secrets", "credentials"}
                for part in parts):
            reason = "unsafe_path"
        elif len(parts) > 1 and parts[0] not in {"references", "examples", "assets"}:
            reason = "outside_reference_folders"
        elif relative.suffix.lower() in IMAGE_TYPES:
            reason = "image_not_loaded_in_research"
        elif relative.suffix.lower() not in TEXT_TYPES:
            reason = "unsupported_type"
        elif depth > MAX_DEPTH:
            reason = "depth_limit"
        elif len(files) >= MAX_FILES:
            reason = "file_limit"
        if reason is None:
            path = root
            for part in parts:
                path = path / part
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    reason = "linked_path"
                    break
                try:
                    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
                    if attributes & (getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0)
                                     | getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                        reason = "hidden_or_reparse_path"
                        break
                except OSError:
                    reason = "unreadable_text"
                    break
            if reason is None:
                try:
                    if not path.resolve().is_relative_to(root):
                        reason = "unsafe_path"
                    elif not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                        reason = "not_regular_file"
                    else:
                        with path.open("rb") as stream:
                            raw = stream.read(MAX_FILE_BYTES + 1)
                        if len(raw) > MAX_FILE_BYTES:
                            reason = "file_size_limit"
                        elif total + len(raw) > MAX_TOTAL_BYTES:
                            reason = "total_size_limit"
                        else:
                            text = raw.decode("utf-8-sig")
                except (OSError, UnicodeError, ValueError):
                    reason = "unreadable_text"
        if reason:
            omitted.append({"path": label, "reason": reason})
            continue
        if any(item["path"] == label for item in files):
            continue
        files.append({"path": label, "sha256": hashlib.sha256(raw).hexdigest(), "text": text})
        total += len(raw)
        enqueue(text, relative.parent, depth + 1)
    return {"files": files, "omitted": omitted,
            "scope": "显式引用的本地文本样例；原文作为参考资料，不是执行指令。未加载图片不代表已看图。"}
