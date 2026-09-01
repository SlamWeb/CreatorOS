from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OperationModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class TopicDraft(OperationModel):
    topic_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    brief: str | None = None
    source: Literal["research", "manual"] = "manual"


class AddTopicsOperation(OperationModel):
    action: Literal["add_topics"] = "add_topics"
    series_id: str = Field(min_length=1, max_length=80)
    topics: list[TopicDraft] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_topics(self) -> "AddTopicsOperation":
        topic_ids = [topic.topic_id for topic in self.topics]
        if len(topic_ids) != len(set(topic_ids)):
            raise ValueError("同一次 add_topics 中的 topic_id 不能重复。")
        return self


class ReorderTopicsOperation(OperationModel):
    action: Literal["reorder_topics"] = "reorder_topics"
    series_id: str = Field(min_length=1, max_length=80)
    ordered_topic_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_topics(self) -> "ReorderTopicsOperation":
        if any(not topic_id for topic_id in self.ordered_topic_ids):
            raise ValueError("ordered_topic_ids 不能包含空 ID。")
        if len(self.ordered_topic_ids) != len(set(self.ordered_topic_ids)):
            raise ValueError("ordered_topic_ids 不能重复。")
        return self


Operation = Annotated[
    AddTopicsOperation | ReorderTopicsOperation,
    Field(discriminator="action"),
]


class OperationPlan(OperationModel):
    schema_version: Literal[1] = 1
    operations: list[Operation] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_new_topic_ids(self) -> "OperationPlan":
        topic_ids = [
            topic.topic_id
            for operation in self.operations
            if isinstance(operation, AddTopicsOperation)
            for topic in operation.topics
        ]
        if len(topic_ids) != len(set(topic_ids)):
            raise ValueError("同一计划中新建的 topic_id 不能重复。")
        return self


class OperationChange(OperationModel):
    action: Literal["add_topics", "reorder_topics"]
    series_id: str
    before_order: list[str]
    after_order: list[str]


class OperationPreview(OperationModel):
    confirmation_token: str
    changes: list[OperationChange]


class OperationReceipt(OperationModel):
    status: Literal["applied"] = "applied"
    applied_operations: int
    topic_orders: dict[str, list[str]]
