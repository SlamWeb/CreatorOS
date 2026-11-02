"""Generic account-scoped plans, executed by the host using existing tools."""
from hashlib import sha256
import json
import re
from typing import Annotated, Any, Literal, Union
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from creatoros.integrations.studio import StudioClientError
from creatoros.plan_capabilities import PLAN_TOOL_NAMES, allows_tool_action
from .argument_contract import ActionContract
from .results import ToolResult
from .studio import _call


def _has_reference(value):
    if isinstance(value, dict):
        return '$ref' in value or any(_has_reference(item) for item in value.values())
    return isinstance(value, list) and any(_has_reference(item) for item in value)


def _validate_argument_shape(value, schema, root):
    """Check the generated contract's fixed structure; refs defer value checks."""
    if isinstance(value, dict) and '$ref' in value:
        if set(value) != {'$ref'} or not isinstance(value['$ref'], str):
            raise ValueError('步骤引用格式无效。')
        return
    if '$ref' in schema:
        schema = root['$defs'][schema['$ref'].rsplit('/', 1)[-1]]
    for keyword in ('oneOf', 'anyOf'):
        if keyword in schema:
            for branch in schema[keyword]:
                try:
                    _validate_argument_shape(value, branch, root)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError('参数不符合当前工具的任何动作或输入分支。')
    if 'const' in schema and value != schema['const']:
        raise ValueError('工具动作或任务类型无效。')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('参数不在允许范围内。')
    expected = schema.get('type')
    matches = {'object': isinstance(value, dict), 'array': isinstance(value, list),
               'string': isinstance(value, str), 'integer': type(value) is int,
               'number': type(value) in (int, float), 'boolean': type(value) is bool,
               'null': value is None}
    if expected and not matches.get(expected, True):
        raise ValueError(f'参数类型必须为 {expected}。')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if any(key not in value for key in schema.get('required', [])):
            raise ValueError('缺少当前工具契约要求的字段。')
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ValueError('包含当前工具契约不接受的旧字段或额外字段。')
        for key, item in value.items():
            if key in properties:
                _validate_argument_shape(item, properties[key], root)
    elif isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', len(value)):
            raise ValueError('参数列表长度不符合当前工具契约。')
        for item in value:
            _validate_argument_shape(item, schema.get('items', {}), root)
    elif isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', len(value)):
            raise ValueError('参数文本长度不符合当前工具契约。')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            raise ValueError('参数文本格式不符合当前工具契约。')
    elif type(value) in (int, float):
        if (('minimum' in schema and value < schema['minimum'])
                or ('maximum' in schema and value > schema['maximum'])
                or ('exclusiveMinimum' in schema and value <= schema['exclusiveMinimum'])
                or ('exclusiveMaximum' in schema and value >= schema['exclusiveMaximum'])):
            raise ValueError('参数数值不符合当前工具契约。')


def validate_plan_arguments(tool, arguments, context, *, allow_references=False):
    """Resolve-free checks and dispatch share the registered tool contract."""
    from .definitions import tool_registry
    from .host_contract import bind_account_arguments
    definition = tool_registry[tool]
    raw = bind_account_arguments(tool, json.dumps(arguments, ensure_ascii=False), context)
    if allow_references and _has_reference(arguments):
        schema = definition.to_schema()['function']['parameters']
        _validate_argument_shape(json.loads(raw), schema, schema)
        return arguments
    return definition.parse_arguments(raw)


class PlanStepArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$", description="本计划内唯一的简短步骤标识。")
    title: str = Field(min_length=1, max_length=240, description="用户能理解的步骤目标。")
    tool: Literal[PLAN_TOOL_NAMES]
    arguments: dict[str, Any] = Field(description="对应工具当前契约的参数；生产必须明确action与任务kind，仅用skill_ids。依赖结果可用 {$ref: '步骤ID.字段'}，保存前检查固定字段、执行前完整校验。")
    depends_on: list[str] = Field(default_factory=list, max_length=100)
    wait_for_completion: bool = Field(default=False, description="提交长任务后等待其真实终态，再解锁依赖步骤。")


class PlanArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class CreatePlanArgs(PlanArguments):
    action: Literal["create"]
    title: str = Field(min_length=1, max_length=240)
    steps: list[PlanStepArgs] = Field(min_length=1, max_length=100)


class ListPlansArgs(PlanArguments):
    action: Literal["list"]


class GetPlanArgs(PlanArguments):
    action: Literal["get"]
    plan_id: str = Field(min_length=1, max_length=128)


class UpdatePlanArgs(PlanArguments):
    action: Literal["update"]
    plan_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=0)
    steps: list[PlanStepArgs] = Field(min_length=1, max_length=100)


class PlanVersionActionArgs(PlanArguments):
    action: Literal["pause", "resume"]
    plan_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=0)


class PlanStepActionArgs(PlanArguments):
    action: Literal["retry", "skip"]
    plan_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=0)
    step_id: str = Field(min_length=1, max_length=64)


ManagePlanArgs = ActionContract(Annotated[Union[
    CreatePlanArgs, ListPlansArgs, GetPlanArgs, UpdatePlanArgs, PlanVersionActionArgs, PlanStepActionArgs,
], Field(discriminator="action")])


