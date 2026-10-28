"""Generic account-scoped plans, executed by the host using existing tools."""
from hashlib import sha256
import json
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creatoros.integrations.studio import StudioClientError
from .studio import _call


class PlanStepArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$", description="本计划内唯一的简短步骤标识。")
    title: str = Field(min_length=1, max_length=240, description="用户能理解的步骤目标。")
    tool: Literal["request_review", "start_content_run", "manage_topics", "manage_material", "manage_media_collection",
                  "manage_skill_library", "get_content_run", "get_creator_tasks", "get_producer_skill"]
    arguments: dict[str, Any] = Field(description="该工具的结构化参数；依赖结果可用 {$ref: '步骤ID.字段'}。")
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    wait_for_completion: bool = Field(default=False, description="提交长任务后等待其真实终态，再解锁依赖步骤。")


class ManagePlanArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    action: Literal["create", "list", "get", "update", "pause", "resume", "retry", "skip"]
    plan_id: str | None = Field(default=None, min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    steps: list[PlanStepArgs] | None = Field(default=None, min_length=1, max_length=100)
    expected_version: int | None = Field(default=None, ge=0)
    step_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def fields_for_action(self):
        required, optional = {
            "create": ({"title", "steps"}, set()), "list": (set(), set()),
            "get": ({"plan_id"}, set()), "update": ({"plan_id", "expected_version", "steps"}, set()),
            "pause": ({"plan_id", "expected_version"}, set()),
            "resume": ({"plan_id", "expected_version"}, set()),
            "retry": ({"plan_id", "expected_version", "step_id"}, set()),
            "skip": ({"plan_id", "expected_version", "step_id"}, set()),
        }[self.action]
        present = {key for key in self.model_fields_set if key != "action" and getattr(self, key) is not None}
        if not required <= present or present - required - optional:
            raise ValueError("计划操作的参数不完整或混用。")
        return self


def manage_plan(action, plan_id=None, title=None, steps=None, expected_version=None, step_id=None, context=None):
    session = getattr(context, "agent_session_id", None)
    continuation = getattr(context, "plan_continuation_id", None)
    def invoke(client):
        if not session:
            raise StudioClientError("需要当前账号对话才能管理计划。", code="session_required")
        if continuation and (plan_id != continuation or action not in {"get", "update", "resume", "retry", "skip"}):
            raise StudioClientError("自动续接只能调整本次通知对应的计划。", code="agent_scope_rejected")
        root = f"/api/agent/sessions/{quote(session, safe='')}/plans"
        if action == "create":
            normalized = [PlanStepArgs.model_validate(item).model_dump(mode="json") for item in steps]
            turn = getattr(context, "user_request_id", None)
            identity = ["agent-plan-v1", session, turn, title, normalized]
            request_id = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest() if turn else uuid4().hex
            return client.request("POST", root, payload={"request_id": request_id, "title": title, "steps": normalized})
        if action == "list":
            return client.request("GET", root)
        path = root + "/" + quote(plan_id, safe="")
        if action == "get":
            return client.request("GET", path)
        if action == "update":
            normalized = [PlanStepArgs.model_validate(item).model_dump(mode="json") for item in steps]
            if continuation:
                current = client.request("GET", path)
                if current["version"] != expected_version:
                    raise StudioClientError("计划已变化，请重新读取。", code="plan_conflict")
                old = {item["id"]: item for item in current["steps"]}
                # During autonomous recovery, omitted steps mean unchanged,
                # never delete history or already-submitted work by omission.
                updates = {item["id"]: item for item in normalized}
                fields = ("id", "title", "tool", "arguments", "depends_on", "wait_for_completion")
                normalized = [updates.pop(item["id"], {key: item[key] for key in fields})
                              for item in current["steps"]] + list(updates.values())
                started = any(item["tool"] == "start_content_run" and item["status"] not in {"pending", "blocked"}
                              for item in current["steps"])
                safe_actions = {"manage_topics": {"list", "get"}, "manage_skill_library": {"get"},
                    "manage_material": {"list", "get", "import_link"},
                    "manage_media_collection": {"list", "get", "create", "export"}}
                for item in normalized:
                    previous = old.get(item["id"])
                    if previous and previous["status"] not in {"pending", "blocked"}:
                        continue  # server also enforces immutable executed steps
                    if item["tool"] == "start_content_run" and started:
                        if not previous or previous["tool"] != "start_content_run":
                            raise StudioClientError("已有生产任务，不能自动新增生产步骤；请先核对原任务。", code="production_replay_blocked")
                        old_args, new_args = previous["arguments"], item["arguments"]
                        if old_args != new_args:
                            def without_video_inputs(value):
                                data = json.loads(json.dumps(value))
                                tasks = data.get("tasks")
                                if not isinstance(tasks, list) or not tasks or any(t.get("kind") != "video" for t in tasks):
                                    return None
                                for task in tasks:
                                    task.pop("inputs", None)
                                return data
                            comparable = without_video_inputs(old_args)
                            if comparable is None or comparable != without_video_inputs(new_args):
                                raise StudioClientError("已有生产任务，不能自动修改其他生产要求；请先核对原任务。", code="production_replay_blocked")
                    elif item["tool"] in safe_actions and item["arguments"].get("action") not in safe_actions[item["tool"]]:
                        raise StudioClientError("自动调整不能加入删除或更改账号数据的步骤。", code="agent_scope_rejected")
            return client.request("PATCH", path, payload={"expected_version": expected_version, "steps": normalized})
        return client.request("POST", path + "/actions", payload={"expected_version": expected_version,
                              "action": action, **({"step_id": step_id} if step_id else {})})
    return _call(invoke, context)
