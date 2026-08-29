from __future__ import annotations

import json
from collections import OrderedDict
from uuid import uuid4

from ...ai.types import ToolCall
from ...context import RuntimeContext
from ...tools.results import ToolResult

MAX_SNAPSHOTS = 32
_snapshots: OrderedDict[str, dict] = OrderedDict()


def _execute_tool(
    tool_name: str,
    arguments: dict,
    context: RuntimeContext | None = None,
) -> ToolResult:
    from ...tools.execution import execute_tool_call

    return execute_tool_call(
        ToolCall(
            id=f"skill-{uuid4().hex}",
            name=tool_name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
        context=context,
    )


def _remember(payload: dict) -> str:
    snapshot_id = f"route-{uuid4().hex[:12]}"
    _snapshots[snapshot_id] = payload
    _snapshots.move_to_end(snapshot_id)
    while len(_snapshots) > MAX_SNAPSHOTS:
        _snapshots.popitem(last=False)
    return snapshot_id


def _route(
    limit: int,
    top_k: int,
    context: RuntimeContext | None = None,
) -> tuple[str, dict] | ToolResult:
    result = _execute_tool("route_hotspots", {"limit": limit, "top_k": top_k}, context)
    if result.is_error:
        return result
    try:
        payload = json.loads(result.content)
    except json.JSONDecodeError:
        return ToolResult(
            content="route_hotspots 返回的不是有效 JSON。",
            is_error=True,
            error_type="skill_protocol_error",
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("plans"), list):
        return ToolResult(
            content="route_hotspots 返回的数据缺少作者候选队列。",
            is_error=True,
            error_type="skill_protocol_error",
        )
    return _remember(payload), payload


def _find_candidate(payload: dict, author_id: str, hotspot_rank: int) -> dict | None:
    for plan in payload["plans"]:
        if plan.get("author_id") != author_id:
            continue
        for item in plan.get("hot", []):
            if item.get("rank") == hotspot_rank:
                return item
    return None


def _candidate_list(payload: dict, snapshot_id: str) -> dict:
    return {
        "status": "awaiting_selection",
        "snapshot_id": snapshot_id,
        "hotspot_count": payload.get("hotspot_count", 0),
        "author_count": payload.get("author_count", 0),
        "plans": payload["plans"],
    }


def _answer(
    payload: dict,
    snapshot_id: str,
    *,
    author_id: str,
    hotspot_rank: int,
    question: str | None,
    context: RuntimeContext | None = None,
) -> ToolResult:
    candidate = _find_candidate(payload, author_id, hotspot_rank)
    if candidate is None:
        return ToolResult(
            content="选择的作者或热点不在候选快照中。",
            is_error=True,
            error_type="invalid_candidate_selection",
        )
    prompt = question or candidate["title"]
    if not question and candidate.get("summary"):
        prompt += f"\n\n问题介绍：{candidate['summary']}"
    result = _execute_tool(
        "ask_author",
        {
            "author": author_id,
            "question": prompt,
            "query_mode": "grounded",
            "writer_prompt": "strong_identity",
            "parent_top_k": 20,
        },
        context,
    )
    if result.is_error:
        return result
    answer = {
        "status": "answer_ready",
        "snapshot_id": snapshot_id,
        "selection": {
            "author_id": author_id,
            "display_name": next(
                plan.get("display_name", author_id)
                for plan in payload["plans"]
                if plan.get("author_id") == author_id
            ),
            "hotspot_rank": hotspot_rank,
            "title": candidate["title"],
            "score": candidate.get("score"),
        },
        "answer": result.content,
        "sources": result.details.get("sources", []),
        "trace_id": result.details.get("trace_id"),
    }
    return ToolResult(
        content=json.dumps(answer, ensure_ascii=False),
        details={
            "skill": "route_and_answer",
            "status": "answer_ready",
            "snapshot_id": snapshot_id,
            "author_id": author_id,
            "hotspot_rank": hotspot_rank,
        },
    )


def _auto_candidate(payload: dict) -> tuple[str, int] | None:
    candidates = [
        (plan, item)
        for plan in payload["plans"]
        for item in plan.get("hot", [])
    ]
    if not candidates:
        return None
    plan, item = max(
        candidates,
        key=lambda pair: (
            pair[1].get("score", 0.0),
            -pair[1].get("rank", 0),
            pair[0].get("author_id", ""),
        ),
    )
    return plan["author_id"], item["rank"]


def run_route_and_answer(
    mode: str = "preview",
    limit: int = 10,
    top_k: int = 3,
    snapshot_id: str | None = None,
    author_id: str | None = None,
    hotspot_rank: int | None = None,
    question: str | None = None,
    context: RuntimeContext | None = None,
) -> ToolResult:
    if mode == "preview":
        routed = _route(limit, top_k, context)
        if isinstance(routed, ToolResult):
            return routed
        saved_id, payload = routed
        return ToolResult(
            content=json.dumps(_candidate_list(payload, saved_id), ensure_ascii=False),
            details={"skill": "route_and_answer", "status": "awaiting_selection", "snapshot_id": saved_id},
        )

    if mode == "auto":
        routed = _route(limit, top_k, context)
        if isinstance(routed, ToolResult):
            return routed
        saved_id, payload = routed
        selection = _auto_candidate(payload)
        if selection is None:
            return ToolResult(
                content="当前没有可回答的作者候选。",
                is_error=True,
                error_type="no_candidate",
            )
        return _answer(
            payload,
            saved_id,
            author_id=selection[0],
            hotspot_rank=selection[1],
            question=question,
            context=context,
        )

    if mode != "confirm":
        return ToolResult(content=f"未知 route_and_answer 模式：{mode}", is_error=True, error_type="invalid_mode")
    if (author_id is None) != (hotspot_rank is None):
        return ToolResult(
            content="confirm 必须同时提供 author_id 和 hotspot_rank。",
            is_error=True,
            error_type="incomplete_candidate_selection",
        )
    if snapshot_id is None:
        routed = _route(limit, top_k, context)
        if isinstance(routed, ToolResult):
            return routed
        snapshot_id, payload = routed
        if author_id is None:
            return ToolResult(
                content=json.dumps(_candidate_list(payload, snapshot_id), ensure_ascii=False),
                details={"skill": "route_and_answer", "status": "awaiting_selection", "snapshot_id": snapshot_id},
            )
    else:
        payload = _snapshots.get(snapshot_id)
        if payload is None:
            return ToolResult(
                content=f"候选快照不存在或已过期：{snapshot_id}",
                is_error=True,
                error_type="snapshot_not_found",
            )
    if author_id is None:
        return ToolResult(
            content=json.dumps(_candidate_list(payload, snapshot_id), ensure_ascii=False),
            details={"skill": "route_and_answer", "status": "awaiting_selection", "snapshot_id": snapshot_id},
        )
    return _answer(
        payload,
        snapshot_id,
        author_id=author_id,
        hotspot_rank=hotspot_rank,
        question=question,
        context=context,
    )