def _valid_write_receipt(data, *, plan_id, session_id, creator_id, expected_version):
    """A 2xx JSON object is not itself a receipt for the requested plan."""
    if not isinstance(data, dict):
        return False
    version, owner, status = data.get("version"), data.get("creator_id"), data.get("status")
    return (data.get("id") == plan_id and data.get("session_id") == session_id
            and isinstance(owner, str) and bool(owner.strip())
            and (creator_id is None or owner == creator_id)
            and type(version) is int and version >= 1
            and (expected_version is None or type(expected_version) is int and version > expected_version)
            and isinstance(status, str) and status in {"running", "paused", "completed", "needs_attention"}
            and isinstance(data.get("steps"), list))


def manage_plan(action, plan_id=None, title=None, steps=None, expected_version=None, step_id=None, context=None):
    session = getattr(context, "agent_session_id", None)
    creator = getattr(context, "creator_id", None)
    continuation = getattr(context, "plan_continuation_id", None)
    write_started = False
    write_status = None
    write_outcome_unknown = False

    def write(client, method, path, *, expected_id, minimum_version, **kwargs):
        nonlocal write_started, write_status, write_outcome_unknown
        write_started = True
        try:
            data = client.request(method, path, **kwargs)
            if not _valid_write_receipt(data, plan_id=expected_id, session_id=session,
                                        creator_id=creator, expected_version=minimum_version):
                write_outcome_unknown = True
                raise StudioClientError("计划写入可能已生效，但回执无效；请先核对原计划，不要自动重试。",
                                        code="studio_outcome_unknown")
            return data
        except StudioClientError as error:
            write_status = error.status_code
            known_rejection = (error.code == "studio_unavailable" and write_status is None or
                write_status in {403, 415} and error.code in {"agent_scope_rejected", "origin_rejected", "json_required"})
            if not known_rejection:
                write_outcome_unknown = True
            raise
        except Exception:
            write_outcome_unknown = True
            raise

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
            identity = sha256(json.dumps({"session_id": session, "request_id": request_id}, ensure_ascii=False,
                                         sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            return write(client, "POST", root, expected_id=identity, minimum_version=None,
                         payload={"request_id": request_id, "title": title, "steps": normalized})
        if action == "list":
            return client.request("GET", root)
        path = root + "/" + quote(plan_id, safe="")
        if action == "get":
            return client.request("GET", path)
        if action == "update":
            normalized = [PlanStepArgs.model_validate(item).model_dump(mode="json") for item in steps]
            if len({item["id"] for item in normalized}) != len(normalized):
                raise StudioClientError("步骤 ID 不能重复；请为每一步使用唯一 ID。", code="invalid_arguments")
            if continuation:
                current = client.request("GET", path)
                if current.get('read_only'):
                    raise StudioClientError('历史计划使用旧工具契约，仅供查看。', code='plan_contract_conflict')
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
                for item in normalized:
                    previous = old.get(item["id"])
                    if previous and previous["status"] not in {"pending", "blocked"}:
                        continue  # server also enforces immutable executed steps
                    if not allows_tool_action(item["tool"], item["arguments"].get("action"), scope="continuation_edit"):
                        raise StudioClientError("自动调整不能加入删除或更改账号数据的步骤。", code="agent_scope_rejected")
                    if item["tool"] == "start_content_run" and started:
                        if not previous or previous["tool"] != "start_content_run":
                            raise StudioClientError("已有生产任务，不能自动新增生产步骤；请先核对原任务。", code="production_replay_blocked")
                        old_args, new_args = previous["arguments"], item["arguments"]
                        if old_args != new_args:
                            def without_video_inputs(value):
                                data = json.loads(json.dumps(value))
                                if data.get('action') != 'tasks': return None
                                tasks = data.get("tasks")
                                if not isinstance(tasks, list) or not tasks or any(t.get("kind") != "video" for t in tasks):
                                    return None
                                for task in tasks:
                                    task.pop("inputs", None)
                                return data
                            comparable = without_video_inputs(old_args)
                            if comparable is None or comparable != without_video_inputs(new_args):
                                raise StudioClientError("已有生产任务，不能自动修改其他生产要求；请先核对原任务。", code="production_replay_blocked")
            return write(client, "PATCH", path, expected_id=plan_id, minimum_version=expected_version,
                         payload={"expected_version": expected_version, "steps": normalized})
        return write(client, "POST", path + "/actions", expected_id=plan_id, minimum_version=expected_version,
                     payload={"expected_version": expected_version, "action": action,
                              **({"step_id": step_id} if step_id else {})})
    try:
        result = _call(invoke, context)
    except Exception as error:
        if not continuation and not write_outcome_unknown:
            raise
        result = ToolResult(content=f"工具 manage_plan 执行失败：{error}", is_error=True,
                            error_type="tool_exception", details={"exception_type": type(error).__name__})
    if continuation and action in {"update", "resume", "retry", "skip"}:
        result.details["continuation_write_started"] = write_started
        if write_status is not None:
            result.details["continuation_http_status"] = write_status
    if write_outcome_unknown:
        result.details["plan_write_outcome_unknown"] = True
    return result
