"""Persistent stage hand-off: wait for exact image revisions, then queue one video.

This is a host state transition, not another model or a chat polling loop.
"""
from pathlib import Path
from io import BytesIO
import json
from zipfile import ZipFile
from sqlalchemy import select

from creatoros.storage import ContentRun, ContentRevision, ProductionTask, Topic, TopicRemoval
from creatoros.storage.models import ProductionQueueItem
from .service import ContentRunError
from .task_contracts import TaskExecutionSpec, TaskMaterial
from .task_execution import freeze_execution_spec
from .repository import ContentRunRepository


class StagedTasks:
    def __init__(self, owner, library=None, collections=None, materials=None):
        self.owner = owner
        self.database, self.runs = owner.database, owner.runs
        self.library, self.collections, self.materials = library, collections, materials
        from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
        self.catalog = library.catalog if library else ProducerSkillCatalog(skills_root_for(self.database))
        self.root = self.runs.output_root / "staged-task-inputs"

    def prepare(self, session, task, topic, item, origin_session_id, context_snapshot_ref):
        selections = item.skills
        if item.skill_ids is not None:
            if not self.library:
                raise ValueError("栏目 Skill 库未配置。")
            pool = self.library.get_series(topic.series_id)
            families = {value["family_id"] for value in pool["items"]}
            selected = []
            for skill_id in item.skill_ids:
                if (not self.library.can_use(topic.series.creator_id, skill_id)
                        or self.library._family(skill_id) not in families):
                    raise ContentRunError("所选 Skill 不在当前栏目可用池中。", code="scope_conflict", status_code=403)
                selected.append(dict(skill_id=skill_id, digest=self.library.describe(skill_id)["digest"], role="selected"))
            selections = selected
        if not selections and not item.skill_overrides and self.library:
            pool = self.library.get_series(topic.series_id)
            # No explicit stage defaults yet: keep legacy image execution intact.
            defaults = pool["defaults"][item.kind] if pool["version"] or item.kind == "video" else []
            selections = [dict(**value, digest=self.library.describe(value["skill_id"])["digest"])
                          for value in defaults]
        if not selections:
            if item.kind == "video":
                raise ValueError("请选择剪辑 Skill。")
            return False  # Old image payload remains supported.
        if item.skill_overrides:
            raise ValueError("请使用一次 Skill 选择，不要同时提交旧式绑定。")
        selected = [value.model_dump() if hasattr(value, "model_dump") else value for value in selections]
        creator_id = topic.series.creator_id
        if self.library and any(not self.library.can_use(creator_id, value["skill_id"]) for value in selected):
            raise ContentRunError("所选 Skill 不在当前账号可用库中。", code="scope_conflict", status_code=403)
        spec = TaskExecutionSpec(kind=item.kind, skills=selected)
        dependencies = self.dependencies(session, topic.id, item.depends_on)
        inputs = item.inputs.model_dump(exclude_none=True) if item.inputs else {}
        if item.kind == "video":
            if not self.materials or not self.collections:
                raise ValueError("视频素材服务未配置。")
            if not inputs:
                raise ValueError("视频需要图片素材集和 BGM。")
            detail = self.collections.get(creator_id, inputs["collection_id"])
            if detail.get("topic_id") != topic.id:
                raise ValueError("请选择本选题的素材集。")
            bgm = self.materials.resolve(creator_id, inputs["bgm_material_id"])
            spec.materials.append(TaskMaterial(id=bgm["id"], kind="audio", path=str(bgm["path"]),
                                               sha256=bgm["sha256"], name=bgm["name"]))
        elif inputs or dependencies:
            raise ValueError("当前图片任务直接生产；输入素材集和阶段依赖用于视频制作。")
        # Freeze now, even if dependencies will take hours. Editing the library
        # after this point cannot silently change an already authorized task.
        spec = freeze_execution_spec(spec, self.catalog, self.root / task.id)
        task.kind, task.dispatch_state = item.kind, "waiting"
        task.dependency_json = dependencies
        task.definition_json = dict(creator_id=creator_id, series_id=topic.series_id,
            execution_spec=spec.model_dump(mode="json"), inputs=inputs,
            origin_session_id=origin_session_id, context_snapshot_ref=context_snapshot_ref)
        return True

    @staticmethod
    def dependencies(session, topic_id, identifiers):
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("等待任务不能重复。")
        result = []
        for identifier in identifiers:
            task = session.get(ProductionTask, identifier)
            if task is None or task.topic_id != topic_id or task.kind != "image":
                raise ValueError("只能等待本选题的图片制作任务。")
            run = session.scalar(select(ContentRun).where(ContentRun.task_id == identifier)
                                 .order_by(ContentRun.created_at.desc(), ContentRun.id.desc()).limit(1))
            if not run:
                raise ValueError("等待的图片任务还没有生产记录。")
            revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run.id,
                ContentRevision.revision_number == run.active_revision_number))
            result.append(dict(task_id=identifier, run_id=run.id, revision_id=revision.id))
        return result

    def ready(self, session, task):
        for dependency in task.dependency_json:
            run = session.get(ContentRun, dependency["run_id"])
            revision = session.get(ContentRevision, dependency["revision_id"])
            latest = session.scalar(select(ContentRun.id).where(ContentRun.task_id == dependency["task_id"])
                .order_by(ContentRun.created_at.desc(), ContentRun.id.desc()).limit(1))
            if (not run or not revision or latest != run.id or run.active_revision_number != revision.revision_number):
                raise ValueError("等待的图片版本已改变，请确认后继续。")
            if run.status.value in {"failed", "interrupted", "cancelled"}:
                raise ValueError("有图片任务未完成，请修复该任务后点击继续。")
            if run.status.value not in {"awaiting_approval", "approved", "published"}:
                return False
            if not revision.artifact_digest:
                raise ValueError("图片任务没有可核验产物。")
        return True

    def dispatch(self, session, task, topic):
        definition = dict(task.definition_json)
        creator_id = definition["creator_id"]
        if (topic.series.creator_id != creator_id or topic.series_id != definition["series_id"]
                or not topic.series.is_active or not topic.series.creator.is_active
                or session.get(TopicRemoval, topic.id)):
            raise ValueError("任务归属已改变或已移除，请重新确认。")
        spec = TaskExecutionSpec.model_validate(definition["execution_spec"])
        if spec.kind == "video":
            inputs = dict(definition["inputs"])
            collection_id = inputs["collection_id"]
            if not inputs.get("export_id"):
                detail = self.collections.get(creator_id, collection_id)
                selected = {(item["run_id"], item["revision_id"]) for item in detail["items"] if item["included"]}
                required = {(dep["run_id"], dep["revision_id"]) for dep in task.dependency_json}
                if not required.issubset(selected):
                    raise ValueError("等待的图片尚未全部进入素材集，或已被取消选择；请确认后继续。")
                receipt = self.collections.export(creator_id, collection_id,
                    expected_version=detail["version"], snapshot_digest=detail["snapshot_digest"])
                inputs["export_id"] = receipt["export_id"]
            receipt = self.collections.get_export(creator_id, collection_id, inputs["export_id"])
            # Validate through the existing export store, then copy the immutable
            # archive into task inputs. Codex never needs to find or move images.
            archive = self.collections.download_export(creator_id, collection_id, inputs["export_id"])
            destination = self.root / task.id / "images.zip"
            try:
                raw = archive.read()
                with ZipFile(BytesIO(raw)) as bundle:
                    manifest = json.loads(bundle.read("manifest.json"))
                selected = {(item["run_id"], item["revision_id"]) for item in manifest["items"]}
                required = {(dep["run_id"], dep["revision_id"]) for dep in task.dependency_json}
                if not required.issubset(selected):
                    raise ValueError("固定导出未包含全部等待的图片版本，请重新选择素材导出。")
                from creatoros.integrations.atomic_file import atomic_write_bytes
                atomic_write_bytes(destination, raw)
            finally:
                archive.close()
            spec.materials = [item for item in spec.materials if item.kind != "images_zip"]
            spec.materials.append(TaskMaterial(id=receipt["export_id"], kind="images_zip",
                path=str(destination.resolve()), sha256=receipt["checksum"], name="images.zip"))
            definition["inputs"] = inputs
        definition["execution_spec"] = spec.model_dump(mode="json")
        run = self.runs._create(topic.id, task_id=task.id, bound_session=session, execution_spec=spec,
            origin_session_id=definition.get("origin_session_id"),
            context_snapshot_ref=definition.get("context_snapshot_ref"))
        revision = ContentRunRepository(self.database, session=session).get_revision_number(run.id, 1)
        session.add(ProductionQueueItem(run_id=run.id, revision_id=revision.id,
                                       expected_version=run.version, status="pending"))
        task.definition_json, task.dispatch_state, task.last_error = definition, "dispatched", None

    def advance(self):
        """Called by the existing queue worker; one blocked stage cannot block others."""
        with self.database.session() as session:
            identifiers = list(session.scalars(select(ProductionTask.id).where(ProductionTask.dispatch_state == "waiting")))
        for identifier in identifiers:
            try:
                with self.runs._write_lock, self.database.session() as session:
                    task = session.get(ProductionTask, identifier)
                    if task and task.dispatch_state == "waiting" and self.ready(session, task):
                        self.dispatch(session, task, session.get(Topic, task.topic_id))
            except Exception as error:
                with self.database.session() as session:
                    task = session.get(ProductionTask, identifier)
                    if task and task.dispatch_state == "waiting":
                        task.dispatch_state, task.last_error = "needs_attention", str(error)[:2000]
