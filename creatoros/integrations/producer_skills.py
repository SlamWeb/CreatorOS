"""Import GitHub originals and invoke editable local Skill directories."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path
from threading import Event, RLock, Thread
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field

from creatoros.skills.loader import SkillLoader, _NAME_PATTERN


_SKILL_EDIT_LOCK = RLock()


class SkillDigestConflict(ValueError):
    def __init__(self, current_digest: str):
        super().__init__("Skill 已被其他编辑更新，请重新读取后再保存。")
        self.current_digest = current_digest


def _validate_skill_frontmatter(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md 必须以 YAML frontmatter 起始。")
    try:
        closing = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as error:
        raise ValueError("SKILL.md 缺少 YAML frontmatter 结束标记。") from error
    try:
        metadata = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as error:
        raise ValueError("SKILL.md frontmatter 不是合法 YAML。") from error
    if not isinstance(metadata, dict):
        raise ValueError("SKILL.md frontmatter 必须是 YAML 键值对象。")
    name, description = metadata.get("name"), metadata.get("description")
    if (not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name) or len(name) > 64):
        raise ValueError("SKILL.md name 必须是小写字母、数字和单连字符组成的字符串名称。")
    if not isinstance(description, str) or not description.strip() or len(description) > 1_024:
        raise ValueError("SKILL.md description 必须是非空字符串且不超过 1024 字符。")
    return {"name": name, "description": description}


def skills_root_for(database) -> Path:
    name = database.engine.url.database
    if not name or name == ":memory:":
        raise ValueError("生产 Skill 注册需要持久化数据库或显式 skills_root。")
    path = Path(name).resolve()
    return path.parent / ("producer-skills" if path.name == "creatoros.db" else path.stem + "-producer-skills")


def github_url(value: str) -> str:
    value = value.strip().rstrip("/")
    p = urlsplit(value)
    parts = p.path.strip("/").split("/")
    if (p.scheme != "https" or p.netloc != "github.com" or p.query or p.fragment
            or len(parts) < 2 or any(not re.fullmatch(r"[\w.\-]+", s) or s in {".", ".."} for s in parts)
            or (len(parts) > 2 and (len(parts) < 4 or parts[2] != "tree"))):
        raise ValueError("请提供 https://github.com/owner/repo 或 /tree/ref/skill-path 链接。")
    return value


def _write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Skill 不允许符号链接或越界文件。")
        if path.is_file():
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def inherit_copy_permissions(directory: Path) -> None:
    """A published copy inherits its destination ACL, not tempfile's private ACL."""
    if os.name == "nt":
        subprocess.run(["icacls", str(directory), "/reset", "/T", "/Q"],
                       check=True, capture_output=True, timeout=30)


def freeze_skill(source: Path, target: Path, expected_digest: str) -> None:
    """Publish a complete copy, rejecting edits that race the copy operation."""
    source, target = Path(source), Path(target)
    if not source.is_dir() or source.is_symlink() or _digest(source) != expected_digest:
        raise ValueError("Skill 文件与本次快照不一致。")
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".skill-copy-", dir=target.parent) as temporary:
        staging = Path(temporary) / "copy"
        shutil.copytree(source, staging)
        if _digest(staging) != expected_digest or _digest(source) != expected_digest:
            raise ValueError("复制期间 Skill 已变化，请重新开始本次调用。")
        try:
            staging.rename(target)
        except OSError:
            if not target.exists():
                raise
            if not target.is_dir() or target.is_symlink() or _digest(target) != expected_digest:
                raise ValueError("已有 Skill 快照与本次输入不一致。")
        else:
            inherit_copy_permissions(target)


class InstallReceipt(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)

    skill_path: str = Field(description="source 仓库内含 SKILL.md 的相对文件夹；根目录为 .")
    carousel_compatible: bool = Field(description="原技能是否本身要求生成图片轮播，而非 HTML/PDF/纯文本")
    compatibility_note: str = Field(min_length=1)


