"""Account-scoped Skill operations for the local Web, separate from global library management.

The creator selector is a local correctness boundary, not user authentication.
Agent requests continue to use their trusted session scope instead.
"""
from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool


_SKILL = re.compile(r"^/api/producer-skills/(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})(?:/(versions|content|files(?:/(?:content|text))?|assets|publish-version))?$")
_SWITCH = re.compile(r"^/api/series/([^/]+)/skill-version$")


def has_web_skill_scope(request: Request) -> bool:
    return (not request.headers.get("x-creatoros-agent-session") and bool(request.query_params.get("creator_id"))
            and bool(_SKILL.fullmatch(request.url.path) or _SWITCH.fullmatch(request.url.path)))


async def check_web_skill_scope(request: Request, guard):
    if not has_web_skill_scope(request):
        return None
    creator_id = request.query_params["creator_id"]
    if not guard._active_creator(creator_id):
        return guard._skill_denied()
    method = request.method.upper()
    match = _SKILL.fullmatch(request.url.path)
    if match:
        skill_id, action = match.groups()
        if action == "versions" and method == "GET":
            if not guard._can_read_skill(skill_id, creator_id):
                return guard._skill_denied()
            try:
                if skill_id == guard.research.catalog.BUILTIN_ID:
                    # The built-in projection currently scans the catalog too.
                    item = await run_in_threadpool(guard._builtin_version)
                    result = {"family_id": skill_id, "items": [item], "bindings": []}
                else:
                    # Version history validates every edition and may read many
                    # assets. Keep the request's scope lock, but do not block
                    # the event loop while those synchronous checks run.
                    result = await run_in_threadpool(request.app.state.skill_versions.history, skill_id)
            except (ValueError, OSError, KeyError):
                return JSONResponse(status_code=404, content={"error": {
                    "code": "not_found", "message": "Skill 版本不可用或未登记。"}})
            result["items"] = [item for item in result.get("items", []) if guard._can_read_skill(item["id"], creator_id)]
            result["bindings"] = [item for item in result.get("bindings", []) if guard._owns_series(item["series_id"], creator_id)]
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        if method == "GET" and action in {"content", "files", "files/content", "files/text"}:
            allowed = guard._can_read_skill(skill_id, creator_id)
        elif (action == "versions" and method == "POST") or (action == "files/content" and method == "PUT") or (action == "assets" and method in {"PUT", "DELETE"}):
            allowed = guard._can_fork_or_edit_skill(skill_id, creator_id)
        elif action == "publish-version" and method == "POST":
            allowed = guard._can_edit_skill(skill_id, creator_id) and guard._owns_skill(skill_id, creator_id)
        elif action is None and method == "DELETE":
            allowed = guard._can_edit_skill(skill_id, creator_id)
        else:
            allowed = False
        if not allowed:
            return guard._skill_denied()
    else:
        switch = _SWITCH.fullmatch(request.url.path)
        if not switch or not guard._owns_series(switch.group(1), creator_id):
            return guard._skill_denied()
        if method == "POST":
            try:
                payload = await request.json()
            except Exception:
                return guard._skill_denied()
            if not isinstance(payload, dict) or not guard._owns_skill(payload.get("skill_id", ""), creator_id):
                return guard._skill_denied()
        elif method != "GET":
            return guard._skill_denied()
    request.state.web_skill_creator = creator_id
    return None
