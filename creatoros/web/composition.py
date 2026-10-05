"""组合栏目写服务：创建/改组合/分配账号的唯一写入点。

规则（P2，用户已确认）：
- 栏目只能是 legacy 单 Skill 或完整 mind+production pair，禁止半套与全空。
- legacy 栏目不转换为 pair；pair 组合可以保存为未验证，生产门禁在 Run 创建时。
- 所有写操作带 request_id 幂等：重复提交返回首次结果，不生成第二条记录。
- 配置修改使用所见 revision 做 CAS，冲突返回 409 且零写入。
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.storage import (
    ContentRun,
    ContentRunStatus,
    Creator,
    Database,
    OperationPolicy,
    Series,
    Topic,
    WriteReceipt,
)


class CompositionError(ValueError):
    """组合写服务校验/冲突错误，携带 HTTP 状态与稳定错误码。"""

    def __init__(self, message: str, *, status_code: int = 422, code: str = "invalid_request"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class SeriesCompositionService:
    def __init__(self, database: Database, catalog: ProducerSkillCatalog):
        self.database = database
        self.catalog = catalog

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
    ) -> tuple[str, bool]:
        """返回 (series_id, deduplicated)。"""

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
            else:
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
            return series

        return self._run_idempotent("create_series", request_id, origin, write)

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
            self._check_revision(series, expected_revision)
            if creator_id is not None:
                creator = session.get(Creator, creator_id)
                if creator is None:
                    raise CompositionError("账号不存在。", code="not_found", status_code=404)
                if not creator.is_active:
                    raise CompositionError("账号已停用，无法归属栏目。", status_code=409, code="creator_inactive")
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
        research,
    ) -> tuple[str, str, bool, str | None]:
        """Delete a truly unused series, otherwise archive it and keep its history.

        Callers hold the run service and research locks while invoking this method,
        so an execution or research submission cannot start between the activity
        check and the database commit.
        """
        operation = "delete_series"
        try:
            with self.database.session() as session:
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

                active_statuses = (ContentRunStatus.PRODUCING, ContentRunStatus.VALIDATING)
                active_run = session.scalar(
                    select(ContentRun.id)
                    .join(Topic, ContentRun.topic_id == Topic.id)
                    .where(Topic.series_id == series_id, ContentRun.status.in_(active_statuses))
                    .limit(1)
                )
                if active_run is not None or research.has_active_for_series(series_id):
                    raise CompositionError(
                        "栏目仍有生产或调研任务进行中；请等待任务结束后重新操作。",
                        status_code=409, code="series_active",
                    )

                has_topics = session.scalar(
                    select(Topic.id).where(Topic.series_id == series_id).limit(1)
                ) is not None
                has_runs = session.scalar(
                    select(ContentRun.id)
                    .join(Topic, ContentRun.topic_id == Topic.id)
                    .where(Topic.series_id == series_id)
                    .limit(1)
                ) is not None
                has_research = research.has_history_for_series(series_id)
                creator_id = series.creator_id
                if has_topics or has_runs or has_research:
                    series.is_active = False
                    status = "archived"
                else:
                    session.delete(series)
                    status = "deleted"

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

    # ---- internals ----

    def _run_idempotent(self, operation: str, request_id: str, origin: str, write, resource_id: str | None = None):
        try:
            with self.database.session() as session:
                existing = session.get(WriteReceipt, request_id)
                if existing is not None:
                    if existing.operation != operation:
                        raise CompositionError(
                            "request_id 已被其他操作占用，请为每个新操作生成新的 request_id。",
                            status_code=409, code="request_id_reused",
                        )
                    return existing.response_json["series_id"], True
                series = write(session)
                session.add(WriteReceipt(
                    request_id=request_id,
                    operation=operation,
                    resource_id=resource_id or series.id,
                    origin=origin,
                    response_json={"series_id": series.id},
                ))
                session.flush()
                return series.id, False
        except IntegrityError as error:
            # 并发下同一 request_id 的竞态：重读回执；否则是栏目同名等业务约束。
            with self.database.session() as session:
                existing = session.get(WriteReceipt, request_id)
                if existing is not None and existing.operation == operation:
                    return existing.response_json["series_id"], True
            raise CompositionError("同名栏目已存在或请求违反数据约束，未写入。", status_code=409, code="conflict") from error

    @staticmethod
    def _load_series(session, series_id: str) -> Series:
        series = session.get(Series, series_id)
        if series is None:
            raise CompositionError("栏目不存在。", code="not_found", status_code=404)
        return series

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
            return self.catalog.describe(skill_id)
        except ValueError as error:
            raise CompositionError(f"{label}不可用：{error}") from error