def _inspect_checkout(source: Path, requested_path: str | None) -> InstallReceipt:
    if requested_path:
        candidates = [source / requested_path / "SKILL.md"]
    else:
        candidates = [path for path in source.rglob("SKILL.md") if ".git" not in path.parts]
    candidates = [path for path in candidates if path.is_file()]
    if len(candidates) != 1:
        detail = "未找到 SKILL.md" if not candidates else "仓库包含多个 Skill，请提供具体 tree 路径"
        raise ValueError(detail)
    skill_file = candidates[0].resolve()
    if not skill_file.is_relative_to(source.resolve()):
        raise ValueError("Skill 路径不在下载仓库内。")
    text = skill_file.read_text(encoding="utf-8")
    compatible = bool(re.search(
        r"(?mi)^creatoros-output\s*:\s*['\"]?social-content-pack\.image-carousel['\"]?\s*$", text
    ))
    path = skill_file.parent.relative_to(source).as_posix() or "."
    note = ("声明 CreatorOS 图片轮播产物契约。" if compatible else
            "未声明 creatoros-output: social-content-pack.image-carousel；可安装但不可绑定生产。")
    return InstallReceipt(skill_path=path, carousel_compatible=compatible, compatibility_note=note)


class GitSkillInstaller:
    """Download committed GitHub files and inspect a declared production contract."""

    def __init__(self, timeout_seconds: float = 120):
        self.timeout_seconds = timeout_seconds

    def _git(self, args: list[str], cwd: Path | None = None):
        try:
            subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=self.timeout_seconds)
        except FileNotFoundError as error:
            raise ValueError("未找到 git CLI。") from error
        except subprocess.TimeoutExpired as error:
            raise ValueError("下载 GitHub Skill 超时。") from error
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or "Git 命令失败").strip()[-1000:]
            raise ValueError(detail) from error

    def install(self, url: str, directory: Path, cancel: Event) -> InstallReceipt:
        parts = url.split("/")
        repo_url = "/".join(parts[:5])
        ref = parts[6] if len(parts) > 5 else None
        requested_path = "/".join(parts[7:]) if len(parts) > 7 else None
        source = directory / "source"
        if cancel.is_set():
            raise ValueError("安装已中断。")
        if ref:
            source.mkdir()
            self._git(["init", str(source)])
            self._git(["remote", "add", "origin", repo_url], cwd=source)
            self._git(["fetch", "--depth", "1", "origin", ref], cwd=source)
            self._git(["checkout", "--detach", "FETCH_HEAD"], cwd=source)
        else:
            self._git(["clone", "--depth", "1", repo_url, str(source)])
        if cancel.is_set():
            raise ValueError("安装已中断。")
        return _inspect_checkout(source, requested_path)


