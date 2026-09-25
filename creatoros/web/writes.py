from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from creatoros.ai import ModelUsage
from creatoros.operations import (
    OperationParseDecision,
    OperationParseResult,
    PendingOperationError,
    PendingOperationService,
    OperationPlanParser,
)
from creatoros.storage import (
    ContentRepository,
    ContentRun,
    Creator,
    CreatorPlatform,
    Database,
    OperationPolicy,
    Series,
    Topic,
    TopicStatus,
)

from .schemas import (
    CreatorCreateRequest,
    OperationEditRequest,
    OperationPreviewRequest,
    OperationProposeRequest,
    SeriesCreateRequest,
)
from creatoros.operations.parser import OperationParseError, OperationScopeError


class StudioWriteError(ValueError):
    """A user-facing write validation or conflict error."""

    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class StudioWriteService:
    """Catalog writes and host approval; only explicit propose/edit invoke the parser."""

    def __init__(self, database: Database, *, parser: OperationPlanParser | None = None):
        self.database = database
        self.pending_operations = PendingOperationService(database, parser=parser)

    def create_creator(self, request: CreatorCreateRequest) -> Creator:
        creator = Creator(
            id=f"creator-{uuid4().hex[:20]}",
            display_name=request.display_name,
            platform=CreatorPlatform.XIAOHONGSHU,
            account_handle=request.account_handle,
            daily_content_limit=request.daily_content_limit,
        )
        try:
            with self.database.session() as session:
                session.add(creator)
                session.flush()
        except IntegrityError as error:
            raise StudioWriteError("账号保存失败，请检查账号信息后重试。") from error
        return creator

    def create_series(self, creator_id: str, request: SeriesCreateRequest) -> Series:
        series = Series(
            id=f"series-{uuid4().hex[:20]}",
            creator_id=creator_id,
            name=request.name,
            description=request.description,
            audience=request.audience,
            skill_name="knowledge-to-carousel",
            selection_policy=OperationPolicy.APPROVAL,
            publish_policy=OperationPolicy.APPROVAL,
            replenish_threshold=5,
        )
        try:
            with self.database.session() as session:
                if session.get(Creator, creator_id) is None:
                    raise StudioWriteError("账号不存在，无法创建栏目。")
                session.add(series)
                session.flush()
        except IntegrityError as error:
            raise StudioWriteError("该账号下已经存在同名栏目。") from error
        return series

    def preview_topics(self, request: OperationPreviewRequest):
        parse_result = OperationParseResult(
            decision=OperationParseDecision(status="ready", plan=request.plan),
            usage=ModelUsage(0, 0, 0),
        )
        try:
            return self.pending_operations.persist_proposal(request.request_text, parse_result, scope_series_id=request.series_id)
        except (OperationParseError, OperationScopeError):
            raise
        except (PendingOperationError, ValueError) as error:
            raise StudioWriteError(str(error)) from error

    def propose(self, request: OperationProposeRequest):
        try:
            return self.pending_operations.propose(
                request.request_text,
                scope_series_id=request.series_id,
            )
        except (OperationParseError, OperationScopeError):
            raise
        except (PendingOperationError, ValueError) as error:
            raise StudioWriteError(str(error)) from error

    def confirm(self, operation_id: str, *, expected_version: int, expected_revision: int, confirmation_token: str):
        try:
            return self.pending_operations.confirm(
                operation_id,
                expected_version=expected_version,
                expected_revision=expected_revision,
                confirmation_token=confirmation_token,
            )
        except PendingOperationError as error:
            raise StudioWriteError(str(error)) from error

    def edit(self, operation_id: str, request: OperationEditRequest):
        try:
            return self.pending_operations.edit(
                operation_id,
                request.instruction,
                expected_version=request.expected_version,
                expected_revision=request.expected_revision,
            )
        except (OperationParseError, OperationScopeError):
            raise
        except (PendingOperationError, ValueError) as error:
            raise StudioWriteError(str(error)) from error

    def edit_topic(self, topic_id: str, *, title: str | None = None, brief: str | None = None) -> Topic:
        """编辑选题标题/简介；生产中禁止编辑。至少提供一个字段。"""
        if title is None and brief is None:
            raise StudioWriteError("没有需要修改的内容。")
        with self.database.session() as session:
            topic = session.get(Topic, topic_id)
            if topic is None:
                raise StudioWriteError("选题不存在。", status_code=404)
            if topic.status is TopicStatus.PRODUCING:
                raise StudioWriteError("选题正在生产中，不能编辑。")
            if title is not None:
                cleaned = title.strip()
                if not cleaned:
                    raise StudioWriteError("标题不能为空。")
                topic.title = cleaned
            if brief is not None:
                topic.brief = brief.strip() or None
            session.flush()
            return topic

    def delete_topic(self, topic_id: str) -> None:
        """删除选题；有生产记录或正在生产的禁止删除（产物链与历史保留）。"""
        with self.database.session() as session:
            topic = session.get(Topic, topic_id)
            if topic is None:
                raise StudioWriteError("选题不存在。", status_code=404)
            if topic.status is TopicStatus.PRODUCING:
                raise StudioWriteError("选题正在生产中，不能删除。")
            has_runs = session.scalar(select(func.count()).select_from(ContentRun).where(ContentRun.topic_id == topic_id))
            if has_runs:
                raise StudioWriteError("已有生产记录的选题不能删除。")
            session.delete(topic)
            session.flush()

    def reorder_topics(self, series_id: str, ordered_topic_ids: list[str]) -> None:
        """显性直写调序：完整顺序列表，一次性事务生效。"""
        try:
            ContentRepository(self.database).reorder_topics(series_id, ordered_topic_ids)
        except ValueError as error:
            raise StudioWriteError(str(error)) from error

    def cancel(self, operation_id: str, *, expected_version: int, expected_revision: int):
        try:
            return self.pending_operations.cancel(
                operation_id,
                expected_version=expected_version,
                expected_revision=expected_revision,
            )
        except PendingOperationError as error:
            raise StudioWriteError(str(error)) from error
