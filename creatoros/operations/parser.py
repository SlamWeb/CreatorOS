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
series_id 必须逐字复用 current_state；有 scope 时只能操作 scope 内的栏目；调序必须输出该栏目调整后的完整 Topic ID 列表。
新增 Topic 使用简短、唯一、稳定的 kebab-case topic_id，不要改写已有 Topic ID。
如果先新增再调序，后一个操作必须引用前一个操作生成的 Topic ID。
没有 scope 且多个栏目可能匹配时，必须返回 needs_clarification，要求用户明确账号和栏目；不要猜测。
用户明确要求发布、生产、删除或其他未支持动作时，必须返回 unsupported；不要把其中一部分改写成 add_topics。
不要添加用户没有要求的选题，不要输出解释文字。"""


class OperationParseError(ValueError):
    """Model output cannot be accepted as an OperationPlan."""


class OperationParserUnavailable(RuntimeError):
    pass


class OperationScopeError(ValueError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def validate_scope(repository: ContentRepository, series_id: str | None):
    if series_id is None:
        return
    series = repository.get_series(series_id)
    if series is None:
        raise OperationScopeError("栏目不存在。", 404)
    creator = repository.get_creator(series.creator_id)
    if not series.is_active or creator is None or not creator.is_active:
        raise OperationScopeError("栏目或所属账号已停用。", 409)


class LazyOperationParser:
    """Only explicit parse requests initialize an external client; each owns its client."""
    def __init__(self, factory):
        self.factory = factory

    def parse(self, *args, **kwargs):
        return self.factory().parse(*args, **kwargs)


@dataclass(frozen=True)
class OperationParseResult:
    decision: OperationParseDecision
    usage: ModelUsage | None

    @property
    def plan(self) -> OperationPlan | None:
        return self.decision.plan


def build_operation_catalog(repository: ContentRepository, series_id: str | None = None) -> dict:
    series_catalog = []
    with repository.transaction() as transaction:
        for series in transaction.list_series():
            creator = transaction.get_creator(series.creator_id)
            if series_id is not None and series.id != series_id:
                series_catalog.append(
                    {
                        "series_id": series.id,
                        "creator_name": creator.display_name if creator else None,
                        "name": series.name,
                        "is_active": series.is_active,
                    }
                )
                continue
            series_catalog.append(
                {
                    "series_id": series.id,
                    "creator_id": series.creator_id,
                    "creator_name": creator.display_name if creator else None,
                    "name": series.name,
                    "is_active": series.is_active,
                    "creator_active": creator.is_active if creator else False,
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

    def parse(self, user_request: str, *, series_id: str | None = None) -> OperationParseResult:
        request = user_request.strip()
        if not request:
            raise ValueError("user_request 不能为空。")
        validate_scope(self.repository, series_id)
        payload = {
            "user_request": request,
            "scope": {"series_id": series_id},
            "current_state": build_operation_catalog(self.repository, series_id),
        }
        try:
            response = self.provider.complete_structured(
                instructions=OPERATION_PLAN_INSTRUCTIONS,
                input_text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                schema_name="creatoros_operation_decision",
                schema=OperationParseDecision.model_json_schema(),
            )
        except Exception as error:
            raise OperationParserUnavailable("模型暂不可用或请求超时；未执行任何计划，请稍后手动重试。") from error
        return OperationParseResult(
            decision=parse_operation_decision_response(response.content),
            usage=response.usage,
        )
