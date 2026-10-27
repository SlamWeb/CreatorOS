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
    ContentRunStatus,
    Creator,
    CreatorPlatform,
    Database,
    OperationPolicy,
    Series,
    SkillIteration,
    Topic,
    TopicRemoval,
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
            skill_name=None,
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

    def delete_creator(self, creator_id: str) -> None:
        """删除账号；名下还有栏目时拒绝（栏目与产物链不级联删除）。"""
        with self.database.session() as session:
            creator = session.get(Creator, creator_id)
            if creator is None:
                raise StudioWriteError("账号不存在。", status_code=404)
            has_series = session.scalar(select(func.count()).select_from(Series).where(Series.creator_id == creator_id))
            if has_series:
                raise StudioWriteError("账号下还有栏目，不能删除。")
            session.delete(creator)
            session.flush()

    def edit_topic(self, topic_id: str, *, title: str | None = None, brief: str | None = None,
                   changes: dict | None = None, expected_digest=None, request_id=None,
                   origin="web", discussions=None) -> dict:
        from .topic_mutations import TopicMutationError, edit_topic
        supplied = changes if changes is not None else {
            key: value for key, value in (("title", title), ("brief", brief)) if value is not None}
        try:
            return edit_topic(self.database, topic_id, changes=supplied, expected_digest=expected_digest,
                              request_id=request_id, origin=origin, discussions=discussions)
        except TopicMutationError as error:
            raise StudioWriteError(str(error), status_code=error.status_code) from error

    def topic_edit_receipt(self, topic_id: str, request_id: str):
        from .topic_mutations import TopicMutationError, read_edit_receipt
        try:
            with self.database.session() as session:
                return read_edit_receipt(session, topic_id, request_id)
        except TopicMutationError as error:
            raise StudioWriteError(str(error), status_code=error.status_code) from error

    def delete_topic(self, topic_id: str) -> None:
        """删除选题；有生产记录或正在生产的禁止删除（产物链与历史保留）。"""
        with self.database.session() as session:
            topic = session.get(Topic, topic_id)
            if topic is None:
                raise StudioWriteError("选题不存在。", status_code=404)
            from .topic_mutations import block_reasons
            reason = block_reasons(session, [topic], []).get(topic_id)
            if reason:
                raise StudioWriteError(reason)
            if topic.status is TopicStatus.PRODUCING:
                raise StudioWriteError("选题正在生产中，不能删除。")
            has_runs = session.scalar(select(func.count()).select_from(ContentRun).where(ContentRun.topic_id == topic_id))
            has_iterations = session.scalar(select(SkillIteration.id).where(
                SkillIteration.topic_id == topic_id).limit(1)) is not None
            if has_runs or has_iterations:
                raise StudioWriteError("已有生产或校准记录的选题不能直接删除，请使用移除归档。")
            session.delete(topic)
            session.flush()

    def remove_topic(self, topic_id: str, *, request_id: str, research,
                     discussions, batch_id: str | None = None, candidate_id: str | None = None) -> dict:
        """Caller holds executor/research/discussion locks; preserve all evidence."""
        with self.database.session() as session:
            replay = session.scalar(select(TopicRemoval).where(TopicRemoval.request_id == request_id))
            if replay is not None:
                if (replay.topic_id, replay.batch_id, replay.candidate_id) != (topic_id, batch_id, candidate_id):
                    raise StudioWriteError("request_id 已用于另一项移除，请使用新的 request_id。")
                return {"id": topic_id, "series_id": replay.series_id,
                        "status": replay.status, "deduplicated": True}
            previous = session.get(TopicRemoval, topic_id)
            if previous is not None:
                if (previous.batch_id, previous.candidate_id) != (batch_id, candidate_id):
                    raise StudioWriteError("该选题的移除来源不一致，请重新读取选题。")
                return {"id": topic_id, "series_id": previous.series_id,
                        "status": previous.status, "deduplicated": True}
            topic = session.get(Topic, topic_id)
            if topic is None:
                if not batch_id or not candidate_id:
                    raise StudioWriteError("选题不存在。", status_code=404)
                try:
                    record = research._load(batch_id)
                except ValueError as error:
                    raise StudioWriteError("调研批次不存在或记录不可读。", status_code=404) from error
                if (record["status"] != "ready" or research.topic_id(batch_id, candidate_id) != topic_id
                        or not any(c["id"] == candidate_id for c in record["candidates"])):
                    raise StudioWriteError("待选建议不属于该调研批次。", status_code=404)
                series_id, status = record["series_id"], "dismissed"
            else:
                if batch_id is not None or candidate_id is not None:
                    raise StudioWriteError("已入队选题请使用正式选题移除请求。")
                related = list(session.scalars(select(ContentRun).where(ContentRun.topic_id == topic_id)))
                from .topic_mutations import block_reasons
                reason = block_reasons(session, [topic], related, discussions).get(topic_id)
                if reason:
                    raise StudioWriteError(reason)
                has_iterations = session.scalar(select(SkillIteration.id).where(
                    SkillIteration.topic_id == topic_id).limit(1)) is not None
                has_history = bool(related) or has_iterations
                series_id, status = topic.series_id, "archived" if has_history else "deleted"
                if not has_history:
                    session.delete(topic)
            series = session.get(Series, series_id)
            if series is None:
                raise StudioWriteError("栏目不存在。", status_code=404)
            if not series.is_active:
                raise StudioWriteError("栏目已移出工作区，不能移除选题。")
            session.add(TopicRemoval(topic_id=topic_id, series_id=series_id, creator_id=series.creator_id,
                                     request_id=request_id, batch_id=batch_id, candidate_id=candidate_id,
                                     status=status))
            session.flush()
            result = {"id": topic_id, "series_id": series_id, "status": status, "deduplicated": False}
            if topic is not None:
                result["title"] = topic.title
            return result

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
