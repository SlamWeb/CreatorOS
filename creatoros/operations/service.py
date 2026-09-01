from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from creatoros.storage import (
    ContentRepository,
    Database,
    OperationEventType,
    PendingOperation,
    PendingOperationStatus,
)

from .executor import OperationConflictError, OperationExecutor
from .models import OperationPlan, OperationPreview
from .parser import OperationParseResult, OperationPlanParser
from .repository import PendingOperationRepository


EDITABLE_STATUSES = {
    PendingOperationStatus.AWAITING_APPROVAL,
    PendingOperationStatus.NEEDS_CLARIFICATION,
    PendingOperationStatus.UNSUPPORTED,
    PendingOperationStatus.STALE,
}
CANCELLABLE_STATUSES = EDITABLE_STATUSES - {PendingOperationStatus.UNSUPPORTED}


class PendingOperationError(ValueError):
    """The requested approval transition is not valid."""


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

    def propose(self, user_request: str) -> PendingOperation:
        if self.parser is None:
            raise PendingOperationError("当前未配置 OperationPlanParser。")
        return self.persist_proposal(user_request, self.parser.parse(user_request))

    def persist_proposal(
        self,
        user_request: str,
        parse_result: OperationParseResult,
    ) -> PendingOperation:
        prepared = self._prepare(parse_result)
        pending = PendingOperation(
            id=str(uuid4()),
            request_text=user_request.strip(),
            decision_status=prepared.decision_status,
            status=prepared.status,
            plan_json=prepared.plan_json,
            preview_json=prepared.preview_json,
            confirmation_token=prepared.confirmation_token,
            message=prepared.message,
            usage_json=prepared.usage_json,
            revision=1,
        )
        with self.pending_repository.transaction() as repository:
            repository.create(pending)
            repository.add_event(
                pending.id,
                OperationEventType.PROPOSED,
                {"decision_status": pending.decision_status, "revision": 1},
            )
        return pending

    def edit(self, operation_id: str, edit_instruction: str) -> PendingOperation:
        if self.parser is None:
            raise PendingOperationError("当前未配置 OperationPlanParser。")
        current = self._require(operation_id)
        instruction = edit_instruction.strip()
        if not instruction:
            raise ValueError("edit_instruction 不能为空。")
        combined_request = (
            f"当前完整请求：{current.request_text}\n"
            f"用户修改要求：{instruction}\n"
            "请输出修改后的完整最终计划，不要只输出差异。"
        )
        result = self.parser.parse(combined_request)
        return self.persist_edit(
            operation_id,
            instruction,
            result,
            expected_revision=current.revision,
        )

    def persist_edit(
        self,
        operation_id: str,
        edit_instruction: str,
        parse_result: OperationParseResult,
        *,
        expected_revision: int,
    ) -> PendingOperation:
        prepared = self._prepare(parse_result)
        with self.pending_repository.transaction() as repository:
            pending = repository.get(operation_id)
            if pending is None:
                raise PendingOperationError(f"待确认计划不存在：{operation_id}")
            if pending.status not in EDITABLE_STATUSES:
                raise PendingOperationError(f"当前状态不能修改：{pending.status.value}")
            if pending.revision != expected_revision:
                raise PendingOperationError("计划已被其他修改更新，请重新查看。")
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
                },
            )
            return pending

    def confirm(self, operation_id: str) -> PendingOperation:
        current = self._require(operation_id)
        if current.status is PendingOperationStatus.SUCCEEDED:
            return current
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
                plan = OperationPlan.model_validate(pending.plan_json)
                pending_repository.add_event(
                    operation_id,
                    OperationEventType.CONFIRMED,
                    {"revision": pending.revision},
                )
                receipt = self.executor.execute_in_transaction(
                    content_repository,
                    plan,
                    pending.confirmation_token,
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
            )
        except PendingOperationError:
            raise
        except Exception as error:
            return self._mark_terminal(
                operation_id,
                PendingOperationStatus.FAILED,
                OperationEventType.FAILED,
                str(error),
            )

    def cancel(self, operation_id: str) -> PendingOperation:
        with self.pending_repository.transaction() as repository:
            pending = repository.get(operation_id)
            if pending is None:
                raise PendingOperationError(f"待确认计划不存在：{operation_id}")
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

    def _prepare(self, parse_result: OperationParseResult) -> PreparedProposal:
        decision = parse_result.decision
        usage_json = parse_result.usage.to_dict() if parse_result.usage else None
        if decision.status == "ready":
            if decision.plan is None:
                raise PendingOperationError("ready 决策缺少 OperationPlan。")
            preview: OperationPreview = self.executor.preview(decision.plan)
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
    ) -> PendingOperation:
        with self.pending_repository.transaction() as repository:
            pending = repository.get(operation_id)
            if pending is None:
                raise PendingOperationError(f"待确认计划不存在：{operation_id}")
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
