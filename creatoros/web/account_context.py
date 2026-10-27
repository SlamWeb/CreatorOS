"""Read-only account capability tree; never a second source of business state."""
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from creatoros.skills.loader import SkillLoader
from creatoros.storage import Creator, CreatorSkillRef, Series, SeriesSkillRef
from creatoros.integrations.producer_skills import declared_skill_outputs
from creatoros.integrations.skill_library import SkillLibraryService


SKILL_DESCRIPTION_LIMIT = 160


class CreatorContextBuilder:
    def __init__(self, database, catalog, skill_library=None):
        self.database, self.catalog = database, catalog
        self.skill_library = skill_library or SkillLibraryService(database, catalog)

    @staticmethod
    def _text(value, resource, field, omissions):
        text = value or ""
        if len(text) > SKILL_DESCRIPTION_LIMIT:
            omissions.append({"resource": resource, "field": field,
                              "original_chars": len(text), "shown_chars": SKILL_DESCRIPTION_LIMIT})
            return text[:SKILL_DESCRIPTION_LIMIT] + "\n[未完整展示；需要时按需查询]"
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
                    "display_name": metadata.display_name or metadata.name,
                    "description": self._text(metadata.description, skill_id, "description", omissions),
                    "available": True, "outputs": declared_skill_outputs(directory)}
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
                       "platform": creator.platform.value}
            library = session.get(CreatorSkillRef, creator_id)
            library_ids = list(library.skill_ids_json) if library else []
            # Every active column remains visible; archived history is read on demand.
            columns = session.scalars(
                select(Series).where(Series.creator_id == creator_id, Series.is_active.is_(True))
                .order_by(Series.created_at, Series.id))
            for column in columns:
                bindings = ({"single": column.skill_name} if column.skill_name else
                            {"mind": column.mind_skill_id, "production": column.production_skill_id}
                            if column.mind_skill_id and column.production_skill_id else {})
                for role, reference in list(bindings.items()):
                    if reference not in resolved:
                        item = self._skill(reference, omissions)
                        resolved[reference] = item["id"]
                        skills.setdefault(item["id"], item)
                    bindings[role] = resolved[reference]
                pool = session.get(SeriesSkillRef, column.id)
                pool_ids = list(pool.skill_ids_json) if pool else list(bindings.values())
                library_ids.extend(value for value in pool_ids if self.skill_library.can_use(creator_id, value))
                series.append({"id": column.id, "name": column.name,
                               "revision": column.revision,
                               "skill_bindings": bindings,
                               "skill_pool": {"version": pool.version if pool else 0, "skill_ids": pool_ids,
                                   "defaults": pool.defaults_json if pool else {"image": ([
                                       {"skill_id": bindings.get("single") or bindings["production"], "role": "primary"},
                                       *([{"skill_id": bindings["mind"], "role": "supporting"}] if "mind" in bindings else [])
                                   ] if bindings else []), "video": []}}})
            for reference in dict.fromkeys(library_ids):
                if reference not in resolved:
                    item = self._skill(reference, omissions)
                    resolved[reference] = item["id"]
                    skills.setdefault(item["id"], item)
            library_version = library.version if library else 0
        if self.skill_library:
            for item in skills.values():
                item["can_use"] = self.skill_library.can_use(creator_id, item["id"])
                item["can_edit"] = self.skill_library.can_edit(creator_id, item["id"])
        return {"kind": "creator_context", "creator": account,
                "series": series, "skills": list(skills.values()),
                "skill_library_version": library_version,
                "omissions": omissions}

    def require_series_skills(self, series_id):
        """New scoped production must not silently repair an unavailable binding."""
        with self.database.session() as session:
            column = session.get(Series, series_id)
            if column is None:
                raise HTTPException(404, "栏目不存在。")
            references = ([column.skill_name] if column.skill_name else
                          [column.mind_skill_id, column.production_skill_id]
                          if column.mind_skill_id and column.production_skill_id else [])
        pool = self.skill_library.get_series(series_id)
        if pool["version"]:
            references = [item["skill_id"] for item in pool["defaults"]["image"]]
        if not references:
            raise HTTPException(409, "请为本次任务选择 Skill，或先配置栏目阶段默认；未创建任务。")
        if any(not self._skill(reference, [])["available"] for reference in references):
            raise HTTPException(409, "绑定的本地 Skill 缺失或元数据无效，请修复后生产；未创建新任务。")
