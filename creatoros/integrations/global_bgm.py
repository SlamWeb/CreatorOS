"""Project BGM bytes, persistent import/analysis state, and series references."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore, Lock, Thread, current_thread

from filelock import FileLock, Timeout
from sqlalchemy import or_, select, text

from creatoros.storage.models import (
    Creator, GlobalBgmImport, GlobalBgmLegacySource, GlobalBgmTrack, ManagedMaterial,
    ProductionTask, Series, SeriesBgmTrack, Topic, utc_now,
)
from .atomic_file import atomic_write_bytes
from .bgm_analysis import validate_analysis
from .bgm_share import download_share, share_url
from .managed_materials import EXTENSIONS, MAX_BYTES
from .media_tools import decode_media, media_tool, probe_media
from .whole_bgm_analysis import read_analysis, run_whole_analysis


LOG = logging.getLogger(__name__)
BGM_ID_PATTERN = r"^bgm-[a-f0-9]{64}$"
SHA_PATTERN = r"^[a-f0-9]{64}$"
LEGACY_SCHEMA = "legacy-bgm-analysis-bundle"
UPLOAD_EXTENSIONS = EXTENSIONS | {".mkv", ".m4v", ".opus", ".aif", ".aiff"}
MEDIA_INPUT_OPTIONS = ("-protocol_whitelist", "file,pipe", "-format_whitelist",
                       "aac,aiff,flac,matroska,mov,mp3,ogg,wav,webm")
ANALYSIS_PENDING = {"queued", "running"}


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_metadata(path):
    try:
        info = probe_media(path, input_options=MEDIA_INPUT_OPTIONS, audio_duration=True)
        if not info["has_audio"]:
            raise ValueError("BGM 素材中没有音轨。")
    except subprocess.TimeoutExpired as error:
        raise ValueError("读取音轨超时，请检查文件后重试。") from error
    return info


def _extract_audio(source, directory):
    target = directory / "audio.m4a"
    try:
        result = subprocess.run([media_tool("ffmpeg"), "-nostdin", "-v", "error", "-xerror", *MEDIA_INPUT_OPTIONS,
            "-i", str(source), "-map", "0:a:0", "-vn", "-map_metadata", "-1", "-map_chapters", "-1",
            "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart", "-fs", str(MAX_BYTES), str(target)],
            capture_output=True, timeout=180)
        if result.returncode or not target.is_file():
            raise ValueError("无法从媒体中提取完整音轨。")
        if target.stat().st_size >= MAX_BYTES - 65536:
            raise ValueError("提取的音频超过 64 MiB 限制。")
        return target.read_bytes(), _media_metadata(target)
    except subprocess.TimeoutExpired as error:
        raise ValueError("从视频提取音轨超时，请上传独立音频。") from error


class GlobalBgmStore:
    def __init__(self, database, *, legacy, root=None):
        self.database = database
        self.legacy = legacy
        self.root = Path(root).resolve() if root else legacy.root / "_global_bgm" if legacy.root else None
        self._legacy_lock = Lock()
        self._analysis_guard = Lock()
        self._analysis_thread = None
        self._analysis_queue = {}
        self._analysis_running = None
        self._import_guard = Lock()
        self._import_slots = BoundedSemaphore(2)

    def _begin_write(self, session):
        if self.database.engine.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))

    def _path(self, *segments):
        if self.root is None:
            raise ValueError("此数据库未配置持久 BGM 目录。")
        path = self.root.joinpath(*segments)
        if path.resolve() != path or self.root not in path.parents:
            raise ValueError("BGM 存储路径无效。")
        return path

    def _source_path(self, row):
        if not re.fullmatch(BGM_ID_PATTERN, row.id) or row.suffix not in UPLOAD_EXTENSIONS:
            raise ValueError("BGM 素材路径无效。")
        return self._path(row.id, "source" + row.suffix)

    @staticmethod
    def _verified(path, size, checksum):
        try:
            if not path.is_file() or path.stat().st_size != size or _sha256(path) != checksum:
                raise ValueError("BGM 素材缺失或已变化。")
        except OSError as error:
            raise ValueError("无法读取 BGM 素材。") from error

    def _lock(self, name):
        path = self._path("locks", name + ".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(path), timeout=0, thread_local=False)

    def _is_busy(self, name):
        if not self._path("locks", name + ".lock").exists():
            return False
        try:
            with self._lock(name):
                return False
        except Timeout:
            return True

    def busy(self):
        if self.root is None or not self.root.exists():
            return []
        with self.database.session() as session:
            imports = list(session.scalars(select(GlobalBgmImport.id).where(GlobalBgmImport.status == "running")))
        pending = []
        if (self._analysis_thread and self._analysis_thread.is_alive()) or self._is_busy("analysis"):
            pending.append("bgm_analysis")
        if any(self._is_busy("import-" + identifier) for identifier in imports):
            pending.append("bgm_import")
        return pending

    def _lookup(self, session, identifier, *, lock=False):
        if not re.fullmatch(BGM_ID_PATTERN, identifier):
            raise ValueError("全局 BGM 不存在。")
        row = session.get(GlobalBgmTrack, identifier, with_for_update=lock)
        if row is None:
            raise ValueError("全局 BGM 不存在。")
        return row

    def ensure_active(self, session, identifier):
        row = self._lookup(session, identifier, lock=True)
        if row.archived_at is not None:
            raise ValueError("这首 BGM 已归档，请先恢复或换一首。")
        return row

    def upload(self, name, data_base64):
        if len(data_base64) > MAX_BYTES * 4 // 3 + 8:
            raise ValueError("素材不能超过 64 MiB。")
        try:
            raw = base64.b64decode(data_base64, validate=True)
        except (TypeError, ValueError) as error:
            raise ValueError("素材编码无效。") from error
        return self.put(name, raw)

    def put(self, name, raw, *, source_url=None):
        name = Path(name.replace("\\", "/")).name.strip()[:200]
        suffix = Path(name).suffix.lower()
        if suffix not in UPLOAD_EXTENSIONS or not raw or len(raw) > MAX_BYTES:
            raise ValueError("请上传不超过 64 MiB 的常见音频或带音轨的视频文件。")
        self.sync_legacy()
        checksum = hashlib.sha256(raw).hexdigest()
        identifier = "bgm-" + checksum
        with self.database.session() as session:
            existing = session.get(GlobalBgmTrack, identifier)
            if existing:
                self._verified(self._source_path(existing), existing.size_bytes, existing.sha256)
                return self._view(session, existing)
        with TemporaryDirectory(prefix="creatoros-bgm-audio-") as temporary:
            source = Path(temporary) / ("input" + suffix)
            source.write_bytes(raw)
            metadata = _media_metadata(source)
            if metadata["has_video"] or suffix in (".aif", ".aiff"):
                raw, metadata = _extract_audio(source, Path(temporary))
                suffix = ".m4a"
            else:
                try:
                    decode_media(source, input_options=MEDIA_INPUT_OPTIONS, audio_only=True)
                except subprocess.TimeoutExpired as error:
                    raise ValueError("读取音轨超时，请检查文件后重试。") from error
        created = False
        with self.database.session() as session:
            self._begin_write(session)
            row = session.get(GlobalBgmTrack, identifier)
            if row is None:
                row = self._create_track(session, identifier, raw, name, suffix, metadata,
                                         "share" if source_url else "upload", source_url, "queued")
                created = True
            else:
                self._verified(self._source_path(row), row.size_bytes, row.sha256)
        if created:
            self._wake_analysis(identifier)
        return self.get(identifier)

    def _create_track(self, session, identifier, raw, name, suffix, metadata, source_type, source_url, status):
        checksum = hashlib.sha256(raw).hexdigest()
        row = GlobalBgmTrack(id=identifier, name=name, suffix=suffix, sha256=checksum,
                             size_bytes=len(raw), source_type=source_type, source_url=source_url,
                             duration_seconds=metadata["duration_seconds"], kind="video" if metadata["has_video"] else "audio",
                             analysis_status=status)
        path = self._source_path(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self._verified(path, len(raw), checksum)
        else:
            atomic_write_bytes(path, raw)
        session.add(row)
        session.flush()
        return row

    def sync_legacy(self):
        """Admit verified bytes from legacy rows without rewriting rows or their paths."""
        if self.root is None or self.legacy.root is None:
            return
        with self._legacy_lock:
            with self.database.session() as session:
                rows = list(session.scalars(select(ManagedMaterial).outerjoin(GlobalBgmLegacySource).where(
                    GlobalBgmLegacySource.material_id.is_(None)).order_by(ManagedMaterial.created_at, ManagedMaterial.id)))
            for material in rows:
                try:
                    self._admit_legacy(material)
                except (ValueError, OSError, TypeError, KeyError, AttributeError):
                    LOG.info("Legacy BGM %s unavailable in the shared pool", material.id)

    def _admit_legacy(self, material):
        suffix = material.metadata_json["suffix"]
        if (not re.fullmatch(SHA_PATTERN, material.id) or not re.fullmatch(SHA_PATTERN, material.sha256)
                or suffix not in EXTENSIONS or not 0 < material.size_bytes <= MAX_BYTES):
            raise ValueError("旧版 BGM 素材记录无效。")
        source = self.legacy._path(material)
        raw = source.read_bytes() if source.is_file() and source.stat().st_size == material.size_bytes else b""
        if not raw or hashlib.sha256(raw).hexdigest() != material.sha256:
            raise ValueError("旧版 BGM 素材文件缺失或已变化。")
        metadata = _media_metadata(source)
        identifier = "bgm-" + material.sha256
        if metadata["has_video"]:
            with TemporaryDirectory(prefix="creatoros-bgm-audio-") as temporary:
                raw, metadata = _extract_audio(source, Path(temporary))
            suffix = ".m4a"
        with self.database.session() as session:
            self._begin_write(session)
            if session.get(GlobalBgmLegacySource, material.id):
                return
            row = session.get(GlobalBgmTrack, identifier)
            if row is None:
                row = self._create_track(session, identifier, raw, material.name, suffix, metadata,
                                         "legacy", material.metadata_json.get("source_url"), "not_started")
            session.add(GlobalBgmLegacySource(material_id=material.id, track_id=row.id))
            if not row.legacy_analysis_json and row.sha256 == material.sha256:
                inherited = self._whole_legacy_analysis(row, source)
                if inherited is not None:
                    data, summary = inherited
                    row.legacy_analysis_json = self._save_analysis(row.id, data, {
                        "schema": LEGACY_SCHEMA, "analyzer_version": "legacy-account-analysis", "summary": summary})
                    if row.analysis_status == "not_started":
                        row.analysis_status = "completed"

    @staticmethod
    def _whole_legacy_analysis(row, source):
        path = source.parent / "bgm-analysis.json"
        try:
            if path.resolve() != path:
                return None
            data = read_analysis(path)
            bundle = json.loads(data)
            receipt, onsets = bundle["receipt"], bundle["onsets"]
            validate_analysis(source, receipt, onsets, audio_duration=row.duration_seconds)
            if (abs(float(receipt["sourceStartSeconds"])) > .001 or
                    abs(float(receipt["sourceEndSeconds"]) - row.duration_seconds) > .05 or
                    not isinstance(bundle["analysis"], dict) or not bundle["analysis"] or
                    not isinstance(onsets["events"], list)):
                return None
            return data, {"duration_seconds": row.duration_seconds, "onset_count": len(onsets["events"])}
        except (ValueError, OSError, TypeError, KeyError, AttributeError, OverflowError):
            return None

    def _save_analysis(self, identifier, data, metadata):
        checksum = hashlib.sha256(data).hexdigest()
        path = self._path(identifier, "analysis", checksum + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self._verified(path, len(data), checksum)
        else:
            atomic_write_bytes(path, data)
        return {**metadata, "sha256": checksum, "size_bytes": len(data)}

    def _available_analysis(self, row):
        for saved in (row.analysis_json, row.legacy_analysis_json):
            if not saved:
                continue
            checksum = saved.get("sha256", "")
            try:
                if not re.fullmatch(SHA_PATTERN, checksum):
                    continue
                path = self._path(row.id, "analysis", checksum + ".json")
                self._verified(path, saved["size_bytes"], checksum)
            except (ValueError, KeyError):
                continue
            return {**saved, "path": path}
        return None

    def _analysis_status(self, row, *, include_summary=False):
        available = self._available_analysis(row)
        status, message = row.analysis_status, row.analysis_message
        live = ((self._analysis_thread and self._analysis_thread.is_alive() and
                 (row.id in self._analysis_queue or row.id == self._analysis_running)) or
                (status in ANALYSIS_PENDING and self._is_busy("analysis-" + row.id)))
        if status in ANALYSIS_PENDING and not live:
            status, message = "interrupted", "上次分析中断，可以重新开始。"
        if status == "completed" and not available:
            status, message = "failed", "已保存的分析缺失或已变化，可以重新分析。"
        view = {"status": status, "message": message, "analysis_available": bool(available),
                "schema": available.get("schema") if available else None,
                "analyzer_version": available.get("analyzer_version") if available else None}
        if include_summary and available:
            view.update(available.get("summary", {}))
        return view

    def _view(self, session, row):
        analysis = self._analysis_status(row)
        return {"id": row.id, "name": row.name, "sha256": row.sha256, "source_sha256": row.id[4:], "kind": row.kind,
                "size_bytes": row.size_bytes, "duration_seconds": row.duration_seconds,
                "source_type": row.source_type, "source_url": row.source_url, "url": f"/api/bgm/{row.id}/file",
                "archived": row.archived_at is not None, "analysis_status": analysis["status"],
                "analysis_available": analysis["analysis_available"], "analysis_message": analysis["message"],
                "reference_count": len(self._references(session, row.id))}

    def list(self, q="", archived=False, *, sync_legacy=True):
        if sync_legacy:
            self.sync_legacy()
        needle = q.strip().casefold()
        with self.database.session() as session:
            rows = session.scalars(select(GlobalBgmTrack).where(GlobalBgmTrack.archived_at.is_not(None) if archived
                else GlobalBgmTrack.archived_at.is_(None)).order_by(GlobalBgmTrack.created_at.desc(), GlobalBgmTrack.id))
            return [self._view(session, row) for row in rows if not needle or
                    needle in row.name.casefold() or needle in (row.source_url or "").casefold()]

    def get(self, identifier):
        with self.database.session() as session:
            return self._view(session, self._lookup(session, identifier))

    def resolve(self, identifier):
        with self.database.session() as session:
            row = self._lookup(session, identifier)
            path = self._source_path(row)
            self._verified(path, row.size_bytes, row.sha256)
            return {**self._view(session, row), "path": path}

    def resolve_analysis(self, identifier):
        with self.database.session() as session:
            row = self._lookup(session, identifier)
            self._verified(self._source_path(row), row.size_bytes, row.sha256)
            saved = self._available_analysis(row)
            return {key: saved[key] for key in ("path", "sha256", "schema")} if saved else None

    def analysis_status(self, identifier):
        with self.database.session() as session:
            return self._analysis_status(self._lookup(session, identifier), include_summary=True)

    def start_analysis(self, identifier, *, force=False):
        with self.database.session() as session:
            self._begin_write(session)
            row = self.ensure_active(session, identifier)
            self._verified(self._source_path(row), row.size_bytes, row.sha256)
            status = self._analysis_status(row, include_summary=True)
            if status["status"] in ANALYSIS_PENDING:
                if force:
                    raise ValueError("全曲分析正在排队或运行，完成后可以重新分析。")
                return status
            if not force and status["analysis_available"]:
                row.analysis_status, row.analysis_message = "completed", None
                return self._analysis_status(row, include_summary=True)
            row.analysis_status, row.analysis_message = "queued", None
        self._wake_analysis(identifier)
        return self.analysis_status(identifier)

    def _wake_analysis(self, identifier):
        with self._analysis_guard:
            self._analysis_queue[identifier] = None
            if self._analysis_thread and self._analysis_thread.is_alive():
                return
            self._analysis_thread = Thread(target=self._drain_analysis, name="creatoros-whole-bgm-analysis", daemon=True)
            try:
                self._analysis_thread.start()
            except RuntimeError:
                self._analysis_queue.clear()
                self._analysis_thread = None
                LOG.warning("Whole-track BGM worker unavailable")

    def _drain_analysis(self):
        try:
            with self._lock("analysis").acquire(timeout=1800):
                while True:
                    with self._analysis_guard:
                        if not self._analysis_queue:
                            self._analysis_thread = None
                            return
                        identifier = next(iter(self._analysis_queue))
                        del self._analysis_queue[identifier]
                        self._analysis_running = identifier
                    try:
                        with self._lock("analysis-" + identifier):
                            with self.database.session() as session:
                                self._begin_write(session)
                                row = self._lookup(session, identifier, lock=True)
                                if row.analysis_status != "queued":
                                    continue
                                row.analysis_status = "running"
                            self._run_analysis(identifier)
                    finally:
                        self._analysis_running = None
        except Exception as error:
            LOG.warning("Whole-track BGM queue stopped (%s)", type(error).__name__)
        finally:
            with self._analysis_guard:
                if self._analysis_thread is current_thread():
                    self._analysis_queue.clear()
                    self._analysis_thread = None

    def _run_analysis(self, identifier):
        try:
            existing = self.resolve(identifier)
            with TemporaryDirectory(prefix="creatoros-whole-bgm-") as directory:
                raw, metadata = run_whole_analysis(existing, Path(directory))
                self._verified(existing["path"], existing["size_bytes"], existing["sha256"])
                saved = self._save_analysis(identifier, raw, metadata)
        except Exception as error:
            LOG.warning("Whole-track BGM analysis %s failed (%s)", identifier, type(error).__name__)
            status = "failed"
            message = str(error) if isinstance(error, ValueError) else "全曲分析失败，请检查素材或本机分析环境后重试。"
            saved = None
        else:
            status, message = "completed", None
        with self.database.session() as session:
            self._begin_write(session)
            row = self._lookup(session, identifier, lock=True)
            row.analysis_status, row.analysis_message = status, message
            if saved:
                row.analysis_json = saved

    def _set_archived(self, identifier, archived):
        with self.database.session() as session:
            self._begin_write(session)
            row = self._lookup(session, identifier, lock=True)
            if archived != (row.archived_at is not None):
                row.archived_at = utc_now() if archived else None
                session.flush()
            return self._view(session, row)

    def archive(self, identifier):
        return self._set_archived(identifier, True)

    def restore(self, identifier):
        return self._set_archived(identifier, False)

    @staticmethod
    def _series(session, series_id, *, active=False):
        series = session.get(Series, series_id, with_for_update=active)
        if not series:
            raise ValueError("栏目不存在。")
        if active and (not series.is_active or not series.creator or not series.creator.is_active):
            raise ValueError("请在启用的账号栏目中变更播放列表。")
        return series

    def list_series(self, series_id):
        with self.database.session() as session:
            self._series(session, series_id)
            rows = session.scalars(select(GlobalBgmTrack).join(SeriesBgmTrack).where(SeriesBgmTrack.series_id == series_id)
                                   .order_by(SeriesBgmTrack.created_at, GlobalBgmTrack.id))
            return [self._view(session, row) for row in rows]

    def add_to_series(self, series_id, identifier):
        with self.database.session() as session:
            self._begin_write(session)
            self._series(session, series_id, active=True)
            row = self.ensure_active(session, identifier)
            if not session.get(SeriesBgmTrack, (series_id, identifier)):
                session.add(SeriesBgmTrack(series_id=series_id, track_id=identifier))
                session.flush()
            return self._view(session, row)

    def remove_from_series(self, series_id, identifier):
        with self.database.session() as session:
            self._begin_write(session)
            self._series(session, series_id, active=True)
            item = session.get(SeriesBgmTrack, (series_id, identifier), with_for_update=True)
            if item:
                session.delete(item)
            return {"removed": item is not None}

    @staticmethod
    def _references(session, identifier):
        playlist = select(Series.id, Series.name, Creator.id, Creator.display_name).join_from(SeriesBgmTrack, Series
            ).outerjoin(Creator).where(SeriesBgmTrack.track_id == identifier).order_by(Series.id)
        references = [dict(kind="playlist", series_id=series_id, series_name=name, creator_id=creator_id, creator_name=creator)
                      for series_id, name, creator_id, creator in session.execute(playlist)]
        legacy_ids = select(GlobalBgmLegacySource.material_id).where(GlobalBgmLegacySource.track_id == identifier)
        inputs = ProductionTask.definition_json["inputs"]
        tasks = (select(ProductionTask.id, ProductionTask.title, Series.id, Series.name, Creator.id, Creator.display_name)
                 .select_from(ProductionTask).join(Topic).join(Series).outerjoin(Creator).where(or_(
                     inputs["bgm_track_id"].as_string() == identifier,
                     inputs["bgm_material_id"].as_string().in_(legacy_ids)))
                 .order_by(ProductionTask.id))
        references.extend(dict(kind="production_task", task_id=task_id, task_title=title, series_id=series_id,
                               series_name=name, creator_id=creator_id, creator_name=creator)
                          for task_id, title, series_id, name, creator_id, creator in session.execute(tasks))
        return references

    def usage(self, identifier):
        with self.database.session() as session:
            self._lookup(session, identifier)
            references = self._references(session, identifier)
            return {"reference_count": len(references), "references": references}

    def _import_view(self, row, *, interrupted=False):
        status, message = row.status, row.message
        if status == "running" and (interrupted or not self._is_busy("import-" + row.id)):
            status, message = "interrupted", "上次分享导入中断，可重试。"
        return {"id": row.id, "source_url": row.source_url, "status": status, "message": message, "track_id": row.track_id}

    def list_imports(self):
        with self.database.session() as session:
            return [self._import_view(row) for row in session.scalars(select(GlobalBgmImport)
                    .order_by(GlobalBgmImport.updated_at.desc(), GlobalBgmImport.id))]

    @staticmethod
    def _import_row(session, identifier):
        row = session.get(GlobalBgmImport, identifier) if re.fullmatch(SHA_PATTERN, identifier) else None
        if row is None:
            raise ValueError("分享导入记录不存在。")
        return row

    def import_status(self, identifier):
        with self.database.session() as session:
            return self._import_view(self._import_row(session, identifier))

    def start_import(self, source_url):
        url = share_url(source_url)
        identifier = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self._start_import(identifier, source_url.strip(), url, retry=False)

    def retry_import(self, identifier):
        with self.database.session() as session:
            row = self._import_row(session, identifier)
        return self._start_import(identifier, row.source_url, row.media_url, retry=True)

    def _start_import(self, identifier, share, url, *, retry):
        with self._import_guard:
            return self._launch_import(identifier, share, url, retry=retry)

    def _launch_import(self, identifier, share, url, *, retry):
        lock = self._lock("import-" + identifier)
        try:
            lock.acquire()
        except Timeout:
            return self.import_status(identifier)
        started = False
        reserved = False
        try:
            with self.database.session() as session:
                self._begin_write(session)
                row = session.get(GlobalBgmImport, identifier)
                if row and (row.status == "completed" or not retry):
                    return self._import_view(row, interrupted=row.status == "running")
                reserved = self._import_slots.acquire(blocking=False)
                if not reserved:
                    raise ValueError("目前已有两项导入正在运行，请稍后重试。")
                if row is None:
                    row = GlobalBgmImport(id=identifier, source_url=share, media_url=url)
                    session.add(row)
                row.status, row.message = "running", None
            thread = Thread(target=self._run_import, args=(identifier, url, lock), name="creatoros-bgm-share-import", daemon=True)
            try:
                thread.start()
            except RuntimeError:
                with self.database.session() as session:
                    self._begin_write(session)
                    row = self._import_row(session, identifier)
                    row.status, row.message = "failed", "无法启动分享导入，请稍后重试。"
                return self.import_status(identifier)
            started = True
        finally:
            if not started:
                lock.release()
                if reserved:
                    self._import_slots.release()
        return self.import_status(identifier)

    def _run_import(self, identifier, url, lock):
        try:
            try:
                name, raw = download_share(url)
                track = self.put(name, raw, source_url=url)
            except Exception as error:
                LOG.warning("BGM share import %s failed (%s)", identifier, type(error).__name__)
                status, track_id = "failed", None
                message = str(error) if isinstance(error, ValueError) else "分享链接导入失败，可重试或上传文件。"
            else:
                status, message, track_id = "completed", None, track["id"]
            with self.database.session() as session:
                self._begin_write(session)
                row = self._import_row(session, identifier)
                row.status, row.message, row.track_id = status, message, track_id
        finally:
            lock.release()
            self._import_slots.release()
