"""Host-only execution contract. HTTP callers provide IDs, never these paths."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskContract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class TaskSkill(TaskContract):
    skill_id: str = Field(min_length=1, max_length=120)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: Literal["primary", "supporting", "selected"]
    path: str | None = None


class TaskMaterial(TaskContract):
    id: str = Field(min_length=1, max_length=128)
    kind: Literal["images_zip", "audio", "video"]
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def absolute_path(self):
        if not Path(self.path).is_absolute():
            raise ValueError("执行素材必须使用宿主解析的绝对路径。")
        return self


class TaskExecutionSpec(TaskContract):
    kind: Literal["image", "video"]
    skills: list[TaskSkill] = Field(min_length=1, max_length=12)
    materials: list[TaskMaterial] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def selections(self):
        if not all(skill.role == "selected" for skill in self.skills) and sum(skill.role == "primary" for skill in self.skills) != 1:
            raise ValueError("任务必须且只能选择一个主 Skill。")
        if any(skill.role == "selected" for skill in self.skills) and not all(skill.role == "selected" for skill in self.skills):
            raise ValueError("统一 Skill 选择不能与旧角色混用。")
        if len({skill.skill_id for skill in self.skills}) != len(self.skills):
            raise ValueError("同一任务不能重复选择 Skill。")
        if len({item.id for item in self.materials}) != len(self.materials):
            raise ValueError("任务素材 ID 不能重复。")
        return self

    def public_dict(self) -> dict:
        return self.model_dump(mode="json", exclude={"skills": {"__all__": {"path"}},
                                                     "materials": {"__all__": {"path"}}})
