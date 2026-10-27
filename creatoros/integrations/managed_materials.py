"""Immutable, account-scoped source media. Model arguments never contain paths."""
import base64
import hashlib
import ipaddress
from pathlib import Path
import os
import shutil
import socket
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from sqlalchemy import select, text
from creatoros.storage import Creator
from creatoros.storage.models import ManagedMaterial
from .atomic_file import atomic_write_bytes
from .media_tools import probe_media, decode_media

MAX_BYTES = 64 * 1024 * 1024
EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".mp4", ".mov", ".webm"}
SHARE_DOMAINS = {"douyin.com", "iesdouyin.com", "bilibili.com", "b23.tv", "xiaohongshu.com",
                 "xhslink.com", "youtube.com", "youtu.be"}


class MaterialStore:
    def __init__(self, database, *, root=None):
        self.database = database
        path = database.engine.url.database
        self.root = (Path(root).resolve() if root else
                     Path(path).resolve().with_suffix(".materials") if path and path != ":memory:" else None)

    def _creator(self, session, creator_id):
        creator = session.get(Creator, creator_id)
        if not creator or not creator.is_active:
            raise ValueError("账号不存在或未启用。")

    def _path(self, row):
        if self.root is None:
            raise ValueError("此数据库未配置持久素材目录。")
        path = self.root / row.id / ("source" + row.metadata_json["suffix"])
        if path.resolve() != path or self.root.is_symlink():
            raise ValueError("素材路径无效。")
        return path

    @staticmethod
    def _view(row):
        return dict(id=row.id, name=row.name, kind=row.kind, size_bytes=row.size_bytes, sha256=row.sha256,
                    duration_seconds=row.metadata_json["duration_seconds"], source_url=row.metadata_json.get("source_url"),
                    url=f"/api/creators/{row.creator_id}/materials/{row.id}/file")

    def upload(self, creator_id, name, data_base64, *, source_url=None):
        if len(data_base64) > (MAX_BYTES * 4 // 3 + 8):
            raise ValueError("素材不能超过64 MiB。")
        try:
            raw = base64.b64decode(data_base64, validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError("素材编码无效。") from error
        return self.put(creator_id, name, raw, source_url=source_url)

    def put(self, creator_id, name, raw, *, source_url=None):
        name = Path(name.replace("\\", "/")).name[:200]
        suffix = Path(name).suffix.lower()
        if suffix not in EXTENSIONS or not raw or len(raw) > MAX_BYTES:
            raise ValueError("请上传不超过64 MiB的音频或视频文件。")
        checksum = hashlib.sha256(raw).hexdigest()
        identifier = hashlib.sha256((creator_id + ":" + checksum).encode()).hexdigest()
        with self.database.session() as session:
            self._creator(session, creator_id)
            existing = session.get(ManagedMaterial, identifier)
            if existing:
                self.resolve(creator_id, identifier)
                return self._view(existing)
        with TemporaryDirectory(prefix="creatoros-material-") as temporary:
            source = Path(temporary) / ("input" + suffix)
            source.write_bytes(raw)
            metadata = probe_media(source)
            if not metadata["has_audio"]:
                raise ValueError("BGM素材中没有音轨。")
            decode_media(source)
        metadata.update(suffix=suffix, source_url=source_url)
        with self.database.session() as session:
            if self.database.engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            self._creator(session, creator_id)
            existing = session.get(ManagedMaterial, identifier)
            if existing:
                return self._view(existing)
            row = ManagedMaterial(id=identifier, creator_id=creator_id, name=name,
                kind="video" if metadata["has_video"] else "audio", sha256=checksum,
                size_bytes=len(raw), metadata_json=metadata)
            path = self._path(row)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(path, raw)
            session.add(row)
            session.flush()
            return self._view(row)

    def list(self, creator_id):
        with self.database.session() as session:
            self._creator(session, creator_id)
            return [self._view(row) for row in session.scalars(select(ManagedMaterial).where(
                ManagedMaterial.creator_id == creator_id).order_by(ManagedMaterial.created_at.desc()))]

    def resolve(self, creator_id, identifier):
        with self.database.session() as session:
            self._creator(session, creator_id)
            row = session.get(ManagedMaterial, identifier)
            if not row or row.creator_id != creator_id:
                raise ValueError("素材不存在或不属于当前账号。")
            path = self._path(row)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row.sha256:
                raise ValueError("素材缺失或已变化。")
            return {**self._view(row), "path": path}

    def from_url(self, creator_id, source_url):
        parsed = urlsplit(source_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("请提供公开音频或视频分享链接。")
        hostname = parsed.hostname.lower().rstrip(".")
        if not any(hostname == domain or hostname.endswith("." + domain) for domain in SHARE_DOMAINS):
            raise ValueError("分享链接仅支持抖音、哔哩哔哩、小红书和 YouTube；其他来源请上传音频文件。")
        try:
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        except OSError as error:
            raise ValueError("无法解析分享链接地址，请检查链接或上传音频文件。") from error
        for address in addresses:
            if not ipaddress.ip_address(address[4][0]).is_global:
                raise ValueError("素材链接不能指向本机或内网。")
        executable = os.getenv("CREATOROS_MEDIA_DOWNLOADER") or shutil.which("yt-dlp")
        if not executable:
            raise ValueError("未配置分享链接解析器，请上传音频文件，或配置 CREATOROS_MEDIA_DOWNLOADER。")
        with TemporaryDirectory(prefix="creatoros-bgm-") as temporary:
            try:
                result = subprocess.run([executable, "--no-playlist", "--no-cache-dir", "--max-filesize", str(MAX_BYTES),
                    "--socket-timeout", "20", "--retries", "1", "-f", "bestaudio/best",
                    "-o", str(Path(temporary) / "source.%(ext)s"), "--", source_url], capture_output=True, timeout=120)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ValueError("分享链接解析未完成，请上传音频文件。") from error
            files = [path for path in Path(temporary).iterdir() if path.suffix.lower() in EXTENSIONS]
            if result.returncode or len(files) != 1:
                raise ValueError("未能从分享链接取得音乐，请上传MP3或包含音乐的视频文件。")
            if files[0].stat().st_size > MAX_BYTES:
                raise ValueError("素材不能超过64 MiB。")
            return self.put(creator_id, files[0].name, files[0].read_bytes(), source_url=source_url)
