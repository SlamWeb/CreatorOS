"""Reference-only, account-scoped collections of exact historical Run images."""
from __future__ import annotations

import hashlib
import json
from io import BytesIO
from tempfile import SpooledTemporaryFile
from threading import RLock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from zipfile import ZIP_STORED, ZipFile

from PIL import Image, ImageStat
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from creatoros.runs import ContentRunError
from creatoros.storage import (
    ContentRevision, ContentRun, Creator, MediaCollection, ProductionTask, Series, Topic, TopicRemoval,
)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _error(message, code="collection_conflict", status=409):
    return ContentRunError(message, code=code, status_code=status)


def _unique(values, label):
    if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
        raise _error(f"{label}必须是有效 ID 列表。", "invalid_collection", 422)
    if len(values) != len(set(values)):
        raise _error(f"{label}不能重复。", "invalid_collection", 422)
    return list(values)


class MediaCollectionService:
    def __init__(self, database, artifacts):
        self.database = database
        self.artifacts = artifacts
        self.lock = RLock()
        from .media_exports import MediaExportStore
        self.exports = MediaExportStore(self)

    def export(self, creator_id, identifier, *, expected_version, snapshot_digest):
        return self.exports.create(creator_id, identifier, expected_version, snapshot_digest)

    def get_export(self, creator_id, identifier, export_id):
        return self.exports.get(creator_id, identifier, export_id)

    def download_export(self, creator_id, identifier, export_id):
        return self.exports.download(creator_id, identifier, export_id)

    @staticmethod
    def _creator(session, creator_id):
        creator = session.get(Creator, creator_id)
        if creator is None:
            raise _error("账号不存在。", "not_found", 404)

    @staticmethod
    def _topic(session, creator_id, topic_id):
        topic = session.get(Topic, topic_id)
        series = session.get(Series, topic.series_id) if topic else None
        if (topic is None or series is None or series.creator_id != creator_id
                or not series.is_active or session.get(TopicRemoval, topic_id) is not None):
            raise _error("选题不存在或不属于当前账号。", "not_found", 404)
        return topic

    def _run(self, session, creator_id, run_id):
        run = session.get(ContentRun, run_id)
        if run is None:
            raise _error("来源任务不存在或不可用。", "not_found", 404)
        topic = self._topic(session, creator_id, run.topic_id)
        self._frozen(run.input_snapshot_json, creator_id, topic)
        return run, topic

    @staticmethod
    def _frozen(data, creator_id, topic):
        if (not isinstance(data, dict) or data.get("creator_id") != creator_id
                or data.get("series_id") != topic.series_id or data.get("topic_id") != topic.id):
            raise _error("来源任务的冻结归属与当前归属不一致。", "source_unavailable")

    @staticmethod
    def _row(session, creator_id, identifier):
        row = session.get(MediaCollection, identifier)
        if row is None or row.creator_id != creator_id:
            raise _error("素材文件夹不存在。", "not_found", 404)
        return row

    @staticmethod
    def _summary(row):
        return dict(id=row.id, name=row.name, version=row.version, topic_id=row.topic_id,
                    run_ids=list(row.run_ids_json), created_at=row.created_at, updated_at=row.updated_at)

    def list(self, creator_id):
        with self.database.session() as session:
            self._creator(session, creator_id)
            return [self._summary(row) for row in session.scalars(select(MediaCollection).where(
                MediaCollection.creator_id == creator_id).order_by(
                    MediaCollection.created_at.desc(), MediaCollection.id))]

    def sources(self, creator_id, topic_id=None):
        with self.database.session() as session:
            self._creator(session, creator_id)
            if topic_id:
                self._topic(session, creator_id, topic_id)
            # This is a lightweight source catalogue, not an artifact review. Read
            # only metadata in one joined query; image verification is deferred to get().
            statement = (select(
                ContentRun.id.label("run_id"), ContentRun.status,
                ContentRun.input_snapshot_json["creator_id"].as_string().label("run_creator"),
                ContentRun.input_snapshot_json["series_id"].as_string().label("run_series"),
                ContentRun.input_snapshot_json["topic_id"].as_string().label("run_topic"),
                ContentRun.input_snapshot_json["topic_title"].as_string().label("run_title"),
                Topic.id.label("topic_id"), Topic.title.label("topic_title"), Topic.series_id,
                ContentRevision.id.label("revision_id"), ContentRevision.revision_number,
                ContentRevision.artifact_digest, ContentRevision.validation_json,
                ContentRevision.production_input_json["creator_id"].as_string().label("revision_creator"),
                ContentRevision.production_input_json["series_id"].as_string().label("revision_series"),
                ContentRevision.production_input_json["topic_id"].as_string().label("revision_topic"),
            ).select_from(ContentRun).join(Topic).join(Series).outerjoin(ContentRevision).where(
                Series.creator_id == creator_id, Series.is_active.is_(True),
                ~select(TopicRemoval.topic_id).where(TopicRemoval.topic_id == Topic.id).exists())
                .order_by(ContentRun.created_at, ContentRun.id, ContentRevision.revision_number))
            if topic_id:
                statement = statement.where(ContentRun.topic_id == topic_id)
            sources = []
            for row in session.execute(statement).mappings():
                expected = (creator_id, row["series_id"], row["topic_id"])
                run_owner = (row["run_creator"], row["run_series"], row["run_topic"])
                revision_owner = (row["revision_creator"], row["revision_series"], row["revision_topic"])
                source = dict(run_id=row["run_id"], run_title=row["run_title"] or row["topic_title"],
                    topic_id=row["topic_id"], topic_title=row["topic_title"], status=row["status"].value,
                    revision_id=row["revision_id"], revision_number=row["revision_number"], image_count=0)
                if run_owner != expected or (row["revision_id"] is not None and revision_owner != expected):
                    if run_owner != expected:
                        source["run_title"] = "不可用的来源"
                    source["error"] = "来源任务的冻结归属与当前归属不一致。"
                elif row["artifact_digest"]:
                    saved = row["validation_json"]
                    count = saved.get("card_count") if isinstance(saved, dict) else None
                    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                        source["image_count"] = count
                    else:
                        source["error"] = "此版本缺少可核验的图片数量记录。"
                sources.append(source)
            return sources

    def create(self, creator_id, *, name, request_id, topic_id=None, run_ids=None):
        name = self._name(name)
        run_ids = _unique(run_ids or [], "来源任务")
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise _error("创建请求标识无效。", "invalid_collection", 422)
        digest = _digest(dict(name=name, topic_id=topic_id, run_ids=run_ids))
        with self.lock:
            try:
                with self.database.session() as session:
                    self._creator(session, creator_id)
                    existing = session.scalar(select(MediaCollection).where(
                        MediaCollection.creator_id == creator_id, MediaCollection.request_id == request_id))
                    if existing:
                        if existing.create_digest != digest:
                            raise _error("同一创建请求已用于不同内容。", "request_conflict")
                        identifier = existing.id
                    else:
                        if topic_id:
                            self._topic(session, creator_id, topic_id)
                        for run_id in run_ids:
                            self._run(session, creator_id, run_id)
                        identifier = str(uuid4())
                        session.add(MediaCollection(id=identifier, creator_id=creator_id,
                            topic_id=topic_id, name=name, request_id=request_id, create_digest=digest,
                            run_ids_json=run_ids, order_ids_json=[], excluded_ids_json=[]))
            except IntegrityError as error:
                # A second service instance may have committed the same request first.
                with self.database.session() as session:
                    row = session.scalar(select(MediaCollection).where(
                        MediaCollection.creator_id == creator_id, MediaCollection.request_id == request_id))
                    if row is None or row.create_digest != digest:
                        raise _error("素材文件夹创建冲突，请刷新。", "request_conflict") from error
                    identifier = row.id
        return self.get(creator_id, identifier)

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise _error("文件夹名称应为 1–120 字。", "invalid_collection", 422)
        return name.strip()

    def get(self, creator_id, identifier):
        with self.database.session() as session:
            self._creator(session, creator_id)
            row = self._row(session, creator_id, identifier)
            summary = self._summary(row)
            order_ids, excluded_ids = list(row.order_ids_json), set(row.excluded_ids_json)
            run_ids = list(row.run_ids_json)
            task_run_ids = set()
            if row.topic_id:
                self._topic(session, creator_id, row.topic_id)
                automatic, task_run_ids = self._automatic_runs(session, row)
                run_ids = list(dict.fromkeys(automatic + run_ids))
        items, sources = [], []
        for run_id in run_ids:
            source_items, source_rows = self._source(creator_id, run_id, active_only=run_id in task_run_ids)
            items.extend(source_items)
            sources.extend(source_rows)
        ranks = {item_id: index for index, item_id in enumerate(order_ids)}
        items.sort(key=lambda item: (0, ranks[item["id"]]) if item["id"] in ranks else (1,))
        for item in items:
            item["included"] = item["id"] not in excluded_ids
        # Hash source identity and selection, not transport URL formatting.
        snapshot = _digest(dict(version=summary["version"], items=items, sources=sources))
        for item in items:
            item["url"] = (f"/api/creators/{creator_id}/collections/{identifier}/items/"
                           f"{item['run_id']}/{item['revision_id']}/{item['order']}"
                           f"?checksum={item['checksum']}")
        return dict(**summary, items=items, sources=sources, snapshot_digest=snapshot)

    @staticmethod
    def _automatic_runs(session, row):
        """Task collections select current task outputs, in task order, not arrival order."""
        tasks = list(session.scalars(select(ProductionTask).where(
            ProductionTask.collection_id == row.id, ProductionTask.kind == "image").order_by(ProductionTask.position, ProductionTask.id)))
        if not tasks:
            automatic = list(session.scalars(select(ContentRun.id).where(
                ContentRun.topic_id == row.topic_id,
                ContentRun.input_snapshot_json["execution_spec"]["kind"].as_string().is_distinct_from("video")
                ).order_by(ContentRun.created_at, ContentRun.id)))
            return automatic, set()
        latest = {}
        for run in session.scalars(select(ContentRun).where(
                ContentRun.task_id.in_([task.id for task in tasks])).order_by(
                ContentRun.created_at.desc(), ContentRun.id.desc())):
            latest.setdefault(run.task_id, run.id)
        automatic = [latest[task.id] for task in tasks if task.id in latest]
        return automatic, set(automatic)

    def _source(self, creator_id, run_id, *, active_only=False):
        try:
            with self.database.session() as session:
                run, topic = self._run(session, creator_id, run_id)
                title = run.input_snapshot_json.get("topic_title") or topic.title
                topic_title, status, topic_id = topic.title, run.status.value, topic.id
                statement = select(ContentRevision).where(ContentRevision.content_run_id == run_id)
                if active_only:
                    statement = statement.where(ContentRevision.revision_number == run.active_revision_number)
                revisions = list(session.scalars(statement.order_by(ContentRevision.revision_number)))
                records = [(revision.id, revision.revision_number, revision.artifact_digest,
                            revision.artifact_directory, revision.production_input_json) for revision in revisions]
        except ContentRunError:
            return [], [dict(run_id=run_id, run_title="不可用的来源", topic_id=None, topic_title=None, status="unavailable",
                revision_id=None, revision_number=None, image_count=0,
                error="来源已移除、归属已变化或无法核验。")]
        items, sources = [], []
        if not records:
            return [], [dict(run_id=run_id, run_title=title, topic_id=topic_id, topic_title=topic_title, status=status,
                            revision_id=None, revision_number=None, image_count=0)]
        for revision_id, number, digest, directory, frozen in records:
            source = dict(run_id=run_id, run_title=title, topic_id=topic_id, topic_title=topic_title, status=status,
                          revision_id=revision_id, revision_number=number, image_count=0)
            try:
                self._frozen(frozen, creator_id, topic)
                if not digest and not directory:
                    sources.append(source)
                    continue
                if not digest or not directory:
                    raise ValueError("Incomplete artifact binding")
                projection = self.artifacts.projection(run_id, revision_id)
                if not projection.get("artifact_available"):
                    raise ValueError("Artifact unavailable")
                source_items = []
                for card in projection["cards"]:
                    query = parse_qs(urlsplit(card.url).query)
                    checksum = query["checksum"][0]
                    source_items.append(dict(id=_digest([run_id, revision_id, card.order]),
                        run_id=run_id, run_title=title, revision_id=revision_id,
                        revision_number=number, order=card.order, checksum=checksum,
                        width=card.width, height=card.height, artifact_digest=digest))
                # Ownership can change while filesystem validation runs.
                with self.database.session() as session:
                    _run, latest_topic = self._run(session, creator_id, run_id)
                    latest = session.get(ContentRevision, revision_id)
                    if latest is None or latest.content_run_id != run_id or latest.artifact_digest != digest:
                        raise ValueError("Revision changed")
                    self._frozen(latest.production_input_json, creator_id, latest_topic)
                items.extend(source_items)
                source["image_count"] = len(source_items)
            except (ContentRunError, OSError, ValueError, KeyError, TypeError, AttributeError,
                    SyntaxError, Image.DecompressionBombError):
                source["error"] = "此版本产物缺失、损坏、已变化或归属无法核验。"
            sources.append(source)
        return items, sources

    def patch(self, creator_id, identifier, *, expected_version, **changes):
        with self.lock:
            current = self.get(creator_id, identifier)
            self._version(current, expected_version)
            run_ids = _unique(changes.get("run_ids", current["run_ids"]), "来源任务")
            # Validate new sources before saving; unavailable old references can be removed.
            if "run_ids" in changes:
                with self.database.session() as session:
                    for run_id in run_ids:
                        self._run(session, creator_id, run_id)
            if "run_ids" in changes:
                with self.database.session() as session:
                    row = self._row(session, creator_id, identifier)
                    topic_id = row.topic_id
                available = set()
                resolved_runs = list(run_ids)
                if topic_id:
                    with self.database.session() as session:
                        row = self._row(session, creator_id, identifier)
                        automatic, _ = self._automatic_runs(session, row)
                        resolved_runs += automatic
                for run_id in dict.fromkeys(resolved_runs):
                    source_items, _ = self._source(creator_id, run_id)
                    available.update(item["id"] for item in source_items)
            else:
                available = {item["id"] for item in current["items"]}
            for field in ("order_ids", "excluded_ids"):
                if field in changes:
                    values = _unique(changes[field], field)
                    if not set(values).issubset(available):
                        raise _error("选片列表包含已失效或不属于此文件夹的图片。", "snapshot_changed")
            try:
                with self.database.session() as session:
                    row = self._row(session, creator_id, identifier)
                    self._version(dict(version=row.version), expected_version)
                    if "name" in changes:
                        row.name = self._name(changes["name"])
                    row.run_ids_json = run_ids
                    for field in ("order_ids", "excluded_ids"):
                        if field in changes:
                            setattr(row, f"{field}_json", list(changes[field]))
                    # Even an explicit no-op consumes its CAS version.
                    row.version += 1
            except StaleDataError as error:
                raise _error("文件夹已更新，请刷新后重试。", "version_conflict") from error
        return self.get(creator_id, identifier)

    @staticmethod
    def _version(detail, expected):
        if detail["version"] != expected:
            raise _error("文件夹已更新，请刷新后重试。", "version_conflict")

    @staticmethod
    def _snapshot(detail, version, snapshot):
        MediaCollectionService._version(detail, version)
        if detail["snapshot_digest"] != snapshot:
            raise _error("来源图片已变化，请刷新后重试。", "snapshot_changed")

    def delete(self, creator_id, identifier, *, expected_version):
        with self.lock:
            try:
                with self.database.session() as session:
                    self._creator(session, creator_id)
                    row = self._row(session, creator_id, identifier)
                    self._version(dict(version=row.version), expected_version)
                    if session.scalar(select(ProductionTask.id).where(
                            ProductionTask.collection_id == identifier).limit(1)):
                        raise _error("这是选题制作任务的收图文件夹，请保留它；可以调整选片，不会删除原图。",
                                     "production_collection_in_use")
                    session.delete(row)
            except StaleDataError as error:
                raise _error("文件夹已更新，请刷新。", "version_conflict") from error
        return dict(deleted=True, id=identifier)

    def _read_item(self, item):
        return self._read_image(item["run_id"], item["revision_id"], item["order"],
                                item["artifact_digest"], item["checksum"])

    def _read_image(self, run_id, revision_id, order, digest, checksum):
        try:
            return self.artifacts.image(run_id, revision_id, order, digest=digest, checksum=checksum)
        except ContentRunError:
            raise
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
            raise _error("图片缺失、损坏或已变化，请刷新。", "artifact_unavailable") from error

    def _image_binding(self, creator_id, identifier, run_id, revision_id):
        with self.database.session() as session:
            self._creator(session, creator_id)
            collection = self._row(session, creator_id, identifier)
            run, topic = self._run(session, creator_id, run_id)
            if collection.topic_id:
                self._topic(session, creator_id, collection.topic_id)
            if run_id not in collection.run_ids_json and run.topic_id != collection.topic_id:
                raise _error("图片不属于此素材文件夹。", "not_found", 404)
            if run_id not in collection.run_ids_json and session.scalar(select(ProductionTask.id).where(
                    ProductionTask.collection_id == identifier).limit(1)):
                task = session.get(ProductionTask, run.task_id) if run.task_id else None
                if task is None or task.collection_id != identifier:
                    raise _error("图片不属于此制作素材集。", "not_found", 404)
            revision = session.get(ContentRevision, revision_id)
            if revision is None or revision.content_run_id != run_id or not revision.artifact_digest:
                raise _error("图片版本不存在。", "not_found", 404)
            self._frozen(revision.production_input_json, creator_id, topic)
            return revision.artifact_digest

    def image(self, creator_id, identifier, run_id, revision_id, order, *, checksum):
        digest = self._image_binding(creator_id, identifier, run_id, revision_id)
        result = self._read_image(run_id, revision_id, order, digest, checksum)
        if self._image_binding(creator_id, identifier, run_id, revision_id) != digest:
            raise _error("图片版本已变化，请刷新。", "artifact_changed")
        return result

    def sort(self, creator_id, identifier, *, expected_version, snapshot_digest):
        detail = self.get(creator_id, identifier)
        self._version(detail, expected_version)
        snapshot = snapshot_digest
        self._snapshot(detail, expected_version, snapshot)
        remaining = []
        for item in detail["items"]:
            if not item["included"]:
                continue
            raw, _mime = self._read_item(item)
            with Image.open(BytesIO(raw)) as image:
                rgb = image.convert("RGB").resize((8, 8))
                mean = ImageStat.Stat(rgb).mean
                luma = list(rgb.convert("L").getdata())
                features = [value / 255 for value in mean] + [value / (255 * 8) for value in luma]
            remaining.append((item["id"], features))
        # Start at the current first image and resolve equal distances by stable ID.
        ordered = []
        if remaining:
            previous = remaining.pop(0)
            ordered.append(previous[0])
            while remaining:
                following = min(remaining, key=lambda row: (
                    sum((a - b) ** 2 for a, b in zip(previous[1], row[1])), row[0]))
                remaining.remove(following)
                ordered.append(following[0])
                previous = following
        ordered.extend(item["id"] for item in detail["items"] if not item["included"])
        with self.lock:
            self._snapshot(self.get(creator_id, identifier), expected_version, snapshot)
            return self.patch(creator_id, identifier, expected_version=expected_version, order_ids=ordered)

    def download(self, creator_id, identifier, *, version, snapshot):
        detail = self.get(creator_id, identifier)
        self._snapshot(detail, version, snapshot)
        selected = [item for item in detail["items"] if item["included"]]
        if not selected:
            raise _error("请先选中至少一张图片。", "empty_selection")
        archive = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        try:
            manifest = []
            with ZipFile(archive, "w", compression=ZIP_STORED) as bundle:
                for index, item in enumerate(selected, 1):
                    raw, mime = self._read_item(item)
                    suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}[mime]
                    filename = f"{index:04d}.{suffix}"
                    bundle.writestr(filename, raw)
                    manifest.append(dict(filename=filename, **{key: value for key, value in item.items() if key != "url"}))
                bundle.writestr("manifest.json", json.dumps(dict(collection_id=identifier,
                    name=detail["name"], version=version, snapshot_digest=snapshot,
                    selected_count=len(selected), collected_count=len(detail["items"]),
                    sources=detail["sources"], items=manifest),
                    ensure_ascii=False, indent=2).encode("utf-8"))
            self._snapshot(self.get(creator_id, identifier), version, snapshot)
            archive.seek(0)
            return archive
        except Exception:
            archive.close()
            raise
