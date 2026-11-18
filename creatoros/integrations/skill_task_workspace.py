"""Session-scoped copies of authorized inputs and immutable chat downloads."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from tempfile import NamedTemporaryFile, TemporaryDirectory
from time import sleep
from typing import BinaryIO, Sequence

from .producer_skills import MAX_LOCAL_SKILL_BYTES, ProducerSkillCatalog, _SKILL_EDIT_LOCK


_READABLE = ("skills", "inputs", "work", "out")
_WRITABLE = ("work", "out")
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,119}")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_CHUNK = 1024 * 1024
_MAX_TEXT_BYTES = 512 * 1024
_MAX_INPUT_BYTES = 128 * _CHUNK
_MAX_OUTPUT_BYTES = 128 * _CHUNK
_MAX_OUTPUT_TOTAL = 256 * _CHUNK
_MAX_OUTPUTS = 20
_MAX_METADATA_BYTES = 64 * 1024


@dataclass(frozen=True)
class SkillTaskSkill:
    skill_id: str
    source: Path
    digest: str


@dataclass(frozen=True)
class SkillTaskInput:
    name: str
    source: Path
    sha256: str


def _component(name: str) -> str:
    if (not name or name in {".", ".."} or name.endswith((".", " "))
            or _INVALID_FILENAME.search(name) or PureWindowsPath(name).is_reserved()
            or len(name.encode("utf-16-le")) > 510):
        raise ValueError("任务文件路径无效。")
    return name


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("任务标识无效。")
    return _component(value)


def _digest_value(value: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError("任务输入摘要无效。")
    return value


def _relative(value: str, scopes: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("任务文件路径无效。")
    parts = tuple(map(_component, value.split("/")))
    if len(parts) < 2 or parts[0] not in scopes:
        raise ValueError("任务文件路径超出允许的目录。")
    return parts


def _plain_stat(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("任务文件不允许符号链接或目录联接。")
    return info


def _directory(path: Path, *, create: bool = False) -> None:
    for part in (*reversed(path.parents), path):
        if part.name == "..":
            raise ValueError("任务目录不能含有上级路径。")
        if create:
            part.mkdir(exist_ok=True)
        if not stat.S_ISDIR(_plain_stat(part).st_mode):
            raise ValueError("任务目录无效。")


def _file_stat(path: Path) -> os.stat_result:
    info = _plain_stat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("任务文件必须是普通文件，且不能是硬链接。")
    return info


def _file_version(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)


@contextmanager
def _open_regular(path: Path, limit: int):
    _directory(path.parent)
    before = _file_stat(path)
    if before.st_size > limit:
        raise ValueError("任务文件超过大小上限。")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _file_version(opened) != _file_version(before):
            raise ValueError("任务文件在打开时发生变化。")
        yield stream
        if (_file_version(os.fstat(stream.fileno())) != _file_version(before)
                or _file_version(_file_stat(path)) != _file_version(before)):
            raise ValueError("任务文件在读取时发生变化。")


def _copy_digest(path: Path, limit: int, target: BinaryIO | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with _open_regular(path, limit) as source:
        for chunk in iter(lambda: source.read(_CHUNK), b""):
            size += len(chunk)
            if size > limit:
                raise ValueError("任务文件超过大小上限。")
            digest.update(chunk)
            if target is not None:
                target.write(chunk)
    return digest.hexdigest(), size


def _read_bytes(path: Path, limit: int) -> bytes:
    with _open_regular(path, limit) as source:
        content = source.read(limit + 1)
        if len(content) > limit:
            raise ValueError("任务文件超过大小上限。")
    return content


def _walk_files(root: Path, max_entries: int):
    _directory(root)
    files = []
    stack = [root]
    entries = 0
    while stack:
        directory = stack.pop()
        _directory(directory)
        for path in directory.iterdir():
            _component(path.name)
            entries += 1
            if entries > max_entries:
                raise ValueError("任务文件或目录数量超过上限。")
            info = _plain_stat(path)
            if stat.S_ISDIR(info.st_mode):
                stack.append(path)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                files.append((path.relative_to(root), info.st_size))
            else:
                raise ValueError("任务文件必须是普通文件，且不能是硬链接。")
    return sorted(files)


def _skill_snapshot(source: Path, target: Path | None = None) -> tuple[str, str]:
    """Hash in catalog order, copying only the files exposed by the catalog."""
    full_digest, visible_digest = hashlib.sha256(), hashlib.sha256()
    total = 0
    for relative, _ in _walk_files(source, 2000):
        visible = not ProducerSkillCatalog._sensitive_skill_path(relative)
        if target is not None and visible:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                digest, size = _copy_digest(source / relative, MAX_LOCAL_SKILL_BYTES - total, stream)
        else:
            digest, size = _copy_digest(source / relative, MAX_LOCAL_SKILL_BYTES - total)
        total += size
        entry = relative.as_posix().encode("utf-8") + b"\0" + bytes.fromhex(digest)
        full_digest.update(entry)
        if visible:
            visible_digest.update(entry)
    return full_digest.hexdigest(), visible_digest.hexdigest()


def _input_path(index: int, name: str) -> str:
    if not isinstance(name, str) or not 1 <= len(name) <= 200:
        raise ValueError("任务素材名称无效。")
    safe_name = _INVALID_FILENAME.sub("_", name).strip(" .")[:120] or "input"
    return f"inputs/{_component(f'{index:02d}-{safe_name}')}"


def _install_directory(staging: Path, target: Path) -> None:
    # A Windows reader may momentarily hold a handle without FILE_SHARE_DELETE.
    for wait in (0.05, 0.1, 0.2, None):
        try:
            staging.rename(target)
            return
        except OSError as error:
            if target.is_dir():  # A concurrent retry installed a complete directory.
                return
            if wait is None or os.name != "nt" or getattr(error, "winerror", None) != 5:
                raise
            sleep(wait)


def _read_json(path: Path) -> dict:
    value = json.loads(_read_bytes(path, _MAX_METADATA_BYTES))
    if not isinstance(value, dict):
        raise ValueError("任务文件清单无效。")
    return value


def _write_json(path: Path, value: dict) -> None:
    content = json.dumps(value, ensure_ascii=False).encode("utf-8")
    if len(content) > _MAX_METADATA_BYTES:
        raise ValueError("任务文件清单超过大小上限。")
    path.write_bytes(content)


class SkillTaskWorkspaceStore:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    def _task(self, session_id: str, task_id: str) -> Path:
        return self.root / _identifier(session_id) / _identifier(task_id)

    def create(self, session_id: str, task_id: str, *, skills: Sequence[SkillTaskSkill],
               inputs: Sequence[SkillTaskInput] = ()) -> SkillTaskWorkspace:
        task = self._task(session_id, task_id)
        skill_rows = [{"skill_id": _identifier(skill.skill_id), "digest": _digest_value(skill.digest),
                       "path": f"skills/{skill.skill_id}/SKILL.md"} for skill in skills]
        if len({row["skill_id"] for row in skill_rows}) != len(skill_rows):
            raise ValueError("任务 Skill 不能重复。")
        input_rows = [{"name": item.name, "sha256": _digest_value(item.sha256), "path": _input_path(i, item.name)}
                      for i, item in enumerate(inputs, 1)]
        if len(skill_rows) > 10 or len(input_rows) > 20:
            raise ValueError("任务 Skill 或素材数量超过上限。")

        _directory(task.parent, create=True)
        if not task.exists():
            with TemporaryDirectory(prefix=".task-", dir=task.parent) as temporary:
                staging = Path(temporary) / "task"
                root = staging / "workspace"
                for directory in _READABLE:
                    (root / directory).mkdir(parents=True)
                snapshot_digests = []
                with _SKILL_EDIT_LOCK:
                    for skill, row in zip(skills, skill_rows):
                        source = Path(skill.source).absolute()
                        _directory(source)
                        _file_stat(source / "SKILL.md")
                        destination = (root / row["path"]).parent
                        digest, visible_digest = _skill_snapshot(source, destination)
                        if digest != skill.digest or _skill_snapshot(source)[0] != digest:
                            raise ValueError("Skill 文件与本次固定版本不一致。")
                        snapshot_digests.append(visible_digest)
                for item, row in zip(inputs, input_rows):
                    with (root / row["path"]).open("xb") as stream:
                        digest, _ = _copy_digest(Path(item.source).absolute(), _MAX_INPUT_BYTES, stream)
                    if digest != item.sha256:
                        raise ValueError("任务素材与本次固定内容不一致。")
                _write_json(staging / "manifest.json", {"skills": skill_rows, "inputs": input_rows,
                                                        "snapshot_digests": snapshot_digests})
                _install_directory(staging, task)

        workspace = self.open(session_id, task_id)
        if workspace.manifest != {"skills": skill_rows, "inputs": input_rows}:
            raise ValueError("已有任务工作区与本次固定输入不一致。")
        workspace.verify_inputs()
        return workspace

    def open(self, session_id: str, task_id: str) -> SkillTaskWorkspace:
        task = self._task(session_id, task_id)
        return SkillTaskWorkspace(task, _read_json(task / "manifest.json"))


class SkillTaskWorkspace:
    def __init__(self, task: Path, manifest: dict):
        self.root = task / "workspace"
        self.skills = self.root / "skills"
        self.inputs = self.root / "inputs"
        self.work = self.root / "work"
        self.out = self.root / "out"
        self._delivery = task / "delivery"
        self._manifest = manifest

    @property
    def manifest(self) -> dict:
        return {section: [dict(row) for row in self._manifest[section]] for section in ("skills", "inputs")}

    @property
    def skill_files(self) -> list[str]:
        return [row["path"] for row in self._manifest["skills"]]

    @property
    def input_files(self) -> list[str]:
        return [row["path"] for row in self._manifest["inputs"]]

    def verify_inputs(self) -> None:
        for path in (self.skills, self.inputs, self.work, self.out):
            _directory(path)
        skill_roots = {path.name for path in self.skills.iterdir()}
        expected_roots = {row["skill_id"] for row in self._manifest["skills"]}
        input_names = {path.as_posix() for path, _ in _walk_files(self.inputs, 40)}
        if skill_roots != expected_roots or input_names != {path.removeprefix("inputs/") for path in self.input_files}:
            raise ValueError("任务冻结输入清单已变化。")
        for path, expected in zip(self.skill_files, self._manifest["snapshot_digests"], strict=True):
            body = self.root.joinpath(*_relative(path, ("skills",)))
            if _skill_snapshot(body.parent)[0] != expected:
                raise ValueError("任务冻结 Skill 已变化。")
        for row in self._manifest["inputs"]:
            path = self.root.joinpath(*_relative(row["path"], ("inputs",)))
            if _copy_digest(path, _MAX_INPUT_BYTES)[0] != row["sha256"]:
                raise ValueError("任务冻结素材已变化。")

    def list_files(self) -> list[dict]:
        return [{"path": f"{name}/{path.as_posix()}", "size_bytes": size}
                for name in _READABLE for path, size in _walk_files(self.root / name, 4000)]

    def read_text(self, path: str) -> str:
        target = self.root.joinpath(*_relative(path, _READABLE))
        try:
            return _read_bytes(target, _MAX_TEXT_BYTES).decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("任务文件不是 UTF-8 文本。") from error

    def write_text(self, path: str, content: str) -> dict:
        target = self.root.joinpath(*_relative(path, _WRITABLE))
        if not isinstance(content, str):
            raise ValueError("任务文件内容必须是文本。")
        data = content.encode("utf-8")
        if len(data) > _MAX_TEXT_BYTES:
            raise ValueError("任务文本超过大小上限。")
        _directory(target.parent, create=True)
        try:
            _file_stat(target)
        except FileNotFoundError:
            pass
        with NamedTemporaryFile(dir=target.parent, prefix=".text-", delete=False) as temporary:
            staging = Path(temporary.name)
            try:
                temporary.write(data)
                temporary.close()
                staging.replace(target)
            finally:
                staging.unlink(missing_ok=True)
        return {"path": path, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def _receipt(self) -> list[dict]:
        receipt = _read_json(self._delivery / "receipt.json")
        items = receipt.get("files")
        if not isinstance(items, list) or not 1 <= len(items) <= _MAX_OUTPUTS:
            raise ValueError("交付文件清单无效。")
        return items

    def publish(self, paths: Sequence[str]) -> list[dict]:
        if isinstance(paths, str) or not 1 <= len(paths) <= _MAX_OUTPUTS:
            raise ValueError("请提交 1 至 20 个交付文件。")
        paths = list(paths)
        parts = [_relative(path, ("out",)) for path in paths]
        if len({path.casefold() for path in paths}) != len(paths):
            raise ValueError("交付文件不能重复。")
        _directory(self._delivery.parent)
        if not self._delivery.exists():
            with TemporaryDirectory(prefix=".delivery-", dir=self._delivery.parent) as temporary:
                staging = Path(temporary) / "delivery"
                staging.mkdir()
                items = []
                remaining = _MAX_OUTPUT_TOTAL
                for index, (path, relative) in enumerate(zip(paths, parts)):
                    with (staging / str(index)).open("xb") as stream:
                        digest, size = _copy_digest(self.root.joinpath(*relative),
                                                    min(remaining, _MAX_OUTPUT_BYTES), stream)
                    if size == 0:
                        raise ValueError("交付文件不能为空。")
                    remaining -= size
                    items.append({"path": path, "name": relative[-1], "sha256": digest, "size_bytes": size,
                                  "media_type": mimetypes.guess_type(path)[0] or "application/octet-stream"})
                _write_json(staging / "receipt.json", {"files": items})
                _install_directory(staging, self._delivery)
        receipt = self._receipt()
        if [row["path"] for row in receipt] != paths:
            raise ValueError("本次任务已经提交了交付文件。")
        for index, item in enumerate(receipt):
            if _copy_digest(self._delivery / str(index), _MAX_OUTPUT_BYTES) != (item["sha256"], item["size_bytes"]):
                raise ValueError("已交付文件与回执不一致。")
        return receipt

    def read_delivery(self, relative_path: str) -> tuple[bytes, dict]:
        _relative(relative_path, ("out",))
        for index, item in enumerate(self._receipt()):
            if item["path"] == relative_path:
                data = _read_bytes(self._delivery / str(index), _MAX_OUTPUT_BYTES)
                if (len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]):
                    raise ValueError("已交付文件与回执不一致。")
                return data, item
        raise FileNotFoundError("交付文件不存在。")
