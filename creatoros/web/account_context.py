"""Read-only account capability tree; never a second source of business state."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from creatoros.skills.loader import SkillLoader
from creatoros.storage import Creator, Series


TEXT_LIMIT = 1000


class CreatorContextBuilder:
    def __init__(self, database, catalog):
        self.database, self.catalog = database, catalog

    @staticmethod
    def _text(value, resource, field, omissions):
        text = value or ""
        if len(text) > TEXT_LIMIT:
            omissions.append({"resource": resource, "field": field,
                              "original_chars": len(text), "shown_chars": TEXT_LIMIT})
            return text[:TEXT_LIMIT] + "\n[未完整展示；需要时按需查询]"
        return text

    def _skill(self, reference, omissions):
        """Do not call describe(): legacy working-directory recovery can write files."""
        skill_id = reference
        try:
            if reference == self.catalog.BUILTIN_ID:
                managed = Path(self.catalog.project_root) / "creatoros/skills"
                if managed.is_symlink() or not managed.resolve().is_relative_to(Path(self.catalog.project_root).resolve()):
                    raise ValueError("invalid managed root")
                directory = managed / reference
            else:
                record = self.catalog._record(reference)
                skill_id = record["id"]
                managed = self.catalog.root / "working"
                if managed.is_symlink() or not managed.resolve().is_relative_to(self.catalog.root):
                    raise ValueError("invalid managed root")
                directory = self.catalog.root / "working" / skill_id
            if directory.resolve().parent != managed.resolve():
                raise ValueError("invalid directory")
            path = directory / "SKILL.md"
            if directory.is_symlink() or path.is_symlink() or not directory.is_dir():
                raise ValueError("missing working directory")
            if not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("invalid file")
            # Only the root metadata, never nested assets or Skill bodies in context.
            metadata = SkillLoader([directory])._read_metadata(path)
            if metadata is None:
                raise ValueError("invalid metadata")
            return {"id": skill_id, "name": metadata.name,
                    "description": self._text(metadata.description, skill_id, "description", omissions),
                    "available": True}
        except (ValueError, OSError, UnicodeError, KeyError):
            return {"id": skill_id, "name": Path(skill_id).name, "description": "",
                    "available": False, "error": "本地 Skill 缺失或元数据无效；需修复后生产。"}

    def build(self, creator_id):
        omissions, series, skills, resolved = [], [], {}, {}
        with self.database.session() as session:
            creator = session.get(Creator, creator_id)
            if creator is None:
                raise HTTPException(404, "绑定账号不存在。")
            if not creator.is_active:
                raise HTTPException(409, "绑定账号已停用。")
            account = {"id": creator.id, "display_name": creator.display_name,
                       "platform": creator.platform.value, "account_handle": creator.account_handle,
                       "timezone": creator.timezone, "daily_content_limit": creator.daily_content_limit,
                       "is_active": creator.is_active}
            # All columns for now, per user decision; no ranking or silent directory cap.
            columns = session.scalars(select(Series).where(Series.creator_id == creator_id)
                                      .order_by(Series.created_at, Series.id))
            for column in columns:
                bindings = ({"single": column.skill_name} if column.skill_name else
                            {"mind": column.mind_skill_id, "production": column.production_skill_id})
                for role, reference in list(bindings.items()):
                    if reference not in resolved:
                        item = self._skill(reference, omissions)
                        resolved[reference] = item["id"]
                        skills.setdefault(item["id"], item)
                    bindings[role] = resolved[reference]
                series.append({"id": column.id, "name": column.name,
                               "description": self._text(column.description, column.id, "description", omissions),
                               "audience": self._text(column.audience, column.id, "audience", omissions),
                               "revision": column.revision, "is_active": column.is_active,
                               "skill_bindings": bindings})
        return {"kind": "creator_context", "as_of": datetime.now(timezone.utc).isoformat(),
                "creator": account, "series": series, "skills": list(skills.values()),
                "omissions": omissions}

    def require_series_skills(self, series_id):
        """New scoped production must not silently repair an unavailable binding."""
        with self.database.session() as session:
            column = session.get(Series, series_id)
            if column is None:
                raise HTTPException(404, "栏目不存在。")
            references = ([column.skill_name] if column.skill_name else
                          [column.mind_skill_id, column.production_skill_id])
        if any(not self._skill(reference, [])["available"] for reference in references):
            raise HTTPException(409, "绑定的本地 Skill 缺失或元数据无效，请修复后生产；未创建新任务。")
