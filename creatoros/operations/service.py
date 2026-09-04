from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
import json
from functools import wraps
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import StaleDataError

from creatoros.storage import (
    ContentRepository,
    Database,
    OperationEventType,
    PendingOperation,
    PendingOperationStatus,
)

from .executor import OperationConflictError, OperationExecutor
from .models import OperationPlan, OperationPreview
from .parser import OperationParseResult, OperationPlanParser, OperationParseError, validate_scope
from .repository import PendingOperationRepository


EDITABLE_STATUSES = {
    PendingOperationStatus.AWAITING_APPROVAL,
    PendingOperationStatus.NEEDS_CLARIFICATION,
    PendingOperationStatus.UNSUPPORTED,
    PendingOperationStatus.STALE,
}
CANCELLABLE_STATUSES = EDITABLE_STATUSES


class PendingOperationError(ValueError):
    """The requested approval transition is not valid."""


def guarded_write(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (StaleDataError, OperationalError) as error:
            raise PendingOperationError("计划正在被其他请求修改，请重新查看。") from error
    return wrapped


@dataclass(frozen=True)
class PreparedProposal:
    decision_status: str
    status: PendingOperationStatus
    plan_json: dict | None
    preview_json: dict | None
    confirmation_token: str | None
    message: str | None
    usage_json: dict | None


class PendingOperationService:
    def __init__(
        self,
        database: Database,
        parser: OperationPlanParser | None,
    ):
        self.database = database
        self.parser = parser
        self.content_repository = ContentRepository(database)
        self.pending_repository = PendingOperationRepository(database)
        self.executor = OperationExecutor(self.content_repository)

    def propose(
        self,
        user_request: str,
        *,
        scope_series_id: str | None = None,
    ) -> PendingOperation:
        if self.parser is None:
            raise PendingOperationError("当前未配置 OperationPlanParser。")
        validate_scope(self.content_repository, scope_series_id)
        return self.persist_proposal(
            user_request,
            self.parser.parse(user_request, series_id=scope_series_id),
            scope_series_id=scope_series_id,
        )

    def persist_proposal(
        self,
        user_request: str,
        parse_result: OperationParseResult,
        *,
        scope_series_id: str | None = None,
    ) -> PendingOperation:
        prepared = self._prepare(parse_result, scope_series_id)
        pending = PendingOperation(
            id=str(uuid4()),
            request_text=user_request.strip(),
            scope_series_id=scope_series_id,
            decision_status=prepared.decision_status,
            status=prepared.status,
            plan_json=prepared.plan_json,
            preview_json=prepared.preview_json,
            confirmation_token=prepared.confirmation_token,
            message=prepared.message,
            usage_json=prepared.usage_json,
            revision=1,
            version=1,
        )
        with self.pending_repository.transaction() as repository:
            repository.create(pending)
            repository.add_event(
                pending.id,
                OperationEventType.PROPOSED,
                {"decision_status": pending.decision_status, "revision": 1, "usage": prepared.usage_json},
            )
        return pending

    def edit(
        self,
        operation_id: str,
        edit_instruction: str,
        *,
        expected_version: int | None = None,
        expected_revision: int | None = None,
    ) -> PendingOperation:
        if self.parser is None:
            raise PendingOperationError("当前未配置 OperationPlanParser。")
        current = self._require(operation_id)
        self._check_expected_version(current, expected_version, expected_revision)
        if current.status not in EDITABLE_STATUSES:
            raise PendingOperationError("当前计划已结束，不能修改。")
        instruction = edit_instruction.strip()
        if not instruction:
            raise ValueError("edit_instruction 不能为空。")
        combined_request = (
            f"当前完整请求：{current.request_text}\n"
            f"当前完整计划：{json.dumps(current.plan_json, ensure_ascii=False)}\n"
            f"用户修改要求：{instruction}\n"
            "请输出修改后的完整最终计划，不要只输出差异。"
        )
        result = self.parser.parse(combined_request, series_id=current.scope_series_id)
        return self.persist_edit(
            operation_id,
            instruction,
            result,
            expected_revision=current.revision,
            expected_version=current.version,
        )

    @guarded_write
    def persist_edit(
        self,
        operation_id: str,
        edit_instruction: str,
        parse_result: OperationParseResult,
        *,
        expected_revision: int,
        expected_version: int | None = None,
    ) -> PendingOperation:
        current = self._require(operation_id)
        self._check_expected_version(current, expected_version, expected_revision)
        prepared = self._prepare(parse_result, current.scope_series_id)
        with self.pending_repository.transaction() as repository:
            pending = repository.get(operation_id)
            if pending is None:
                raise PendingOperationError(f"待确认计划不存在：{operation_id}")
            if pending.status not in EDITABLE_STATUSES:
                raise PendingOperationError(f"当前状态不能修改：{pending.status.value}")
            if pending.revision != expected_revision:
                raise PendingOperationError("计划已被其他修改更新，请重新查看。")
            self._check_expected_version(pending, expected_version, expected_revision)
            previous_request = pending.request_text
            pending.request_text = (
                f"{previous_request}\n修改要求（revision {pending.revision + 1}）："
                f"{edit_instruction.strip()}"
            )
            pending.decision_status = prepared.decision_status
            pending.status = prepared.status
            pending.plan_json = prepared.plan_json
            pending.preview_json = prepared.preview_json
            pending.confirmation_token = prepared.confirmation_token
            pending.message = prepared.message
            pending.usage_json = prepared.usage_json
            pending.error = None
            pending.confirmed_at = None
            pending.completed_at = None
            pending.revision += 1
            repository.add_event(
                operation_id,
                OperationEventType.EDITED,
                {
                    "edit_instruction": edit_instruction.strip(),
                    "revision": pending.revision,
                    "decision_status": pending.decision_status,
                    "usage": prepared.usage_json,
                },
            )
            return pending

    @guarded_write
    def confirm(
        self,
        operation_id: str,
        *,
        expected_version: int | None = None,
        expected_revision: int | None = None,
        confirmation_token: str | None = None,
    ) -> PendingOperation:
        current = self._require(operation_id)
        if expected_version is None or expected_revision is None or not confirmation_token:
            raise PendingOperationError("确认必须携带所见版本、修订号和确认凭证。")
        credentials = {"expected_version": expected_version, "revision": expected_revision,
                       "confirmation_token": confirmation_token}
        if current.status is PendingOperationStatus.SUCCEEDED:
            if any(event.event_type is OperationEventType.CONFIRMED and
                   event.payload_json == credentials
                   for event in self.pending_repository.list_events(operation_id)):
                return current
            raise PendingOperationError("确认凭证已过期，请重新查看计划。")
        self._check_expected_version(current, expected_version, expected_revision)
        if current.status is not PendingOperationStatus.AWAITING_APPROVAL:
            raise PendingOperationError(f"当前状态不能确认：{current.status.value}")
        if current.plan_json is None or current.confirmation_token is None:
            raise PendingOperationError("待确认计划缺少 plan 或 confirmation token。")

        try:
            with self.database.session() as session:
                pending_repository = PendingOperationRepository(
                    self.database,
                    session=session,
                )
                content_repository = ContentRepository(self.database, session=session)
                pending = pending_repository.get(operation_id)
                if pending is None:
                    raise PendingOperationError(f"待确认计划不存在：{operation_id}")
                if pending.status is not PendingOperationStatus.AWAITING_APPROVAL:
                    raise PendingOperationError(f"当前状态不能确认：{pending.status.value}")
                self._check_expected_version(pending, expected_version, expected_revision)
                plan = OperationPlan.model_validate(pending.plan_json)
                if pending.scope_series_id and any(op.series_id != pending.scope_series_id for op in plan.operations):
                    raise PendingOperationError("计划超出原栏目范围，请重新生成。")
                token = confirmation_token
                if token != pending.confirmation_token:
                    raise PendingOperationError("确认凭证已过期，请重新查看计划。")
                pending_repository.add_event(
                    operation_id,
                    OperationEventType.CONFIRMED,
                    credentials,
                )
                receipt = self.executor.execute_in_transaction(
                    content_repository,
                    plan,
                    token,
                )
                now = datetime.now(timezone.utc)
                pending.status = PendingOperationStatus.SUCCEEDED
                pending.confirmed_at = now
                pending.completed_at = now
                pending.error = None
                pending_repository.add_event(
                    operation_id,
                    OperationEventType.SUCCEEDED,
                    receipt.model_dump(mode="json"),
                )
                return pending
        except OperationConflictError as error:
            return self._mark_terminal(
                operation_id,
                PendingOperationStatus.STALE,
                OperationEventType.STALE,
                str(error),
                expected=current,
            )
        except (StaleDataError, OperationalError):
            raise
        except PendingOperationError:
            raise
        except Exception as error:
            return self._mark_terminal(
                operation_id,
                PendingOperationStatus.FAILED,
                OperationEventType.FAILED,
                str(error),
                expected=current,
            )

    @guarded_write
    def cancel(
        self,
        operation_id: str,
        *,
        expected_version: int | None = None,
        expected_revision: int | None = None,
    ) -> PendingOperation:
        with self.pending_repository.transaction() as repository:
            pending = repository.get(operation_id)
            if pending is None:
                raise PendingOperationError(f"待确认计划不存在：{operation_id}")
            self._check_expected_version(pending, expected_version, expected_revision)
            if pending.status is PendingOperationStatus.CANCELLED:
                return pending
            if pending.status not in CANCELLABLE_STATUSES:
                raise PendingOperationError(f"当前状态不能取消：{pending.status.value}")
            pending.status = PendingOperationStatus.CANCELLED
            pending.completed_at = datetime.now(timezone.utc)
            repository.add_event(
                operation_id,
                OperationEventType.CANCELLED,
                {"revision": pending.revision},
            )
            return pending

    def get(self, operation_id: str) -> PendingOperation:
        return self._require(operation_id)

    def list_actionable(self) -> tuple[PendingOperation, ...]:
        return self.pending_repository.list_actionable()

    def _require(self, operation_id: str) -> PendingOperation:
        pending = self.pending_repository.get(operation_id)
        if pending is None:
            raise PendingOperationError(f"待确认计划不存在：{operation_id}")
        return pending

    @staticmethod
    def _check_expected_version(
        pending: PendingOperation,
        expected_version: int | None,
        expected_revision: int | None,
    ) -> None:
        if expected_version is None or expected_revision is None:
            raise PendingOperationError("请提交所见 version 和 revision，再执行修改。")
        if expected_revision is not None and pending.revision != expected_revision:
            raise PendingOperationError("计划版本已变化，请刷新后重新确认。")
        if expected_version is not None and pending.version != expected_version:
            raise PendingOperationError("计划版本已变化，请刷新后重新确认。")

    def _prepare(self, parse_result: OperationParseResult, scope_series_id: str | None = None) -> PreparedProposal:
        validate_scope(self.content_repository, scope_series_id)
        decision = parse_result.decision
        usage_json = parse_result.usage.to_dict() if parse_result.usage else None
        if decision.plan and scope_series_id and any(op.series_id != scope_series_id for op in decision.plan.operations):
            return PreparedProposal("needs_clarification", PendingOperationStatus.NEEDS_CLARIFICATION,
                                    None, None, None, "要求涉及其他栏目，请新建对应栏目范围的指令。", usage_json)
        if decision.status == "ready":
            if decision.plan is None:
                raise PendingOperationError("ready 决策缺少 OperationPlan。")
            try:
                preview: OperationPreview = self.executor.preview(decision.plan)
            except ValueError as error:
                raise OperationParseError("计划包含无效目标或不完整调序，未保存可执行计划。") from error
            return PreparedProposal(
                decision_status=decision.status,
                status=PendingOperationStatus.AWAITING_APPROVAL,
                plan_json=decision.plan.model_dump(mode="json"),
                preview_json=preview.model_dump(mode="json"),
                confirmation_token=preview.confirmation_token,
                message=decision.message,
                usage_json=usage_json,
            )
        status = (
            PendingOperationStatus.NEEDS_CLARIFICATION
            if decision.status == "needs_clarification"
            else PendingOperationStatus.UNSUPPORTED
        )
        return PreparedProposal(
            decision_status=decision.status,
            status=status,
            plan_json=None,
            preview_json=None,
            confirmation_token=None,
            message=decision.message,
            usage_json=usage_json,
        )

    def _mark_terminal(
        self,
        operation_id: str,
        status: PendingOperationStatus,
        event_type: OperationEventType,
        error: str,
        *,
        expected: PendingOperation,
    ) -> PendingOperation:
        with self.pending_repository.transaction() as repository:
            pending = repository.get(operation_id)
            if pending is None:
                raise PendingOperationError(f"待确认计划不存在：{operation_id}")
            self._check_expected_version(pending, expected.version, expected.revision)
            if pending.status is not expected.status:
                raise PendingOperationError("计划状态已变化，请重新查看。")
            now = datetime.now(timezone.utc)
            pending.status = status
            pending.error = error
            pending.confirmed_at = pending.confirmed_at or now
            pending.completed_at = now if status is PendingOperationStatus.FAILED else None
            repository.add_event(
                operation_id,
                OperationEventType.CONFIRMED,
                {"revision": pending.revision},
            )
            repository.add_event(operation_id, event_type, {"error": error})
            return pending
