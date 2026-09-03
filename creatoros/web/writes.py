from __future__ import annotations

from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from creatoros.ai import ModelUsage
from creatoros.operations import (
    OperationParseDecision,
    OperationParseResult,
    PendingOperationError,
    PendingOperationService,
)
from creatoros.storage import (
    Creator,
    CreatorPlatform,
    Database,
    OperationPolicy,
    Series,
)

from .schemas import CreatorCreateRequest, OperationEditRequest, OperationPreviewRequest, SeriesCreateRequest


class StudioWriteError(ValueError):
    """A user-facing write validation or conflict error."""


class StudioWriteService:
    """Owns the small S3 write surface; no LLM or production calls happen here."""

    def __init__(self, database: Database):
        self.database = database
        self.pending_operations = PendingOperationService(database, parser=None)

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
            return self.pending_operations.persist_proposal(request.request_text, parse_result)
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
        except (PendingOperationError, ValueError) as error:
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
