"""Resolve a production Skill selection consistently for every entry point."""
from dataclasses import dataclass
from typing import Any

from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest, skills_root_for
from creatoros.integrations.skill_library import SkillLibraryService
from creatoros.integrations.skill_pair import snapshot_pair
from creatoros.storage import SeriesSkillRef

from .errors import ContentRunError
from .task_contracts import TaskExecutionSpec


@dataclass(frozen=True)
class ImageSkillSelection:
    execution_spec: TaskExecutionSpec | None
    single_id: str | None
    input_fields: dict[str, Any]


def resolve_task_skill_ids(library, creator_id, skill_ids, *, kind):
    """One-off selection uses the same account-use authority as staged tasks."""
    if not skill_ids or len(skill_ids) > 12 or len(set(skill_ids)) != len(skill_ids):
        raise ContentRunError("请选择1～12份不重复的精确 Skill。", code="invalid_skill_selection", status_code=422)
    if any(not library.can_use(creator_id, skill_id) for skill_id in skill_ids):
        raise ContentRunError("所选 Skill 不在当前账号的 Skill 库中。", code="scope_conflict", status_code=403)
    try:
        return TaskExecutionSpec(kind=kind, skills=[dict(skill_id=skill_id,
            digest=library.describe(skill_id)["digest"], role="selected") for skill_id in skill_ids])
    except (ValueError, OSError, KeyError) as error:
        raise ContentRunError("所选 Skill 版本不可用，请重新读取。", code="invalid_skill_selection", status_code=422) from error


def resolve_image_skills(database, session, series, *, protocol, selected=None, topic_source=None, catalog=None,
                         library=None):
    """Resolve defaults or an explicit selection without creating a Topic or Run.

    Both ordinary production and preflight for reference production use this
    function, so a newly configured Skill pool cannot pass one entry and fail
    an older, separate preflight at the other entry.
    """
    catalog = catalog or ProducerSkillCatalog(skills_root_for(database))
    library = library or SkillLibraryService(database, catalog)
    execution_spec = None
    if selected is None:
        pool = session.get(SeriesSkillRef, series.id)
        defaults = (pool.defaults_json or {}).get("image", []) if pool else []
        if defaults:
            if any(not library.can_use(series.creator_id, item["skill_id"]) for item in defaults):
                raise ContentRunError("栏目默认 Skill 不在当前账号可用库中，请重新选择。", code="scope_conflict", status_code=403)
            execution_spec = TaskExecutionSpec(kind="image", skills=[{
                **item, "digest": library.describe(item["skill_id"])["digest"]} for item in defaults])
        elif pool is not None or not any((series.skill_name, series.mind_skill_id, series.production_skill_id)):
            raise ContentRunError("请选择本次制作的主 Skill，或先设置栏目图片阶段默认。",
                                  code="skill_selection_required", status_code=422)
    if execution_spec is not None:
        return ImageSkillSelection(execution_spec, None, {"execution_spec": execution_spec})

    single_id = selected.skill_id if selected and selected.skill_id else (
        None if selected and (selected.mind_skill_id or selected.production_skill_id) else series.skill_name)
    requested = ([single_id] if single_id else [
        (selected.mind_skill_id if selected else None) or series.mind_skill_id,
        (selected.production_skill_id if selected else None) or series.production_skill_id])
    if any(skill_id and not library.can_use(series.creator_id, skill_id) for skill_id in requested):
        raise ContentRunError("所选 Skill 不在当前账号可用库中。", code="scope_conflict", status_code=403)
    if single_id is None:
        mind_id = (selected.mind_skill_id if selected else None) or series.mind_skill_id
        production_id = (selected.production_skill_id if selected else None) or series.production_skill_id
        if not mind_id or not production_id:
            raise ContentRunError("双 Skill 制作需要完整的内容与呈现选择。", code="invalid_skill_selection", status_code=422)
        try:
            pair = snapshot_pair(catalog, mind_id, production_id, native=protocol == "native-v1",
                                 selected_digests=selected.expected_digests if selected else None)
        except ValueError as error:
            raise ContentRunError(f"双 Skill 不可用：{error}", code="unsupported_skill_pair") from error
        creator = series.creator
        return ImageSkillSelection(None, None, dict(
            composition=pair, creator_name=creator.display_name, creator_platform=creator.platform.value,
            series_revision=series.revision, topic_source=topic_source))

    try:
        directory = (catalog.resolve_selected_version(single_id, expected_digest=selected.expected_digests[single_id])
                     if selected else catalog.resolve(single_id))
        digest = selected.expected_digests[single_id] if selected else _digest(directory)
    except (ValueError, OSError) as error:
        raise ContentRunError(str(error), code="invalid_skill") from error
    return ImageSkillSelection(None, single_id, {"skill_path": str(directory), "skill_digest": digest})
