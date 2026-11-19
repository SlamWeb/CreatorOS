"""组合栏目写服务：创建/改组合/分配账号的唯一写入点。

规则（P2，用户已确认）：
- 新栏目可在创建时配置常用池；旧单 Skill 与完整 pair 保持兼容，禁止半套。
- legacy 栏目不转换为 pair；pair 组合可以保存为未验证，生产门禁在 Run 创建时。
- 所有写操作带 request_id 幂等：重复提交返回首次结果，不生成第二条记录。
- 配置修改使用所见 revision 做 CAS，冲突返回 409 且零写入。
"""
from __future__ import annotations

from uuid import uuid4
from datetime import datetime, timezone
from contextlib import nullcontext

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from creatoros.storage.models import ProductionTask, ProductionTaskHistory, ProductionQueueItem, ProductionQueueHistory

from creatoros.integrations.producer_skills import ProducerSkillCatalog, _SKILL_EDIT_LOCK
from creatoros.storage import (
    ContentRun,
    ContentRunStatus,
    Creator,
    Database,
    OperationPolicy,
    Series,
    SkillCalibration,
    Topic,
    TopicRemoval,
    WriteReceipt,
)


class CompositionError(ValueError):
    """组合写服务校验/冲突错误，携带 HTTP 状态与稳定错误码。"""

    def __init__(self, message: str, *, status_code: int = 422, code: str = "invalid_request"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class SeriesCompositionService:
    def __init__(self, database: Database, catalog: ProducerSkillCatalog, *, skill_library=None):
        self.database = database
        self.catalog = catalog
        self.skill_library = skill_library

    def create_series(
        self,
        *,
        name: str,
        description: str,
        audience: str,
        creator_id: str | None,
        skill_name: str | None,
        mind_skill_id: str | None,
        production_skill_id: str | None,
        request_id: str,
        origin: str,
        skill_ids: list[str] | None = None,
    ) -> tuple[str, bool]:
        """返回 (series_id, deduplicated)。"""

        if skill_ids and (not creator_id or any((skill_name, mind_skill_id, production_skill_id))):
            raise CompositionError("请选择归属账号；栏目 Skill 池不能和旧绑定同时提交。")
        pool_request = ({"creator_id": creator_id, "name": name, "description": description,
                         "audience": audience, "skill_ids": skill_ids} if skill_ids else None)

        def write(session) -> Series:
            mind_binding, production_binding = mind_skill_id, production_skill_id
            if creator_id is not None:
                creator = session.get(Creator, creator_id)
                if creator is None:
                    raise CompositionError("账号不存在，无法归属栏目。", code="not_found", status_code=404)
                if not creator.is_active:
                    raise CompositionError("账号已停用，无法归属栏目。", status_code=409, code="creator_inactive")
            if skill_name is not None:
                # legacy 绑定沿用生产门禁：必须可解析且满足当前产物契约。
                legacy_binding = self._resolve_legacy(skill_name)
            elif mind_skill_id is not None or production_skill_id is not None:
                mind_binding, production_binding = self._check_pair(mind_skill_id, production_skill_id)
            series = Series(
                id=f"series-{uuid4().hex[:20]}",
                creator_id=creator_id,
                name=name,
                description=description,
                audience=audience,
                skill_name=legacy_binding if skill_name is not None else None,
                mind_skill_id=mind_binding,
                production_skill_id=production_binding,
                selection_policy=OperationPolicy.APPROVAL,
                publish_policy=OperationPolicy.APPROVAL,
                replenish_threshold=5,
            )
            session.add(series)
            session.flush()
            if skill_ids:
                if self.skill_library is None:
                    raise CompositionError("账号 Skill 库未配置。", status_code=503)
                try:
                    self.skill_library.initialize_series_pool(session, series, skill_ids)
                except PermissionError as error:
                    raise CompositionError(str(error), status_code=403, code="skill_scope_denied") from error
                except (ValueError, OSError, KeyError) as error:
                    raise CompositionError(str(error)) from error
            return series

        with (_SKILL_EDIT_LOCK if skill_ids else nullcontext()):
            return self._run_idempotent("create_series", request_id, origin, write, pool_request=pool_request)

    def update_composition(
        self,
        series_id: str,
        *,
        mind_skill_id: str,
        production_skill_id: str,
        expected_revision: int,
        request_id: str,
        origin: str,
    ) -> tuple[str, bool]:
        def write(session) -> Series:
            series = self._load_series(session, series_id)
            self._require_active(series)
            if series.skill_name is not None:
                raise CompositionError(
                    "旧单 Skill 栏目不转换为组合；请新建组合栏目。",
                    status_code=409, code="legacy_series",
                )
            self._check_revision(series, expected_revision)
            series.mind_skill_id, series.production_skill_id = self._check_pair(mind_skill_id, production_skill_id)
            session.flush()
            return series

        return self._run_idempotent("update_composition", request_id, origin, write, resource_id=series_id)

    def assign_series(
        self,
        series_id: str,
        *,
        creator_id: str | None,
        expected_revision: int,
        request_id: str,
        origin: str,
    ) -> tuple[str, bool]:
        def write(session) -> Series:
            series = self._load_series(session, series_id)
            self._require_active(series)
            self._check_revision(series, expected_revision)
            if creator_id is not None:
                creator = session.get(Creator, creator_id)
                if creator is None:
                    raise CompositionError("账号不存在。", code="not_found", status_code=404)
                if not creator.is_active:
                    raise CompositionError("账号已停用，无法归属栏目。", status_code=409, code="creator_inactive")
            if series.creator_id != creator_id:
                now = datetime.now(timezone.utc)
                message = "栏目归属已变化，请核对后明确恢复。"
                items = session.scalars(select(ProductionQueueItem).join(ContentRun).join(Topic).where(
                    Topic.series_id == series_id, ProductionQueueItem.status == "pending"))
                for item in items:
                    if item.last_error is not None:
                        session.add(ProductionQueueHistory(queue_item_id=item.id, expected_version=item.expected_version,
                            status=item.status, submitted_at=item.submitted_at, started_at=item.started_at,
                            completed_at=item.completed_at, last_error=item.last_error))
                    item.status, item.last_error, item.completed_at = "needs_attention", message, now
                tasks = session.scalars(select(ProductionTask).join(Topic).where(
                    Topic.series_id == series_id, ProductionTask.dispatch_state == "waiting"))
                for task in tasks:
                    session.add(ProductionTaskHistory(task_id=task.id, status=task.dispatch_state,
                        last_error=task.last_error, recorded_at=now, dependencies=task.dependency_json))
                    task.dispatch_state, task.last_error = "needs_attention", message
            series.creator_id = creator_id
            session.flush()
            return series

        return self._run_idempotent("assign_series", request_id, origin, write, resource_id=series_id)

    def delete_series(
        self,
        series_id: str,
        *,
        expected_revision: int,
        request_id: str,
        origin: str,
        lifecycle,
        expected_status=None,
    ) -> tuple[str, str, bool, str | None]:
        """Delete a truly unused series, otherwise archive it and keep its history.

        Callers hold the run service and research locks while invoking this method,
        so an execution or research submission cannot start between the activity
        check and the database commit.
        """
        operation = "delete_series"
        try:
            with self.database.session() as session:
                if self.database.engine.dialect.name == 'sqlite':
                    session.execute(text('BEGIN IMMEDIATE'))
                existing = session.get(WriteReceipt, request_id)
                if existing is not None:
                    if existing.operation != operation:
                        raise CompositionError(
                            "request_id 已被其他操作占用，请为每个新操作生成新的 request_id。",
                            status_code=409, code="request_id_reused",
                        )
                    payload = existing.response_json
                    if existing.resource_id != series_id:
                        raise CompositionError(
                            "request_id 已用于删除其他栏目，请为每个栏目生成独立 request_id。",
                            status_code=409, code="request_id_reused",
                        )
                    return payload["series_id"], payload["status"], True, payload.get("creator_id")

                series = self._load_series(session, series_id)
                if not series.is_active:
                    raise CompositionError("栏目已归档。", status_code=409, code="series_inactive")
                self._check_revision(series, expected_revision)

                assessment = lifecycle.assess("series", series_id, session)
                if assessment["status"] == "blocked":
                    raise CompositionError("栏目仍有进行中或已授权排队任务，请等待结束后重试。",
                                           status_code=409, code="series_active")
                if expected_status is not None and assessment["status"] != expected_status:
                    raise CompositionError("栏目保留状态已变化，请重新查看后确认。", status_code=409, code="removal_changed")
                creator_id = series.creator_id
                status = assessment["status"]
                if status == "archived":
                    series.is_active = False
                else:
                    session.delete(series)

                response = {"series_id": series_id, "status": status, "creator_id": creator_id}
                session.add(WriteReceipt(
                    request_id=request_id,
                    operation=operation,
                    resource_id=series_id,
                    origin=origin,
                    response_json=response,
                ))
                session.flush()
                return series_id, status, False, creator_id
        except IntegrityError as error:
            with self.database.session() as session:
                existing = session.get(WriteReceipt, request_id)
                if existing is not None and existing.operation == operation:
                    payload = existing.response_json
                    if existing.resource_id != series_id:
                        raise CompositionError(
                            "request_id 已用于删除其他栏目，请为每个栏目生成独立 request_id。",
                            status_code=409, code="request_id_reused",
                        )
                    return payload["series_id"], payload["status"], True, payload.get("creator_id")
            raise CompositionError("删除栏目时发生数据冲突，请重新读取后操作。", status_code=409, code="conflict") from error
        except StaleDataError as error:
            raise CompositionError("栏目配置已变化，请按最新状态重新提交。", status_code=409, code="revision_conflict") from error

    def rename_series(self, series_id: str, *, name: str, expected_revision: int,
                      request_id: str, origin: str) -> dict:
        """Rename current metadata, keeping historical inputs and a frozen receipt."""
        from .queries import StudioQueryService, _visible_topic

        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise CompositionError("栏目名称须为 1–120 字符。")
        payload = {"name": name.strip(), "expected_revision": expected_revision}

        def replay(receipt, creator_id):
            stored = receipt.response_json
            if (receipt.operation != "rename_series" or receipt.resource_id != series_id
                    or stored.get("request_payload") != payload or stored.get("creator_id") != creator_id):
                raise CompositionError("请求标识已用于不同的栏目或修改内容。",
                                       status_code=409, code="request_id_reused")
            return {**stored["response"], "deduplicated": True}

        try:
            with self.database.session() as session:
                series = self._load_series(session, series_id)
                if not series.is_active or (series.creator_id and (series.creator is None or not series.creator.is_active)):
                    raise CompositionError("栏目已移出工作区。", status_code=404, code="not_found")
                existing = session.get(WriteReceipt, request_id)
                if existing is not None:
                    return replay(existing, series.creator_id)
                self._check_revision(series, expected_revision)
                series.name = payload["name"]
                session.flush()
                topics = list(session.scalars(select(Topic).where(
                    Topic.series_id == series_id, _visible_topic())))
                runs = list(session.scalars(select(ContentRun).where(
                    ContentRun.topic_id.in_([topic.id for topic in topics])))) if topics else []
                view = StudioQueryService._series_view(series, topics, runs).model_dump(mode="json")
                response = {"request_id": request_id, "deduplicated": False, "series": view}
                session.add(WriteReceipt(request_id=request_id, operation="rename_series", resource_id=series_id,
                    origin=origin, response_json={"creator_id": series.creator_id,
                        "request_payload": payload, "response": response}))
                session.flush()
                return response
        except IntegrityError as error:
            with self.database.session() as session:
                series = session.get(Series, series_id)
                existing = session.get(WriteReceipt, request_id)
                if series is not None and existing is not None:
                    return replay(existing, series.creator_id)
            raise CompositionError("同名栏目已存在或请求违反数据约束，未写入。",
                                   status_code=409, code="conflict") from error
        except StaleDataError as error:
            raise CompositionError("栏目配置已变化，请按最新状态重新提交。",
                                   status_code=409, code="revision_conflict") from error

    # ---- internals ----

    def _run_idempotent(self, operation: str, request_id: str, origin: str, write,
                        resource_id: str | None = None, *, pool_request: dict | None = None):
        def replay(receipt):
            if receipt.operation != operation or receipt.response_json.get("pool_request") != pool_request:
                raise CompositionError("request_id 已用于不同操作或创建内容。",
                                       status_code=409, code="request_id_reused")
            return receipt.response_json["series_id"], True

        try:
            with self.database.session() as session:
                if pool_request is not None and self.database.engine.dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                existing = session.get(WriteReceipt, request_id)
                if existing is not None:
                    return replay(existing)
                series = write(session)
                session.add(WriteReceipt(
                    request_id=request_id,
                    operation=operation,
                    resource_id=resource_id or series.id,
                    origin=origin,
                    response_json={"series_id": series.id, **({"pool_request": pool_request} if pool_request else {})},
                ))
                session.flush()
                return series.id, False
        except IntegrityError as error:
            # 并发下同一 request_id 的竞态：重读回执；否则是栏目同名等业务约束。
            with self.database.session() as session:
                existing = session.get(WriteReceipt, request_id)
                if existing is not None:
                    return replay(existing)
            raise CompositionError("同名栏目已存在或请求违反数据约束，未写入。", status_code=409, code="conflict") from error

    @staticmethod
    def _load_series(session, series_id: str) -> Series:
        series = session.get(Series, series_id)
        if series is None:
            raise CompositionError("栏目不存在。", code="not_found", status_code=404)
        return series

    @staticmethod
    def _require_active(series: Series) -> None:
        if not series.is_active or (series.creator_id and (series.creator is None or not series.creator.is_active)):
            raise CompositionError("栏目或所属账号已移出工作区。", status_code=409, code="inactive")

    @staticmethod
    def _check_revision(series: Series, expected_revision: int) -> None:
        if series.revision != expected_revision:
            raise CompositionError(
                f"栏目配置已变化（当前 revision {series.revision}），请按最新状态重新提交。",
                status_code=409, code="revision_conflict",
            )

    def _resolve_legacy(self, skill_name: str) -> str:
        try:
            self.catalog.resolve(skill_name)
            return skill_name if skill_name == self.catalog.BUILTIN_ID else self.catalog.describe(skill_name)["id"]
        except ValueError as error:
            raise CompositionError(str(error)) from error

    def _check_pair(self, mind_skill_id: str | None, production_skill_id: str | None) -> tuple[str, str]:
        if not mind_skill_id or not production_skill_id:
            raise CompositionError("组合栏目必须同时选择内容 Skill 与制作 Skill。")
        bindings = []
        for skill_id, expected_role, label in (
            (mind_skill_id, "mind", "内容 Skill"),
            (production_skill_id, "production", "制作 Skill"),
        ):
            record = self._catalog_record(skill_id, label)
            if record["role"] != expected_role:
                raise CompositionError(f"{label} 的角色是 {record['role'] or '未分类'}，不能放在该槽位。")
            bindings.append(record["id"])
        return bindings[0], bindings[1]

    def _catalog_record(self, skill_id: str, label: str) -> dict:
        if skill_id == ProducerSkillCatalog.BUILTIN_ID:
            raise CompositionError(f"内置端到端 Skill 不能作为{label}参与组合。")
        try:
            record = self.catalog.describe(skill_id)
        except ValueError as error:
            raise CompositionError(f"{label}不可用：{error}") from error
        if record.get("version_status") == "candidate":
            raise CompositionError(
                f"{label}是修改候选；请先试用并确认版本，不能直接绑定栏目。",
                status_code=409, code="skill_candidate_unpublished",
            )
        return record
