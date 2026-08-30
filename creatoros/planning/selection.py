from __future__ import annotations

from typing import Any

from .models import CandidateSelector, SelectionAssignment, SelectionGroup, SelectionPlan


class SelectionExpansionError(ValueError):
    """The plan cannot be resolved against the supplied route result."""


def _route_plans(payload: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    raw_plans = payload.get("plans")
    if not isinstance(raw_plans, list):
        raise SelectionExpansionError("route_hotspots 结果缺少 plans 列表。")
    order: list[str] = []
    by_author: dict[str, dict[str, Any]] = {}
    for raw_plan in raw_plans:
        if not isinstance(raw_plan, dict) or not isinstance(raw_plan.get("author_id"), str):
            raise SelectionExpansionError("route_hotspots 包含无效作者计划。")
        author_id = raw_plan["author_id"]
        if not author_id or author_id in by_author:
            raise SelectionExpansionError(f"route_hotspots 包含空或重复作者：{author_id!r}。")
        order.append(author_id)
        by_author[author_id] = raw_plan
    return order, by_author


def _group_authors(
    group: SelectionGroup,
    author_order: list[str],
    plans: dict[str, dict[str, Any]],
) -> list[str]:
    if group.authors == "all":
        unknown = set(group.exclude_authors) - plans.keys()
        if unknown:
            raise SelectionExpansionError(f"排除名单包含未知作者：{sorted(unknown)}。")
        return [author for author in author_order if author not in group.exclude_authors]
    unknown = set(group.authors) - plans.keys()
    if unknown:
        raise SelectionExpansionError(f"选择了未知作者：{sorted(unknown)}。")
    return list(group.authors)


def _candidate_index(candidates: Any, author_id: str, queue: str) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        raise SelectionExpansionError(f"作者 {author_id} 的 {queue} 队列不是列表。")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise SelectionExpansionError(f"作者 {author_id} 的 {queue} 队列包含无效候选。")
    return candidates


def _choose(
    selector: CandidateSelector,
    candidates: list[dict[str, Any]],
    author_id: str,
) -> list[dict[str, Any]]:
    if selector.kind == "all":
        chosen = candidates
    elif selector.kind == "top_n":
        chosen = candidates[: selector.top_n]
    else:
        field = "position" if selector.kind == "positions" else "rank"
        requested = selector.positions if selector.kind == "positions" else selector.hotspot_ranks
        index = {candidate.get(field): candidate for candidate in candidates}
        missing = [value for value in requested if value not in index]
        if missing:
            raise SelectionExpansionError(
                f"作者 {author_id} 的候选中不存在 {field}={missing}。"
            )
        chosen = [index[value] for value in requested]
    if not chosen:
        raise SelectionExpansionError(f"作者 {author_id} 没有符合条件的候选。")
    return chosen


def _assignment(
    author_id: str,
    display_name: str,
    queue: str,
    candidate: dict[str, Any],
    instruction: str | None,
) -> SelectionAssignment:
    try:
        return SelectionAssignment(
            author_id=author_id,
            display_name=display_name,
            queue=queue,
            position=int(candidate["position"]),
            hotspot_rank=int(candidate["rank"]),
            title=str(candidate["title"]),
            url=str(candidate.get("url") or ""),
            summary=str(candidate.get("summary") or ""),
            score=float(candidate["score"]),
            instruction=instruction,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SelectionExpansionError(f"作者 {author_id} 的候选字段不完整。") from error


def expand_selection_plan(
    plan: SelectionPlan,
    route_payload: dict[str, Any],
) -> tuple[SelectionAssignment, ...]:
    """Resolve normalized intent into de-duplicated author-hotspot tasks."""
    author_order, plans = _route_plans(route_payload)
    assignments: list[SelectionAssignment] = []
    seen: set[tuple[str, str, int]] = set()
    for group in plan.selections:
        for author_id in _group_authors(group, author_order, plans):
            raw_plan = plans[author_id]
            candidates = _candidate_index(raw_plan.get(group.queue), author_id, group.queue)
            for candidate in _choose(group.candidates, candidates, author_id):
                assignment = _assignment(
                    author_id,
                    str(raw_plan.get("display_name") or author_id),
                    group.queue,
                    candidate,
                    plan.instruction,
                )
                key = (assignment.author_id, assignment.queue, assignment.hotspot_rank)
                if key not in seen:
                    seen.add(key)
                    assignments.append(assignment)
    if not assignments:
        raise SelectionExpansionError("选择计划没有展开出任何任务。")
    return tuple(assignments)
