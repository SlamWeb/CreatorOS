from __future__ import annotations

import hashlib
import json

from creatoros.storage import ContentRepository, TopicSource

from .models import (
    AddTopicsOperation,
    OperationChange,
    OperationPlan,
    OperationPreview,
    OperationReceipt,
    ReorderTopicsOperation,
)


class OperationPlanError(ValueError):
    """A valid schema cannot be resolved against current business state."""


class OperationConflictError(OperationPlanError):
    """The database changed after the plan was previewed."""


class OperationExecutor:
    def __init__(self, repository: ContentRepository):
        self.repository = repository

    def preview(self, plan: OperationPlan) -> OperationPreview:
        with self.repository.transaction() as transaction:
            return self._build_preview(transaction, plan)

    def execute(self, plan: OperationPlan, confirmation_token: str) -> OperationReceipt:
        with self.repository.transaction() as transaction:
            return self.execute_in_transaction(transaction, plan, confirmation_token)

    def execute_in_transaction(
        self,
        repository: ContentRepository,
        plan: OperationPlan,
        confirmation_token: str,
    ) -> OperationReceipt:
        preview = self._build_preview(repository, plan)
        if preview.confirmation_token != confirmation_token:
            raise OperationConflictError("数据库状态已变化，请重新预览后再确认。")
        for operation in plan.operations:
            if isinstance(operation, AddTopicsOperation):
                for topic in operation.topics:
                    repository.add_topic(
                        topic_id=topic.topic_id,
                        series_id=operation.series_id,
                        title=topic.title,
                        brief=topic.brief,
                        source=TopicSource(topic.source),
                    )
            else:
                repository.reorder_topics(
                    operation.series_id,
                    operation.ordered_topic_ids,
                )
        affected_series = dict.fromkeys(
            operation.series_id for operation in plan.operations
        )
        return OperationReceipt(
            applied_operations=len(plan.operations),
            topic_orders={
                series_id: [topic.id for topic in repository.list_topics(series_id)]
                for series_id in affected_series
            },
        )

    def _build_preview(
        self,
        repository: ContentRepository,
        plan: OperationPlan,
    ) -> OperationPreview:
        series_ids = list(dict.fromkeys(operation.series_id for operation in plan.operations))
        orders: dict[str, list[str]] = {}
        for series_id in series_ids:
            if repository.get_series(series_id) is None:
                raise OperationPlanError(f"栏目不存在：{series_id}")
            orders[series_id] = [topic.id for topic in repository.list_topics(series_id)]
        initial_orders = {series_id: list(order) for series_id, order in orders.items()}

        changes: list[OperationChange] = []
        for operation in plan.operations:
            before = list(orders[operation.series_id])
            if isinstance(operation, AddTopicsOperation):
                for topic in operation.topics:
                    if repository.get_topic(topic.topic_id) is not None:
                        raise OperationPlanError(f"Topic ID 已存在：{topic.topic_id}")
                    orders[operation.series_id].append(topic.topic_id)
            elif isinstance(operation, ReorderTopicsOperation):
                if set(operation.ordered_topic_ids) != set(before):
                    raise OperationPlanError(
                        f"栏目 {operation.series_id} 的调序必须包含当前全部且仅包含自身 Topic。"
                    )
                orders[operation.series_id] = list(operation.ordered_topic_ids)
            changes.append(
                OperationChange(
                    action=operation.action,
                    series_id=operation.series_id,
                    before_order=before,
                    after_order=list(orders[operation.series_id]),
                )
            )

        token_payload = {
            "plan": plan.model_dump(mode="json"),
            "initial_orders": initial_orders,
        }
        canonical = json.dumps(
            token_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return OperationPreview(
            confirmation_token=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            changes=changes,
        )
