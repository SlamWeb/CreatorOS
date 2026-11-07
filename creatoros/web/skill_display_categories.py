"""Local Web-only display metadata, never part of a Skill or Run snapshot."""
from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from creatoros.storage.models import SkillDisplayCategories


Category = Literal["content", "visual", "processing", "rules"]


class CategoryChange(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    categories: list[Category] = Field(max_length=4)
    expected_revision: int = Field(ge=0, le=2147483646)

    @field_validator("categories")
    @classmethod
    def unique_categories(cls, categories):
        if len(categories) != len(set(categories)):
            raise ValueError("分类不能重复。")
        return categories


def _local_web_only(request: Request) -> None:
    # Agent sessions and account-scoped operations must not read or alter the
    # local operator's global display metadata. This guard is not identity auth.
    if (request.headers.get("x-creatoros-agent-session")
            or request.headers.get("x-creatoros-origin", "").lower() == "agent"
            or "creator_id" in request.query_params):
        raise HTTPException(403, "Skill 分类仅供本机全局工作台使用。")
    client = request.client.host if request.client else ""
    try:
        is_local = ipaddress.ip_address(client).is_loopback
    except ValueError:
        is_local = client == "localhost"
    if not is_local or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(403, "Skill 分类仅供本机全局工作台使用。")
    origin = request.headers.get("origin")
    if origin is not None:
        parsed = urlsplit(origin)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise HTTPException(403, "Skill 分类仅供本机全局工作台使用。")


def _family_id(catalog, skill_id: str) -> str:
    if skill_id == catalog.BUILTIN_ID:
        return skill_id
    try:
        result = catalog.describe(skill_id)
        family_id = result["family_id"]
        if not isinstance(family_id, str) or not 1 <= len(family_id) <= 120:
            raise ValueError("Invalid family id")
        return family_id
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise HTTPException(404, "Skill 不可用或未登记。") from error


def _conflict():
    return JSONResponse(status_code=409, content={"error": {
        "code": "skill_display_category_conflict",
        "message": "分类已更改，请重新读取后再保存。",
    }}, headers={"Cache-Control": "no-store"})


def skill_display_category_routes(database, catalog):
    router = APIRouter(prefix="/api/skill-display-categories")

    @router.get("")
    def list_categories(request: Request):
        _local_web_only(request)
        with database.session() as session:
            rows = session.scalars(select(SkillDisplayCategories).order_by(SkillDisplayCategories.family_id)).all()
            items = [{"family_id": row.family_id, "categories": row.categories_json,
                      "revision": row.revision} for row in rows]
        return JSONResponse({"items": items}, headers={"Cache-Control": "no-store"})

    @router.put("/{skill_id}")
    def change_categories(skill_id: str, payload: CategoryChange, request: Request):
        _local_web_only(request)
        family_id = _family_id(catalog, skill_id)
        # Stable order makes the same logical set appear consistently.
        categories = sorted(payload.categories)
        revision = payload.expected_revision + 1
        try:
            with database.session() as session:
                if payload.expected_revision == 0:
                    session.add(SkillDisplayCategories(family_id=family_id, categories_json=categories, revision=revision))
                    session.flush()
                else:
                    changed = session.execute(update(SkillDisplayCategories).where(
                        SkillDisplayCategories.family_id == family_id,
                        SkillDisplayCategories.revision == payload.expected_revision,
                    ).values(categories_json=categories, revision=revision))
                    if changed.rowcount != 1:
                        return _conflict()
        except IntegrityError:
            return _conflict()
        return JSONResponse({"family_id": family_id, "categories": categories, "revision": revision},
                            headers={"Cache-Control": "no-store"})

    return router
