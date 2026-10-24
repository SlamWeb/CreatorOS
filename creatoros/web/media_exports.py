"""Immutable ZIP receipts, stored beside this service's SQLite database only."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from tempfile import SpooledTemporaryFile, mkdtemp
from urllib.parse import quote, urlencode
from zipfile import BadZipFile, ZipFile

from creatoros.integrations.atomic_file import atomic_write_text
from creatoros.storage import ContentRevision
from .media_collections import _digest, _error


class _SeekableArchive:
    """Python 3.10 SpooledTemporaryFile lacks the seekable method ZipFile reads."""
    def __init__(self, archive):
        self.archive = archive

    def seekable(self):
        return True

    def __getattr__(self, name):
        return getattr(self.archive, name)


class MediaExportStore:
    def __init__(self, service):
        self.service = service
        database_file = service.database.engine.url.database
        self.root = (Path(database_file).resolve().with_suffix(".media-exports")
                     if database_file and database_file != ":memory:" else None)

    def _directory(self, export_id, *, create_root=False):
        if not isinstance(export_id, str) or re.fullmatch(r"[a-f0-9]{64}", export_id) is None:
            raise _error("导出不存在。", "not_found", 404)
        if self.root is None:
            raise _error("此数据库未配置持久导出目录。", "export_unavailable", 503)
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise _error("导出目录无法核验。", "export_unavailable")
        if create_root:
            self.root.mkdir(parents=False, exist_ok=True)
        directory = self.root / export_id
        if directory.is_symlink() or directory.resolve() != directory:
            raise _error("导出目录无法核验。", "export_unavailable")
        return directory

    @staticmethod
    def _file(directory, name):
        path = directory / name
        if path.is_symlink() or path.resolve() != path:
            raise _error("导出文件无法核验。", "export_unavailable")
        return path

    def _bindings(self, creator_id, identifier, manifest, *, check_images=True):
        """Validate exact source membership without depending on today's ordering."""
        bindings = []
        for item in manifest["items"]:
            digest = self.service._image_binding(
                creator_id, identifier, item["run_id"], item["revision_id"])
            if digest != item["artifact_digest"]:
                raise _error("导出来源版本已变化。", "source_unavailable")
            with self.service.database.session() as session:
                run, topic = self.service._run(session, creator_id, item["run_id"])
                revision = session.get(ContentRevision, item["revision_id"])
                if revision is None or revision.content_run_id != run.id:
                    raise _error("导出来源版本已变化。", "source_unavailable")
                self.service._frozen(revision.production_input_json, creator_id, topic)
                bindings.append(dict(run_id=run.id, revision_id=revision.id,
                    run_owner={key: run.input_snapshot_json.get(key)
                               for key in ("creator_id", "series_id", "topic_id")},
                    revision_owner={key: revision.production_input_json.get(key)
                                    for key in ("creator_id", "series_id", "topic_id")}))
            # Reuse the artifact validator for original bytes and frozen evidence.
            if check_images:
                self.service._read_item(item)
        return bindings

    @staticmethod
    def _receipt(metadata):
        creator_id, identifier, export_id = (
            quote(metadata[key], safe="") for key in ("creator_id", "collection_id", "export_id"))
        base = f"/api/creators/{creator_id}/collections/{identifier}"
        return {key: metadata[key] for key in (
            "export_id", "collection_id", "creator_id", "name", "selected_count", "size_bytes", "checksum")
        } | {"url": f"{base}/exports/{export_id}/download",
             "collection_url": "/?" + urlencode(dict(creator=metadata["creator_id"],
                                                      view="materials", collection=metadata["collection_id"]))}

    def _load(self, creator_id, identifier, export_id):
        # Check account/collection before touching any receipt on disk.
        with self.service.database.session() as session:
            self.service._creator(session, creator_id)
            self.service._row(session, creator_id, identifier)
        directory = self._directory(export_id)
        try:
            path = self._file(directory, "metadata.json")
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("Oversized receipt")
            metadata = json.loads(path.read_text(encoding="utf-8"))
            if (metadata["creator_id"] != creator_id or metadata["collection_id"] != identifier
                    or metadata["export_id"] != export_id):
                raise _error("导出不存在。", "not_found", 404)
            if export_id != _digest([creator_id, identifier, metadata["manifest"]["snapshot_digest"]]):
                raise ValueError("Export identity changed")
            if metadata["bindings"] != self._bindings(creator_id, identifier, metadata["manifest"]):
                raise _error("导出来源的冻结归属已变化。", "source_unavailable")
            archive = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
            try:
                checksum, size = hashlib.sha256(), 0
                with self._file(directory, "images.zip").open("rb") as stream:
                    while chunk := stream.read(64 * 1024):
                        checksum.update(chunk)
                        size += len(chunk)
                        archive.write(chunk)
                if size != metadata["size_bytes"] or checksum.hexdigest() != metadata["checksum"]:
                    raise _error("导出 ZIP 缺失、损坏或已变化。", "export_unavailable")
                archive.seek(0)
                with ZipFile(_SeekableArchive(archive)) as bundle:
                    manifest = json.loads(bundle.read("manifest.json"))
                if manifest != metadata["manifest"]:
                    raise ValueError("Manifest changed")
                if metadata["bindings"] != self._bindings(
                        creator_id, identifier, manifest, check_images=False):
                    raise _error("导出来源的冻结归属已变化。", "source_unavailable")
                archive.seek(0)
                return metadata, archive
            except Exception:
                archive.close()
                raise
        except FileNotFoundError as error:
            raise _error("导出不存在或文件已移除。", "not_found", 404) from error
        except (OSError, ValueError, KeyError, TypeError, BadZipFile) as error:
            raise _error("导出文件缺失、损坏或无法核验。", "export_unavailable") from error

    def create(self, creator_id, identifier, version, snapshot):
        export_id = _digest([creator_id, identifier, snapshot])
        with self.service.lock:
            detail = self.service.get(creator_id, identifier)
            self.service._snapshot(detail, version, snapshot)
            directory = self._directory(export_id, create_root=True)
            if directory.exists():
                return self.get(creator_id, identifier, export_id)
            archive = self.service.download(creator_id, identifier, version=version, snapshot=snapshot)
            temporary = None
            try:
                with ZipFile(_SeekableArchive(archive)) as bundle:
                    manifest = json.loads(bundle.read("manifest.json"))
                bindings = self._bindings(creator_id, identifier, manifest)
                temporary = Path(mkdtemp(prefix=f".{export_id}-", dir=self.root))
                checksum, size = hashlib.sha256(), 0
                archive.seek(0)
                with (temporary / "images.zip").open("xb") as stream:
                    while chunk := archive.read(64 * 1024):
                        stream.write(chunk)
                        checksum.update(chunk)
                        size += len(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                metadata = dict(schema_version=1, export_id=export_id, creator_id=creator_id,
                    collection_id=identifier, name=manifest["name"], selected_count=manifest["selected_count"],
                    size_bytes=size, checksum=checksum.hexdigest(), manifest=manifest, bindings=bindings)
                atomic_write_text(temporary / "metadata.json", json.dumps(metadata, ensure_ascii=False))
                self.service._snapshot(self.service.get(creator_id, identifier), version, snapshot)
                try:
                    # Atomic, complete directory publication; existing nonempty exports cannot be replaced.
                    temporary.rename(directory)
                except OSError:
                    if not directory.exists():
                        raise
                return self.get(creator_id, identifier, export_id)
            except OSError as error:
                raise _error("导出保存失败，请核对导出状态。", "export_unavailable") from error
            finally:
                archive.close()
                if temporary is not None and temporary.exists():
                    for name in ("images.zip", "metadata.json"):
                        (temporary / name).unlink(missing_ok=True)
                    temporary.rmdir()

    def get(self, creator_id, identifier, export_id):
        metadata, archive = self._load(creator_id, identifier, export_id)
        archive.close()
        return self._receipt(metadata)

    def download(self, creator_id, identifier, export_id):
        _metadata, archive = self._load(creator_id, identifier, export_id)
        return archive
