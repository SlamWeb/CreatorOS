"""Tools for one explicitly authorized account chat turn."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .results import ToolResult


class SkillTaskArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    operation: Literal["prepare", "list", "read", "write", "run", "publish"]
    skills: list[str] | None = Field(default=None, max_length=4,
        description="prepare 可选：当前账号可用的准确 Skill ID。当前轮提交的附件自动进入 inputs。")
    path: str | None = Field(default=None, min_length=1, max_length=240,
        description="read/write/run 使用相对路径。脚本放在 work/ 或使用已冻结的 skills/ 下脚本。")
    content: str | None = Field(default=None, max_length=65536, description="write 的完整 UTF-8 文件内容。")
    args: list[str] | None = Field(default=None, max_length=24, description="run 的脚本参数列表。")
    paths: list[str] | None = Field(default=None, min_length=1, max_length=8,
        description="publish 要交付的 out/ 文件，相对路径。")

    @model_validator(mode="after")
    def operation_fields(self):
        allowed, required = {
            "prepare": ({"skills"}, set()),
            "list": (set(), set()),
            "read": ({"path"}, {"path"}),
            "write": ({"path", "content"}, {"path", "content"}),
            "run": ({"path", "args"}, {"path"}),
            "publish": ({"paths"}, {"paths"}),
        }[self.operation]
        supplied = {key for key in ("skills", "path", "content", "args", "paths") if getattr(self, key) is not None}
        if supplied - allowed or required - supplied:
            raise ValueError(f"{self.operation} 需要 {sorted(required)}，可传 {sorted(allowed)}。")
        return self


def manage_skill_task(operation, skills=None, path=None, content=None, args=None, paths=None, context=None):
    task = getattr(context, "skill_task", None)
    if task is None:
        return ToolResult("请在账号对话中明确授权本轮运行脚本。", True, "skill_task_not_authorized")
    from ..integrations.skill_task_runner import SkillTaskRunnerError
    from ..integrations.skill_tasks import SkillTaskError

    try:
        result = task.perform(operation, skills=skills, path=path, content=content, args=args, paths=paths)
    except SkillTaskRunnerError as error:
        return ToolResult(str(error), True, error.code)
    except (SkillTaskError, ValueError) as error:
        return ToolResult(str(error), True, "skill_task_invalid", retryable=True)
    except OSError:
        return ToolResult("任务文件无法读取或保存，请先核对工作目录状态。", True, "skill_task_io")
    return ToolResult(json.dumps(result, ensure_ascii=False))
