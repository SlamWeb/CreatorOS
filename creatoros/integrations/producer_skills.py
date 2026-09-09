"""Codex fetches a Skill; the host verifies and stores an immutable production version."""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path
from threading import Event, RLock, Thread
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from creatoros.skills.loader import SkillLoader


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
    def __init__(self, root: Path, project_root: Path | None = None):
        from creatoros.config import PROJECT_ROOT
        self.root = Path(root).resolve()
        self.project_root = project_root or PROJECT_ROOT

    def list(self):
        builtin = {"id": "knowledge-to-carousel", "name": "knowledge-to-carousel",
                   "description": "把知识点转成图片轮播", "carousel_compatible": True,
                   "compatibility_note": "内置生产技能", "commit": None, "github_url": None}
        return [builtin] + [json.loads(p.read_text(encoding="utf-8"))
                            for p in sorted((self.root / "registry").glob("*.json"))]

    def resolve(self, skill_id: str) -> Path:
        if skill_id == "knowledge-to-carousel":
            return self.project_root / "creatoros" / "skills" / skill_id
        if not re.fullmatch(r"[a-z0-9-]+--[a-f0-9]{16}", skill_id):
            raise ValueError("未知生产 Skill ID。")
        record = self.root / "registry" / f"{skill_id}.json"
        if not record.is_file():
            raise ValueError("生产 Skill 未安装。")
        data = json.loads(record.read_text(encoding="utf-8"))
        if not data["carousel_compatible"]:
            raise ValueError("该 Skill 不是图片轮播生产技能，请先改造产物契约再绑定。")
        directory = self.root / "versions" / skill_id
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.root):
            raise ValueError("Skill 目录不在受管理的安装范围内。")
        if _digest(directory) != data["digest"]:
            raise ValueError("已安装 Skill 文件发生变化，拒绝静默使用被修改的版本。")
        return directory

    def register(self, workspace: Path, url: str, receipt: InstallReceipt) -> dict:
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
                  "compatibility_note": receipt.compatibility_note}
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
        return json.loads(record_path.read_text(encoding="utf-8"))


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

    def submit(self, url, *, retry=False):
        url = github_url(url)
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
            record = self.catalog.register(workspace, job["github_url"], receipt)
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
