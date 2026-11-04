"""Immutable, account-scoped source media. Model arguments never contain paths."""
import base64
import hashlib
import ipaddress
import json
import math
import re
from pathlib import Path
import os
import shutil
import socket
import subprocess
import sys
import threading
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from sqlalchemy import select, text
from creatoros.storage import Creator
from creatoros.storage.models import ManagedMaterial
from .atomic_file import atomic_write_bytes, atomic_write_text
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
        self._analysis_lock = threading.Lock()
        self._analysis_jobs = {}
        self._import_jobs = {}

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
            rows = list(session.scalars(select(ManagedMaterial).where(
                ManagedMaterial.creator_id == creator_id).order_by(ManagedMaterial.created_at.desc())))
            values = []
            for row in rows:
                path = self._path(row)
                available = (path.parent / "bgm-analysis.json").is_file()
                values.append({**self._view(row), "analysis_available": available,
                               "analysis_status": self._analysis_status_for_path(creator_id, row.id, path, available)["status"]})
        return values

    def resolve(self, creator_id, identifier):
        with self.database.session() as session:
            self._creator(session, creator_id)
            row = session.get(ManagedMaterial, identifier)
            if not row or row.creator_id != creator_id:
                raise ValueError("素材不存在或不属于当前账号。")
            path = self._path(row)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row.sha256:
                raise ValueError("素材缺失或已变化。")
            return {**self._view(row), "analysis_available": (path.parent / "bgm-analysis.json").is_file(), "path": path}

    def save_analysis(self, creator_id, identifier, analysis, receipt, onsets):
        """Save an existing bgm-understanding result beside its account's audio."""
        from .bgm_analysis import validate_analysis

        audio = self.resolve(creator_id, identifier)
        try:
            validate_analysis(audio["path"], receipt, onsets, audio_duration=audio["duration_seconds"])
        except (KeyError, TypeError) as error:
            raise ValueError("BGM 分析缺少音频来源或起音数据。") from error
        # Keep only the source identity and timebase; analysis files may contain
        # paths from the computer where they were originally generated.
        clean_receipt = {key: receipt[key] for key in ("inputSha256", "sourceStartSeconds", "sourceEndSeconds")}
        analysis = json.loads(json.dumps(analysis, ensure_ascii=False))
        if isinstance(analysis.get("audio"), dict):
            analysis["audio"].pop("source", None)
        for value in analysis.get("sources", {}).values():
            if isinstance(value, dict):
                value.pop("path", None)
        bundle = {"analysis": analysis, "receipt": clean_receipt, "onsets": onsets,
                  "audio_duration": audio["duration_seconds"]}
        destination = audio["path"].parent / "bgm-analysis.json"
        atomic_write_text(destination, json.dumps(bundle, ensure_ascii=False, indent=2))
        return {"material_id": identifier, "analysis_available": True}

    def resolve_analysis(self, creator_id, identifier):
        """Return a registered analysis path, or None for unanalysed audio."""
        audio = self.resolve(creator_id, identifier)
        path = audio["path"].parent / "bgm-analysis.json"
        if not path.is_file():
            return None
        return {"path": path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def analysis_status(self, creator_id, identifier):
        audio = self.resolve(creator_id, identifier)
        return self._analysis_status_for_path(creator_id, identifier, audio["path"], audio["analysis_available"])

    def _analysis_status_for_path(self, creator_id, identifier, audio_path, analysis_available):
        with self._analysis_lock:
            job = self._analysis_jobs.get((creator_id, identifier))
            if job:
                return dict(job)
        saved = audio_path.parent / "bgm-analysis-status.json"
        if saved.is_file():
            status = json.loads(saved.read_text(encoding="utf-8"))
            if status.get("status") == "running":
                return {"status": "interrupted", "message": "上次分析中断，可手动重新开始。"}
            return status
        return {"status": "completed" if analysis_available else "not_started"}

    def start_analysis(self, creator_id, identifier, *, start=0, duration=None, force=False):
        audio = self.resolve(creator_id, identifier)
        if not isinstance(start, (int, float)) or isinstance(start, bool) or not math.isfinite(start) or start < 0:
            raise ValueError("分析起点必须是非负秒数。")
        if duration is not None and (not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or not 0 < duration <= 300):
            raise ValueError("分析时长必须在 0 到 300 秒之间。")
        key = (creator_id, identifier)
        with self._analysis_lock:
            if self._analysis_jobs.get(key, {}).get("status") == "running":
                return {"status": "running"}
            analysis_path = audio["path"].parent / "bgm-analysis.json"
            if not force and analysis_path.is_file():
                saved = json.loads(analysis_path.read_text(encoding="utf-8"))
                receipt = saved.get("receipt", {})
                requested_end = min(audio["duration_seconds"], start + duration) if duration else audio["duration_seconds"]
                if (abs(receipt.get("sourceStartSeconds", -1) - start) < .05 and
                        abs(receipt.get("sourceEndSeconds", -1) - requested_end) < .1):
                    return {"status": "completed", "analysis_available": True}
            self._analysis_jobs[key] = {"status": "running"}
            atomic_write_text(audio["path"].parent / "bgm-analysis-status.json", '{"status":"running"}')

        def run():
            try:
                script = Path(__file__).resolve().parents[1] / "skills" / "bgm-collection" / "scripts" / "analyze_bgm.py"
                if not script.is_file():
                    raise ValueError("没有找到 bgm-understanding 分析器。")
                configured = os.getenv("CREATOROS_BGM_PYTHON")
                python = [configured] if configured else (["py", "-3.11"] if os.name == "nt" else [sys.executable])
                with TemporaryDirectory(prefix="creatoros-bgm-understanding-") as temp:
                    target = Path(temp) / "analysis"
                    command = [*python, str(script), "--audio", str(audio["path"]), "--output-dir", str(target), "--start", str(start)]
                    if duration is not None:
                        command.extend(["--duration", str(duration)])
                    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
                    if result.returncode:
                        raise ValueError("BGM 分析未完成：" + (result.stderr.strip() or result.stdout.strip() or "分析器退出失败")[-1000:])
                    def load(path):
                        return json.loads(path.read_text(encoding="utf-8"))
                    self.save_analysis(creator_id, identifier, load(target / "music-analysis.json"),
                                       load(target / "source-receipt.json"), load(target / "onsets" / "events.json"))
                status = {"status": "completed"}
            except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as error:
                status = {"status": "failed", "message": str(error)}
            with self._analysis_lock:
                self._analysis_jobs[key] = status
                atomic_write_text(audio["path"].parent / "bgm-analysis-status.json", json.dumps(status, ensure_ascii=False))

        threading.Thread(target=run, name="creatoros-bgm-analysis", daemon=True).start()
        return {"status": "running"}

    def _import_path(self, creator_id, identifier):
        if self.root is None or not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ValueError("导入记录无效。")
        owner = hashlib.sha256(creator_id.encode()).hexdigest()
        return self.root / "imports" / owner / (identifier + ".json")

    def import_status(self, creator_id, identifier):
        with self.database.session() as session:
            self._creator(session, creator_id)
        path = self._import_path(creator_id, identifier)
        with self._analysis_lock:
            item = self._import_jobs.get((creator_id, identifier))
            if item:
                return dict(item)
        if not path.is_file():
            raise ValueError("导入记录不存在。")
        item = json.loads(path.read_text(encoding="utf-8"))
        if item["status"] == "running":
            return {**item, "status": "interrupted", "message": "上次导入中断，可重新提交链接。"}
        return item

    def list_imports(self, creator_id):
        with self.database.session() as session:
            self._creator(session, creator_id)
        folder = self._import_path(creator_id, "0" * 64).parent
        if not folder.exists():
            return []
        return [self.import_status(creator_id, path.stem) for path in folder.glob("*.json")]

    def start_import(self, creator_id, source_url):
        with self.database.session() as session:
            self._creator(session, creator_id)
        if not isinstance(source_url, str) or not source_url.strip() or len(source_url) > 2000:
            raise ValueError("请提供音乐分享链接。")
        source_url = source_url.strip()
        match = re.search(r"https?://[^\s<>\]\)）]+", source_url)
        if not match:
            raise ValueError("分享内容中没有找到链接。")
        url = match.group(0).rstrip("。，,.!！")
        identity = hashlib.sha256((creator_id + ":" + source_url).encode("utf-8")).hexdigest()
        path = self._import_path(creator_id, identity)
        key = (creator_id, identity)
        with self._analysis_lock:
            active = self._import_jobs.get(key)
            if active and active["status"] == "running":
                return dict(active)
            if path.is_file():
                prior = json.loads(path.read_text(encoding="utf-8"))
                if prior.get("status") == "completed":
                    return prior
            item = {"id": identity, "status": "running", "source_url": source_url}
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, json.dumps(item, ensure_ascii=False))
            self._import_jobs[key] = item

        def run():
            try:
                material = self.from_url(creator_id, url)
            except Exception as error:
                result = {**item, "status": "failed", "message": str(error)}
            else:
                result = {**item, "status": "completed", "material_id": material["id"]}
                try:
                    result["analysis_status"] = self.start_analysis(creator_id, material["id"])["status"]
                except Exception as error:
                    result.update(analysis_status="failed", message=str(error))
            with self._analysis_lock:
                self._import_jobs[key] = result
                atomic_write_text(path, json.dumps(result, ensure_ascii=False))

        try:
            threading.Thread(target=run, name="creatoros-bgm-import", daemon=True).start()
        except RuntimeError as error:
            item = {**item, "status": "failed", "message": str(error)}
            with self._analysis_lock:
                self._import_jobs[key] = item
                atomic_write_text(path, json.dumps(item, ensure_ascii=False))
        return dict(item)

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
