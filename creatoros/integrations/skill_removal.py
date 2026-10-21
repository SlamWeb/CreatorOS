"""Remove an unbound Skill from the active catalog without deleting its files."""
from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import or_, select

from creatoros.storage import Series

from .producer_skills import SkillDigestConflict, _digest


class SkillInUseError(ValueError):
    def __init__(self, series: list[dict]):
        super().__init__("Skill 正被有效栏目使用；请先更换或移除栏目绑定。")
        self.series = series


def remove_unused_skill(catalog, skill_id: str, expected_digest: str, database) -> dict:
    if skill_id == catalog.BUILTIN_ID:
        raise PermissionError("内置 Skill 只读，不能移出库。")
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_digest):
        raise ValueError("expected_digest 必须是文件列表返回的 Skill digest。")
    directory, _item, _skill = catalog._readable_skill_directory(skill_id)
    current = _digest(directory)
    if current != expected_digest:
        raise SkillDigestConflict(current)
    if len(catalog.list_skill_versions(skill_id)["items"]) > 1:
        raise PermissionError("这份 Skill 已有版本历史，不能单独移出，以免丢失回退入口。")
    record = catalog.root / "registry" / f"{skill_id}.json"
    if catalog._is_link_or_reparse(record) or not record.is_file():
        raise ValueError("Skill 注册记录不存在或路径无效。")

    trash = catalog.root / "trash" / "removed-skill-records"
    for candidate in (catalog.root / "trash", trash):
        if catalog._is_link_or_reparse(candidate) or (candidate.exists() and not candidate.is_dir()):
            raise ValueError("Skill 回收目录路径无效。")
        candidate.mkdir(exist_ok=True)
        if not candidate.resolve().is_relative_to(catalog.root):
            raise ValueError("Skill 回收目录路径越界。")
    archived_record = trash / f"{skill_id}--{uuid4().hex}.json"
    with database.session() as session:
        bindings = session.execute(select(Series.id, Series.name).where(
            Series.is_active.is_(True),
            or_(Series.skill_name == skill_id,
                Series.mind_skill_id == skill_id,
                Series.production_skill_id == skill_id),
        ).order_by(Series.id)).all()
        if bindings:
            raise SkillInUseError([{"id": row.id, "name": row.name} for row in bindings])
        # The registry entry is the catalog membership. Preserve working,
        # original versions, and every frozen Run directory for recovery.
        record.replace(archived_record)
    return {"skill_id": skill_id, "operation": "remove", "removed": True,
            "recovery_record": archived_record.name}
