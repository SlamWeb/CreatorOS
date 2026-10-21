"""One bounded task tool; session identity and retries belong to the host."""
import hashlib
import json
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .results import ToolResult
from .studio import _call


class ManageSkillIterationArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    action: Literal["start", "get", "pause", "resume", "continue"]
    iteration_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    series_id: str | None = Field(default=None, min_length=1, max_length=120)
    skill_id: str | None = Field(default=None, min_length=1, max_length=160)
    topic_id: str | None = Field(default=None, min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=4000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=6,
        description="本会话已上传、用户指定为优秀参考的真实图片ID；不接受本地路径。")
    reference_paths: list[str] | None = Field(default=None, max_length=20,
        description="从目标Skill文件列表取得的参考图相对路径；省略用其图片，最多20张，不接受任意本地路径。")
    max_rounds: int = Field(default=3, ge=1, le=3,
        description="默认3轮含基线；到上限停止，继续需要用户明确要求。")
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def operation_fields(self):
        if self.action == "start":
            if not all((self.series_id, self.skill_id, self.topic_id)) or self.iteration_id is not None:
                raise ValueError("启动需要栏目、准确Skill版本与已入队选题，不传iteration_id。")
            if self.expected_revision is not None:
                raise ValueError("启动不传任务revision。")
        elif not self.iteration_id:
            raise ValueError("请使用已有校准任务ID。")
        elif any((self.series_id, self.skill_id, self.topic_id, self.instructions, self.attachment_ids)) or self.reference_paths is not None:
            raise ValueError("查询和任务动作不能更换原始校准输入。")
        elif self.max_rounds != 3:
            raise ValueError("轮数只在启动时配置；继续使用任务既定预算。")
        elif self.action == "get" and self.expected_revision is not None:
            raise ValueError("查询不需要任务revision。")
        elif self.action != "get" and self.expected_revision is None:
            raise ValueError("控制任务前需先查询当前revision。")
        return self


def manage_skill_iteration(action, iteration_id=None, series_id=None, skill_id=None, topic_id=None,
                           instructions="", attachment_ids=None, max_rounds=3,
                           expected_revision=None, reference_paths=None, context=None):
    values = ManageSkillIterationArgs(action=action, iteration_id=iteration_id, series_id=series_id,
        skill_id=skill_id, topic_id=topic_id, instructions=instructions,
        attachment_ids=attachment_ids or [], reference_paths=reference_paths,
        max_rounds=max_rounds, expected_revision=expected_revision)
    path = "/api/skill-iterations"
    if action == "get":
        return _call(lambda client: client.request("GET", path + "/" + quote(iteration_id, safe="")), context)
    sid, turn = getattr(context, "agent_session_id", None), getattr(context, "user_request_id", None)
    if not sid or not turn:
        return ToolResult("校准操作需要当前网页对话和用户请求。", True, "session_required")
    # Refreshing the observed revision or reformulating instructions in the same
    # human turn must not authorize another paid loop. The API compares inputs
    # against this stable identity and reports a conflict instead.
    target = [series_id, skill_id, topic_id] if action == "start" else iteration_id
    identity = json.dumps([sid, turn, action, target], ensure_ascii=False, sort_keys=True)
    request_id = hashlib.sha256(identity.encode()).hexdigest()
    if action == "start":
        payload = values.model_dump(include={"series_id", "skill_id", "topic_id", "instructions",
                                             "attachment_ids", "max_rounds", "reference_paths"})
    else:
        path += "/" + quote(iteration_id, safe="") + "/actions"
        payload = {"action": action, "expected_revision": expected_revision}
    payload["request_id"] = request_id
    return _call(lambda client: client.request("POST", path, payload=payload), context)
