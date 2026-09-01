from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from creatoros.ai import ModelUsage, StructuredModelProvider
from creatoros.storage import ContentRepository

from .models import OperationParseDecision, OperationPlan


OPERATION_PLAN_INSTRUCTIONS = """你是 CreatorOS 的运营指令解析器。
只把用户意图翻译成符合 JSON Schema 的解析决策，不执行任何操作。
只允许 add_topics 和 reorder_topics；用户要求范围外能力时不要擅自替代。
意图完整且可解析时返回 ready 和 plan；信息不足时返回 needs_clarification；
请求超出当前能力时返回 unsupported。后两种状态必须令 plan=null 并给出简短 message。
series_id 必须逐字复用 current_state；调序必须输出该栏目调整后的完整 Topic ID 列表。
新增 Topic 使用简短、唯一、稳定的 kebab-case topic_id，不要改写已有 Topic ID。
如果先新增再调序，后一个操作必须引用前一个操作生成的 Topic ID。
不要添加用户没有要求的选题，不要输出解释文字。"""


class OperationParseError(ValueError):
    """Model output cannot be accepted as an OperationPlan."""


@dataclass(frozen=True)
class OperationParseResult:
    decision: OperationParseDecision
    usage: ModelUsage | None

    @property
    def plan(self) -> OperationPlan | None:
        return self.decision.plan


def build_operation_catalog(repository: ContentRepository) -> dict:
    series_catalog = []
    with repository.transaction() as transaction:
        for series in transaction.list_series():
            series_catalog.append(
                {
                    "series_id": series.id,
                    "creator_id": series.creator_id,
                    "name": series.name,
                    "skill_name": series.skill_name,
                    "topics": [
                        {
                            "topic_id": topic.id,
                            "position": topic.position,
                            "title": topic.title,
                        }
                        for topic in transaction.list_topics(series.id)
                    ],
                }
            )
    return {"series": series_catalog}


def parse_operation_plan_response(content: str | None) -> OperationPlan:
    if not content or not content.strip():
        raise OperationParseError("模型没有返回 OperationPlan。")
    try:
        return OperationPlan.model_validate_json(content)
    except ValidationError as error:
        raise OperationParseError("模型返回的 JSON 不符合 OperationPlan。") from error


def parse_operation_decision_response(content: str | None) -> OperationParseDecision:
    if not content or not content.strip():
        raise OperationParseError("模型没有返回运营解析决策。")
    try:
        return OperationParseDecision.model_validate_json(content)
    except ValidationError as error:
        raise OperationParseError("模型返回的 JSON 不符合运营解析决策。") from error


class OperationPlanParser:
    def __init__(self, provider: StructuredModelProvider, repository: ContentRepository):
        self.provider = provider
        self.repository = repository

    def parse(self, user_request: str) -> OperationParseResult:
        request = user_request.strip()
        if not request:
            raise ValueError("user_request 不能为空。")
        payload = {
            "user_request": request,
            "current_state": build_operation_catalog(self.repository),
        }
        response = self.provider.complete_structured(
            instructions=OPERATION_PLAN_INSTRUCTIONS,
            input_text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            schema_name="creatoros_operation_decision",
            schema=OperationParseDecision.model_json_schema(),
        )
        return OperationParseResult(
            decision=parse_operation_decision_response(response.content),
            usage=response.usage,
        )
