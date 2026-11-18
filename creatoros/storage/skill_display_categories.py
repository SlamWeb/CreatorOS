"""Local display metadata shared by the library and draft registration."""
from typing import Literal, get_args

from sqlalchemy.dialects.sqlite import insert

from .models import SkillDisplayCategories


Category = Literal["content", "visual", "processing", "rules"]


def normalize_categories(categories: list[Category]) -> list[Category]:
    allowed = get_args(Category)
    if (not isinstance(categories, list)
            or any(not isinstance(category, str) or category not in allowed for category in categories)):
        raise ValueError("请使用有效的 Skill 分类。")
    if len(categories) != len(set(categories)):
        raise ValueError("分类不能重复。")
    return sorted(categories)


def initialize_display_categories(database, family_id: str, categories: list[Category]) -> None:
    """Start at revision one and preserve any row already written by the operator."""
    statement = insert(SkillDisplayCategories).values(family_id=family_id, categories_json=categories, revision=1)
    with database.session() as session:
        session.execute(statement.on_conflict_do_nothing(index_elements=[SkillDisplayCategories.family_id]))