class ProducerSkillCatalog:
    """已安装 Skill 目录：角色（mind/production/legacy_end_to_end）与产物能力分离。

    旧注册记录没有 role 键，读取时映射为 legacy_end_to_end；不回写历史文件。
    """

    ROLES = frozenset({"mind", "production", "legacy_end_to_end"})
    PRODUCIBLE_ROLES = frozenset({"production", "legacy_end_to_end"})
    BUILTIN_ID = "knowledge-to-carousel"
    MAX_BROWSE_FILES = 500
    MAX_BROWSE_ENTRIES = 2_000
    MAX_TEXT_BYTES = 512 * 1024
    MAX_IMAGE_BYTES = 16 * 1024 * 1024
    MAX_IMAGE_PIXELS = 40_000_000
    _TEXT_SUFFIXES = frozenset({".txt", ".py", ".sh", ".bash", ".ps1", ".js", ".ts", ".jsx",
                                ".tsx", ".sql", ".css", ".html", ".json", ".yaml", ".yml",
                                ".toml", ".ini", ".csv", ".xml", ".md"})
    _IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
    _IMAGE_MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp",
                         "GIF": "image/gif"}

    def __init__(self, root: Path, project_root: Path | None = None):
        from creatoros.config import PROJECT_ROOT
        self.root = Path(root).resolve()
        self.project_root = project_root or PROJECT_ROOT

    @classmethod
    def _role(cls, data: dict):
        # 键缺失 = 0005 之前的旧记录；键存在但为 None = 安装时明确未分类。
        if "role" not in data:
            return "legacy_end_to_end"
        role = data["role"]
        if role is not None and role not in cls.ROLES:
            raise ValueError(f"Skill 角色无效：{role}")
        return role

    @classmethod
    def _producible(cls, data: dict) -> bool:
        # 当前执行/验收器只支持图片轮播；新制作 Skill 未接入适配前不可生产。
        return bool(data["carousel_compatible"]) and cls._role(data) in cls.PRODUCIBLE_ROLES

    def describe(self, reference: str) -> dict:
        """A catalog ID is an alias for the same editable local path."""
        data = self._record(reference)
        directory = self._working_directory(data)
        skill = next((s for s in SkillLoader([directory]).discover()
                      if s.path == directory / "SKILL.md"), None)
        if skill is None:
            raise ValueError("本地 Skill 缺少有效 name/description 的 SKILL.md。")
        receipt = _inspect_checkout(directory, ".")
        current = {**data, "name": skill.name, "description": skill.description,
                   "source_digest": data["digest"], "digest": _digest(directory),
                   "local_path": str(directory), "role": self._role(data),
                   "carousel_compatible": receipt.carousel_compatible,
                   "compatibility_note": receipt.compatibility_note}
        return {**current, "producible": self._producible(current)}

    def list(self):
        builtin = {"id": self.BUILTIN_ID, "name": self.BUILTIN_ID,
                   "description": "把知识点转成图片轮播", "carousel_compatible": True,
                   "compatibility_note": "内置生产技能", "commit": None, "github_url": None,
                   "role": "legacy_end_to_end", "producible": True,
                   "local_path": str(self.project_root / "creatoros" / "skills" / self.BUILTIN_ID)}
        items = [builtin]
        for path in sorted((self.root / "registry").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            try:
                items.append(self.describe(data["id"]))
            except (ValueError, OSError) as error:
                items.append({**data, "role": self._role(data), "producible": False,
                              "local_path": str(self.root / "working" / data["id"]),
                              "local_error": str(error)})
        return items

    def read_skill_page(self, skill_id: str, *, offset: int = 0, limit: int = 2000) -> dict:
        """Read one bounded page of a registered Skill's SKILL.md without repairing files."""
        if offset < 0 or not 1 <= limit <= 4000:
            raise ValueError("Skill 正文分页参数无效。")
        directory, item, skill = self._readable_skill_directory(skill_id)
        skill_file = directory / "SKILL.md"
        content = skill_file.read_text(encoding="utf-8")
        page = content[offset:offset + limit]
        has_more = offset + len(page) < len(content)
        return {**item, "name": skill.name, "description": skill.description,
                "content": page, "page": {"offset": offset, "limit": limit,
                                             "total_chars": len(content), "has_more": has_more,
                                             "next_offset": offset + len(page) if has_more else None}}

    @staticmethod
    def _is_link_or_reparse(path: Path) -> bool:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)

    @staticmethod
    def _sensitive_skill_path(relative: Path) -> bool:
        parts = relative.parts
        if any(part.startswith(".") or part.lower() in {"secrets", "credentials"} for part in parts):
            return True
        name = parts[-1].lower()
        return (name in {"id_rsa", "id_ed25519", "credentials", "secrets.json"}
                or name.startswith(".env")
                or Path(name).suffix in {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db"})

    def _readable_skill_directory(self, skill_id: str):
        """Resolve only an explicitly registered working copy or the fixed built-in Skill."""
        if skill_id == self.BUILTIN_ID:
            base = self.project_root.resolve()
            managed_root = self.project_root / "creatoros" / "skills"
            directory = managed_root / skill_id
            item = {"id": skill_id, "role": "legacy_end_to_end", "editable": False}
            if self._is_link_or_reparse(self.project_root / "creatoros"):
                raise ValueError("Skill 受管目录路径无效。")
        else:
            if not isinstance(skill_id, str) or not re.fullmatch(r"[a-z0-9-]+--[a-f0-9]{16}", skill_id):
                raise ValueError("请提供目录返回的 Skill ID。")
            base = self.root
            registry_root = self.root / "registry"
            if (self._is_link_or_reparse(registry_root) or not registry_root.is_dir()
                    or not registry_root.resolve().is_relative_to(self.root)):
                raise ValueError("Skill 注册目录路径无效。")
            record_path = registry_root / f"{skill_id}.json"
            if (self._is_link_or_reparse(record_path) or not record_path.is_file()
                    or record_path.stat().st_size > 128 * 1024):
                raise ValueError("生产 Skill 未登记。")
            try:
                data = json.loads(record_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeError) as error:
                raise ValueError("Skill 注册记录无效。") from error
            if (not isinstance(data, dict) or data.get("id") != skill_id
                    or not isinstance(data.get("digest"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", data["digest"])):
                raise ValueError("Skill 注册记录与 ID 不匹配。")
            directory = self.root / "working" / skill_id
            managed_root = self.root / "working"
            item = {"id": skill_id, "role": self._role(data), "editable": True}

        if (self._is_link_or_reparse(managed_root) or not managed_root.is_dir()
                or not managed_root.resolve().is_relative_to(base)):
            raise ValueError("Skill 受管目录不存在或路径无效。")
        if (self._is_link_or_reparse(directory) or not directory.is_dir()
                or not directory.resolve().is_relative_to(managed_root.resolve())):
            raise ValueError("Skill 工作目录不存在或路径无效。")
        skill_file = directory / "SKILL.md"
        if (self._is_link_or_reparse(skill_file) or not skill_file.is_file()
                or skill_file.resolve().parent != directory.resolve()):
            raise ValueError("Skill 正文文件不存在或路径无效。")
        from creatoros.skills.loader import SkillLoader
        skill = SkillLoader([directory])._read_metadata(skill_file)
        if skill is None:
            raise ValueError("本地 Skill 缺少有效 name/description 的 SKILL.md。")
        return directory, item, skill

    def list_skill_files(self, skill_id: str) -> dict:
        with _SKILL_EDIT_LOCK:
            return self._list_skill_files_locked(skill_id)

    def _list_skill_files_locked(self, skill_id: str) -> dict:
        directory, item, skill = self._readable_skill_directory(skill_id)
        files = []
        entries = 0
        for current, dirs, names in os.walk(directory, topdown=True, followlinks=False):
            parent = Path(current)
            visible_dirs = []
            for name in dirs:
                relative = (parent / name).relative_to(directory)
                if self._sensitive_skill_path(relative):
                    continue
                entries += 1
                if entries > self.MAX_BROWSE_ENTRIES:
                    raise ValueError("Skill 文件过多，无法安全展示。")
                child = parent / name
                if self._is_link_or_reparse(child) or not child.resolve().is_relative_to(directory.resolve()):
                    raise ValueError("Skill 包含不支持的链接或越界目录。")
                visible_dirs.append(name)
            dirs[:] = visible_dirs
            for name in names:
                path = parent / name
                relative = path.relative_to(directory)
                if self._sensitive_skill_path(relative):
                    continue
                entries += 1
                if entries > self.MAX_BROWSE_ENTRIES:
                    raise ValueError("Skill 文件过多，无法安全展示。")
                if self._is_link_or_reparse(path) or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
                    raise ValueError("Skill 包含不支持的链接或越界文件。")
                suffix = path.suffix.lower()
                kind = "markdown" if suffix == ".md" else (
                    "text" if suffix in self._TEXT_SUFFIXES else
                    "image" if suffix in self._IMAGE_SUFFIXES else "unsupported")
                files.append({"path": relative.as_posix(), "kind": kind, "size": path.stat().st_size})
                if len(files) > self.MAX_BROWSE_FILES:
                    raise ValueError("Skill 文件过多，无法安全展示。")
        files.sort(key=lambda entry: entry["path"].casefold())
        return {**item, "name": skill.name, "description": skill.description,
                "digest": _digest(directory), "files": files}

    def read_skill_file(self, skill_id: str, relative_path: str, *, text_only: bool = False) -> dict:
        with _SKILL_EDIT_LOCK:
            return self._read_skill_file_locked(skill_id, relative_path, text_only=text_only)

    def _read_skill_file_locked(self, skill_id: str, relative_path: str, *, text_only: bool = False) -> dict:
        if (not isinstance(relative_path, str) or not relative_path or len(relative_path) > 512
                or "\\" in relative_path or ":" in relative_path or "\x00" in relative_path):
            raise ValueError("Skill 文件路径无效。")
        if any(part in {"", ".", ".."} for part in relative_path.split("/")):
            raise ValueError("Skill 文件路径无效。")
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ValueError("Skill 文件路径无效。")
        if self._sensitive_skill_path(relative):
            raise ValueError("Skill 文件不可读取。")
        directory, item, _skill = self._readable_skill_directory(skill_id)
        path = directory.joinpath(*relative.parts)
        resolved_root = directory.resolve()
        for parent in [directory, *(directory / Path(*relative.parts[:index])
                                    for index in range(1, len(relative.parts) + 1))]:
            if self._is_link_or_reparse(parent) or not parent.resolve().is_relative_to(resolved_root):
                raise ValueError("Skill 文件路径越界或无效。")
        if not path.is_file():
            raise ValueError("Skill 文件不存在。")
        suffix = path.suffix.lower()
        if text_only and suffix not in self._TEXT_SUFFIXES:
            raise TypeError("当前工具只能读取 Markdown/UTF-8 文本，不能读取图片或其他二进制文件；"
                            "文件路径或列表不代表已实际看图。")
        if suffix in self._IMAGE_SUFFIXES:
            size = path.stat().st_size
            if size > self.MAX_IMAGE_BYTES:
                raise OverflowError("图片超过 16 MiB，无法预览。")
            with path.open("rb") as stream:
                content = stream.read(self.MAX_IMAGE_BYTES + 1)
            if len(content) > self.MAX_IMAGE_BYTES:
                raise OverflowError("图片超过 16 MiB，无法预览。")
            from PIL import Image, UnidentifiedImageError
            try:
                with Image.open(io.BytesIO(content)) as image:
                    if (image.format not in self._IMAGE_MIME_TYPES
                            or image.width * image.height > self.MAX_IMAGE_PIXELS):
                        raise ValueError("图片格式或尺寸无效。")
                    media_type = self._IMAGE_MIME_TYPES[image.format]
                    frames = getattr(image, "n_frames", 1)
                    if frames > 100:
                        raise ValueError("图片动画帧数过多，无法预览。")
                    for index in range(frames):
                        image.seek(index)
                        if image.width * image.height > self.MAX_IMAGE_PIXELS:
                            raise ValueError("图片尺寸过大，无法预览。")
                        image.load()
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
                raise ValueError("图片内容无效，无法预览。") from error
            return {"path": relative.as_posix(), "kind": "image", "content": content,
                    "media_type": media_type, "digest": _digest(directory),
                    "editable": item["editable"]}
        if suffix in self._TEXT_SUFFIXES:
            size = path.stat().st_size
            if size > self.MAX_TEXT_BYTES:
                raise OverflowError("文本文件超过 512 KiB，无法预览。")
            try:
                with path.open("rb") as stream:
                    raw_content = stream.read(self.MAX_TEXT_BYTES + 1)
                if len(raw_content) > self.MAX_TEXT_BYTES:
                    raise OverflowError("文本文件超过 512 KiB，无法预览。")
                content = raw_content.decode("utf-8")
            except UnicodeError as error:
                raise ValueError("文本文件不是有效 UTF-8。") from error
            return {"path": relative.as_posix(), "digest": _digest(directory),
                    "editable": item["editable"],
                    "kind": "markdown" if suffix == ".md" else "text", "content": content}
        raise TypeError("此文件类型不支持预览。")

    def update_skill_file(self, skill_id: str, relative_path: str, content: str,
                          expected_digest: str) -> dict:
        with _SKILL_EDIT_LOCK:
            return self._update_skill_file_locked(skill_id, relative_path, content, expected_digest)

    def _update_skill_file_locked(self, skill_id: str, relative_path: str, content: str,
                                  expected_digest: str) -> dict:
        """Atomically update one listed UTF-8 text file in an installed working copy."""
        if skill_id == self.BUILTIN_ID:
            raise ValueError("内置 Skill 只读，不能通过已安装 Skill 编辑入口修改。")
        if not isinstance(content, str) or len(content.encode("utf-8")) > self.MAX_TEXT_BYTES:
            raise OverflowError("Skill 文本文件最多 512 KiB。")
        if not isinstance(expected_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_digest):
            raise ValueError("expected_digest 必须是文件列表返回的 Skill digest。")
        # Reuse the reader's strict relative-path, symlink, UTF-8, size, and
        # extension checks. Images and unsupported files are never writable.
        readable = self.read_skill_file(skill_id, relative_path)
        if readable["kind"] not in {"markdown", "text"}:
            raise TypeError("只支持编辑已列出的 Markdown 或文本文件。")
        directory, _item, _skill = self._readable_skill_directory(skill_id)
        target = directory.joinpath(*Path(relative_path).parts)
        if self._is_link_or_reparse(target) or not target.is_file():
            raise ValueError("Skill 文件路径无效。")

        current_digest = _digest(directory)
        if current_digest != expected_digest:
            raise SkillDigestConflict(current_digest)
        temporary = None
        try:
            # Validate SKILL.md metadata before publishing the replacement.
            if Path(relative_path).as_posix().casefold() == "skill.md":
                frontmatter = _validate_skill_frontmatter(content)
                from tempfile import NamedTemporaryFile
                with NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                        prefix=".skill-edit-", suffix=".md",
                                        dir=directory, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                parsed = SkillLoader([directory])._read_metadata(temporary)
                if (parsed is None or parsed.name != frontmatter["name"]
                        or parsed.description != frontmatter["description"]):
                    raise ValueError("当前只支持与单行 frontmatter 解析一致的 name/description；多行块标量不能保存。")
            else:
                from tempfile import NamedTemporaryFile
                with NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                        prefix=".skill-edit-", suffix=target.suffix,
                                        dir=target.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
            # Confirm the target is still the file inspected above, then atomically replace.
            if self._is_link_or_reparse(target) or target.resolve().parent != target.parent.resolve():
                raise ValueError("Skill 文件路径越界或无效。")
            temporary.replace(target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        updated = self.read_skill_file(skill_id, relative_path)
        described = self.describe(skill_id)
        return {"skill_id": skill_id, "path": updated["path"], "content": updated["content"],
                "digest": described["digest"], "name": described["name"],
                "description": described["description"], "editable": True}

    def _record(self, skill_id: str) -> dict:
        if not re.fullmatch(r"[a-z0-9-]+--[a-f0-9]{16}", skill_id):
            directory = Path(skill_id)
            if (not directory.is_absolute() or directory.is_symlink()
                    or directory.resolve().parent != self.root / "working"):
                raise ValueError("请提供已安装 Skill 的目录条目或 working 本地路径。")
            skill_id = directory.name
            if not re.fullmatch(r"[a-z0-9-]+--[a-f0-9]{16}", skill_id):
                raise ValueError("未知本地 Skill 路径。")
        record = self.root / "registry" / f"{skill_id}.json"
        if not record.is_file():
            raise ValueError("生产 Skill 未安装。")
        return json.loads(record.read_text(encoding="utf-8"))

    def original(self, reference: str, expected_digest: str) -> Path:
        data = self._record(reference)
        directory = self.root / "versions" / data["id"]
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.root):
            raise ValueError("Skill 目录不在受管理的安装范围内。")
        if not directory.is_dir() or _digest(directory) != expected_digest:
            raise ValueError("原 Run 的 Skill 快照缺失，导入原件也不匹配。")
        return directory

    def _working_directory(self, data: dict) -> Path:
        directory = self.root / "working" / data["id"]
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.root / "working"):
            raise ValueError("本地 Skill 路径越界。")
        if not directory.exists():
            freeze_skill(self.original(data["id"], data["digest"]), directory, data["digest"])
        if not directory.is_dir():
            raise ValueError("本地 Skill 目录不存在。")
        _digest(directory)  # retain file/path checks; content is intentionally editable
        return directory

    def locate(self, skill_id: str) -> Path:
        """Read the editable local directory; no import-digest content lock."""
        if skill_id == self.BUILTIN_ID:
            return self.project_root / "creatoros" / "skills" / skill_id
        return Path(self.describe(skill_id)["local_path"])

    def resolve(self, skill_id: str) -> Path:
        """生产门禁：只有声明为可生产角色且满足当前轮播产物契约的 Skill 可绑定/生产。"""
        if skill_id == self.BUILTIN_ID:
            return self.project_root / "creatoros" / "skills" / skill_id
        data = self.describe(skill_id)
        role = self._role(data)
        if role is None:
            raise ValueError("该 Skill 尚未声明内容/制作角色，先完成配置再绑定生产。")
        if role == "mind":
            raise ValueError("该 Skill 是内容 Skill（mind），不能单独承担图片轮播生产。")
        if not data["carousel_compatible"]:
            raise ValueError("该 Skill 不是图片轮播生产技能，请先改造产物契约再绑定。")
        return Path(data["local_path"])

    def register(self, workspace: Path, url: str, receipt: InstallReceipt, role: str | None = None) -> dict:
        if role is not None and role not in self.ROLES:
            raise ValueError(f"Skill 角色必须是 {sorted(self.ROLES)} 之一，或省略表示暂不分类。")
        source = workspace / "source"
        def git(*args, binary=False):
            result = subprocess.run(["git", "-C", str(source), *args], check=True,
                                    capture_output=True, timeout=30)
            return result.stdout if binary else result.stdout.decode("utf-8").strip()
        expected = "/".join(url.split("/")[:5]).removesuffix(".git")
        if git("remote", "get-url", "origin").rstrip("/").removesuffix(".git").lower() != expected.lower():
            raise ValueError("下载的仓库与请求不一致。")
        commit = git("rev-parse", "HEAD")
        path = receipt.skill_path.replace("\\", "/").strip("/")
        # Some real receipts include the explicitly named checkout directory.
        if path.startswith("source/") and not (source / path / "SKILL.md").is_file():
            path = path.removeprefix("source/")
        if path != "." and (not path or any(p in {"", ".", ".."} for p in path.split("/")) or ":" in path):
            raise ValueError("Skill 路径必须位于下载仓库内。")
        parts = url.split("/")
        if len(parts) > 5:
            ref = parts[6]
            try:
                requested_commit = git("rev-parse", "--verify", f"{ref}^{{commit}}")
            except subprocess.CalledProcessError:
                requested_commit = git("rev-parse", "--verify", f"origin/{ref}^{{commit}}")
            if requested_commit != commit or (len(parts) > 7 and path != "/".join(parts[7:])):
                raise ValueError("安装回执与指定 Git ref/Skill 路径不一致。")
        # Use committed bytes, never modified working-tree content or model-provided source code.
        archive = git("archive", "--format=tar", "HEAD" if path == "." else f"HEAD:{path}", binary=True)
        if len(archive) > 32 * 1024 * 1024:
            raise ValueError("Skill 超过 32 MiB，请提供具体 Skill 子目录。")
        staging = workspace / "verified"
        staging.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            members = tar.getmembers()
            if len(members) > 2000:
                raise ValueError("Skill 文件过多，请提供具体 Skill 子目录。")
            for member in members:
                target = staging / member.name
                if not target.resolve().is_relative_to(staging.resolve()) or not (member.isfile() or member.isdir()):
                    raise ValueError("Skill 包含不支持的链接或路径。")
                if member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as stream:
                        target.write_bytes(stream.read())
        loader = SkillLoader([staging])
        skills = loader.discover()
        skill = next((s for s in skills if s.path == staging / "SKILL.md"), None)
        if skill is None:
            raise ValueError("选定目录缺少有效 name/description 的 SKILL.md。")
        digest = _digest(staging)
        suffix = hashlib.sha256(f"{expected}:{commit}:{path}:{digest}".encode()).hexdigest()[:16]
        skill_id = f"{skill.name}--{suffix}"
        if len(skill_id) > 120:
            raise ValueError("Skill 名称太长。")
        record = {"id": skill_id, "name": skill.name, "description": skill.description,
                  "github_url": url, "commit": commit, "skill_path": path, "digest": digest,
                  "carousel_compatible": receipt.carousel_compatible,
                  "compatibility_note": receipt.compatibility_note,
                  "role": role}
        destination = self.root / "versions" / skill_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if _digest(destination) != digest:
                raise ValueError("同版本目录已被修改；不会覆盖。")
        else:
            staging.rename(destination)
        record_path = self.root / "registry" / f"{skill_id}.json"
        if not record_path.exists():
            _write(record_path, record)
        return self.describe(skill_id)

    def register_local(self, directory: Path, *, role: str, source_note: str | None = None) -> dict:
        """Register a local Skill folder without downloading it or invoking a model."""
        if role not in self.ROLES:
            raise ValueError(f"Skill 角色必须是 {sorted(self.ROLES)} 之一。")

        source = Path(directory)
        if source.is_symlink() or not source.is_dir():
            raise ValueError("本地 Skill 必须是普通目录，不能是符号链接。")
        source = source.resolve(strict=True)

        # Match the existing Git import bounds and reject all linked/escaping entries.
        entries, total_bytes = 0, 0
        for path in source.rglob("*"):
            entries += 1
            if entries > 2_000:
                raise ValueError("Skill 文件过多，请拆分目录后再导入。")
            if path.is_symlink():
                raise ValueError("Skill 不允许符号链接或越界文件。")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(source):
                raise ValueError("Skill 路径不在本地源目录内。")
            if path.is_file():
                total_bytes += path.stat().st_size
            elif not path.is_dir():
                raise ValueError("Skill 只允许普通文件和目录。")
            if total_bytes > 32 * 1024 * 1024:
                raise ValueError("Skill 超过 32 MiB，请拆分目录后再导入。")

        initial_digest = _digest(source)
        loader = SkillLoader([source])
        skill = next((item for item in loader.discover() if item.path == source / "SKILL.md"), None)
        if skill is None:
            raise ValueError("本地 Skill 缺少有效 name/description 的 SKILL.md。")
        receipt = _inspect_checkout(source, ".")
        if _digest(source) != initial_digest:
            raise ValueError("导入期间本地 Skill 已变化，请重新开始。")

        identity = os.path.normcase(str(source))
        suffix = hashlib.sha256(f"local:{identity}:{initial_digest}".encode("utf-8")).hexdigest()[:16]
        skill_id = f"{skill.name}--{suffix}"
        if len(skill_id) > 120:
            raise ValueError("Skill 名称太长。")

        # Keep one immutable source copy per identity/content pair. freeze_skill
        # verifies the source before and after copying and never replaces an existing target.
        versions = self.root / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        if versions.is_symlink() or not versions.resolve().is_relative_to(self.root):
            raise ValueError("版本目录路径越界。")
        original = versions / skill_id
        if original.is_symlink():
            raise ValueError("原始 Skill 目录不能是符号链接。")
        if original.exists():
            if not original.is_dir() or _digest(original) != initial_digest:
                raise ValueError("同版本目录已被修改；不会覆盖。")
        else:
            freeze_skill(source, original, initial_digest)

        record = {"id": skill_id, "name": skill.name, "description": skill.description,
                  "github_url": None, "commit": None, "skill_path": ".",
                  "digest": initial_digest, "carousel_compatible": receipt.carousel_compatible,
                  "compatibility_note": receipt.compatibility_note, "role": role,
                  "source_kind": "local"}
        if source_note is not None:
            record["source_note"] = source_note

        registry = self.root / "registry"
        registry.mkdir(parents=True, exist_ok=True)
        if registry.is_symlink() or not registry.resolve().is_relative_to(self.root):
            raise ValueError("注册表目录路径越界。")
        record_path = registry / f"{skill_id}.json"
        if record_path.is_symlink():
            raise ValueError("Skill 注册记录不能是符号链接。")
        if record_path.exists():
            existing = json.loads(record_path.read_text(encoding="utf-8"))
            if (existing.get("id") != skill_id or existing.get("source_kind") != "local"
                    or existing.get("digest") != initial_digest or existing.get("github_url") is not None
                    or existing.get("commit") is not None):
                raise ValueError("本地 Skill ID 与已有注册记录冲突。")
        else:
            _write(record_path, record)
        return self.describe(skill_id)


class SkillInstallService:
    """One bounded local job, persisted receipt; no autonomous retry or production."""
    def __init__(self, catalog: ProducerSkillCatalog, installer=None):
        self.catalog = catalog
        self.installer = installer or GitSkillInstaller()
        self.lock, self.cancel = RLock(), Event()
        self.thread = None

    def _path(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{64}", job_id):
            raise ValueError("无效安装任务 ID。")
        return self.catalog.root / "jobs" / f"{job_id}.json"

    def get(self, job_id):
        path = self._path(job_id)
        if not path.exists():
            raise ValueError("安装任务不存在。")
        return json.loads(path.read_text(encoding="utf-8"))

    def start(self):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("旧安装任务尚未停止。")
        self.cancel.clear()
        for path in (self.catalog.root / "jobs").glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["status"] == "installing":
                _write(path, {**data, "status": "interrupted", "message": "宿主已重启；未自动重试安装。"})

    def submit(self, url, *, retry=False, role=None):
        url = github_url(url)
        if role is not None and role not in ProducerSkillCatalog.ROLES:
            raise ValueError(f"Skill 角色必须是 {sorted(ProducerSkillCatalog.ROLES)} 之一，或省略表示暂不分类。")
        job_id = hashlib.sha256(url.encode()).hexdigest()
        with self.lock:
            previous = self.get(job_id) if self._path(job_id).exists() else None
            if previous and not (retry and previous["status"] in {"failed", "interrupted"}):
                return previous
            if self.cancel.is_set():
                raise ValueError("安装服务正在关闭。")
            if self.thread and self.thread.is_alive():
                raise ValueError("另一个 Skill 正在安装，请完成后再提交。")
            job = {"id": job_id, "github_url": url, "status": "installing", "skill": None,
                   "attempt": (previous or {}).get("attempt", 0) + 1,
                   "role": role if previous is None else previous.get("role"),
                   "message": "CreatorOS 正在下载和核验，尚未绑定栏目。"}
            _write(self._path(job_id), job)
            self.thread = Thread(target=self._run, args=(job,), daemon=True)
            try:
                self.thread.start()
            except Exception:
                _write(self._path(job_id), {**job, "status": "interrupted", "message": "安装线程启动失败，未调用 Codex。"})
                raise
            return job

    def _run(self, job):
        workspace = self.catalog.root / "work" / job["id"] / f"attempt-{job['attempt']}"
        try:
            workspace.mkdir(parents=True, exist_ok=False)
            receipt = self.installer.install(job["github_url"], workspace, self.cancel)
            if self.cancel.is_set():
                raise ValueError("安装已中断。")
            record = self.catalog.register(workspace, job["github_url"], receipt, role=job.get("role"))
            job = {**job, "status": "installed", "skill": record, "message": "已安装，尚未绑定栏目。"}
        except Exception as error:
            # Keep detailed errors local, not in model context/browser (may contain paths).
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "error.txt").write_text(str(error), encoding="utf-8")
            job = {**job, "status": "interrupted" if self.cancel.is_set() else "failed",
                   "message": "安装未完成；请核对仓库链接、Git ref、Skill 路径或网络连接。未修改栏目。"}
        _write(self._path(job["id"]), job)

    def shutdown(self):
        self.cancel.set()
        if self.thread:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError("Skill 安装执行器尚未停止，不能安全释放宿主。")
