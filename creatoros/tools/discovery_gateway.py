"""Session-scoped model tool discovery; execution remains in the host loop.

This module does not dispatch business calls. It maps an observed gateway call
to the canonical registered call so the host can run its normal guard, tracing,
pending-task and unknown-write machinery unchanged.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json

from pydantic import ValidationError
from creatoros.ai.types import ToolCall
from creatoros.tools.definitions import tool_registry
from creatoros.tools.host_contract import AccountArgumentRejected, bind_account_arguments
from creatoros.tools.results import ToolResult


DISCOVER = "discover_tools"
EXECUTE = "call_discovered_tool"
FORMAT = "creatoros-tool-discovery-v1"
DISCOVERY_SOURCE = "gateway_discovery"
RESULT_SOURCE_PREFIX = "gateway_result:"
SCOPED_RESULT_VERSION = "v2:"


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return sha256(_encoded(value).encode("utf-8")).hexdigest()


def scope_token(context):
    if context.session_file is None or not context.creator_id:
        raise ValueError("需要绑定当前账号及会话。")
    return _digest({"creator_id": context.creator_id, "session_file": str(context.session_file.resolve())})


def scoped_gateway_source(context, action_id):
    if not isinstance(action_id, str) or not action_id or ":" in action_id:
        raise ValueError("无效的操作标识。")
    return RESULT_SOURCE_PREFIX + SCOPED_RESULT_VERSION + scope_token(context) + ":" + action_id


def gateway_source_action(source, context=None):
    """Read host marker; context requires current scoped v2, never legacy."""
    if not isinstance(source, str) or not source.startswith(RESULT_SOURCE_PREFIX):
        return None
    value = source[len(RESULT_SOURCE_PREFIX):]
    if value.startswith(SCOPED_RESULT_VERSION):
        parts = value.split(":", 2)
        if len(parts) != 3 or len(parts[1]) != 64 or any(c not in "0123456789abcdef" for c in parts[1]) or not parts[2]:
            return None
        if context is not None and parts[1] != scope_token(context):
            return None
        return parts[2]
    return value if context is None and value and ":" not in value else None


def _parse(raw):
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        raise ValueError("参数必须为 JSON 对象。")
    return data


def _invalid_parameters(error):
    if isinstance(error, ValidationError):
        problems = [{"field": ".".join(str(part) for part in item.get("loc", ())),
                     "message": item.get("msg", "参数无效"), "type": item.get("type")}
                    for item in error.errors(include_input=False, include_context=False)[:4]]
        return ToolResult("操作参数无效，未执行：" + _encoded(problems), True, "invalid_arguments", retryable=True,
                          details={"pre_dispatch": True})
    return ToolResult("操作参数无效或缺少必填字段，未执行；请根据工具说明修正。", True,
                      "invalid_arguments", retryable=True, details={"pre_dispatch": True})


def _validated_canonical(name, raw, context):
    """Match executor normalization while preserving which fields were sent.

    Guard identities must use the same stripped/validated values as the
    eventual handler. Exclude unset fields so action validators do not mistake
    default-valued fields for explicitly supplied incompatible arguments on
    the executor's second validation.
    """
    original = _parse(raw)
    bound = bind_account_arguments(name, raw, context)
    tool = tool_registry[name]
    # Execute the same validation path used by execute_tool_call.
    tool.parse_arguments(bound)
    if tool.args_model is None:
        normalized = _parse(bound)
    else:
        normalized = tool.args_model.model_validate_json(bound).model_dump(mode="json", exclude_unset=True)
    if not isinstance(normalized, dict):
        raise ValueError("工具参数必须为对象。")
    # The account binder is authoritative and will inject it again at dispatch;
    # do not add a model-hidden parameter to the canonical call unnecessarily.
    if "creator_id" not in original:
        normalized.pop("creator_id", None)
    return _encoded(normalized)


def _calls(message):
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or call
        if isinstance(function, dict):
            yield call.get("id"), function.get("name"), function.get("arguments")


def _before_current(messages, call_id):
    # A sibling discover in the *same* model output has not been seen by the
    # model and must not authorize this call, even if its result was processed.
    for index, message in enumerate(messages):
        if (isinstance(message, dict) and message.get("role") == "assistant"
                and any(cid == call_id for cid, _, _ in _calls(message))):
            return messages[:index]
    return messages


def resolved_canonical(call, tool_message):
    """Recover canonical identity for view/trace only, never for execution.

    Both the actual gateway call and host-owned tool-message marker must match.
    The provider still receives the original call and matching tool_call_id.
    """
    try:
        function = call.get("function") or call
        if (not isinstance(function, dict) or tool_message.get("role") != "tool"
                or tool_message.get("tool_call_id") != call.get("id")):
            return None
        key = gateway_source_action(tool_message.get("creatoros_source"))
        if key is None:
            return None
        args = _parse(function.get("arguments"))
        if function.get("name") == EXECUTE:
            if set(args) != {"name", "arguments"} or args["name"] != key or not isinstance(args["arguments"], dict):
                return None
            result = deepcopy(args["arguments"])
        else:
            from creatoros.tools.action_catalog import DIRECT_READ_ACTIONS
            if DIRECT_READ_ACTIONS.get(function.get("name")) != key or "action" in args:
                return None
            result = deepcopy(args)
        name, separator, action = key.partition(".")
        if name not in tool_registry or separator and not action:
            return None
        if separator:
            if "action" in result and result["action"] != action:
                return None
            result["action"] = action
        return name, result
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


class ToolGateway:
    def __init__(self, context, schemas, catalog=None, *, enable_direct=False):
        self.context = context
        self.enable_direct = enable_direct
        self.scope = scope_token(context)
        allowed_schemas = {s["function"]["name"]: s for s in schemas}
        if catalog is None:
            from creatoros.tools.action_catalog import build_action_catalog
            catalog = build_action_catalog(schemas)
        self.catalog = deepcopy(catalog)
        for key, item in self.catalog.items():
            name = item.get("name")
            tool = tool_registry.get(name)
            if (not isinstance(key, str) or item.get("id") != key or name not in allowed_schemas
                    or not tool or not tool.expose_to_model
                    or context.allowed_tools is not None and name not in context.allowed_tools
                    or not isinstance(item.get("schema"), dict)):
                raise ValueError("工具目录与宿主授权不一致。")
        self.versions = {key: _digest(item["schema"]) for key, item in self.catalog.items()}

    def model_schemas(self):
        directory = "\n".join(f"{key}: {item['label']}" for key, item in self.catalog.items())
        root = [
            {"type": "function", "function": {"name": DISCOVER,
             "description": "需要使用未说明的业务操作时，从目录选择操作，获取完整参数和适用规则。目录：\n" + directory,
             "parameters": {"type": "object", "properties": {"names": {"type": "array", "minItems": 1,
                             "maxItems": 4, "items": {"type": "string"}}},
                            "required": ["names"], "additionalProperties": False}}},
            {"type": "function", "function": {"name": EXECUTE,
             "description": "执行此前已发现的业务操作。name 为目录中的操作名；arguments 按发现结果填写。只在用户授权和业务条件允许时操作。",
             "parameters": {"type": "object", "properties": {"name": {"type": "string"},
                            "arguments": {"type": "object"}}, "required": ["name", "arguments"],
                            "additionalProperties": False}}},
        ]
        if self.enable_direct:
            from creatoros.tools.action_catalog import build_direct_read_schemas
            root.extend(build_direct_read_schemas(self.catalog))
        return root

    def resolve_direct(self, call):
        if not self.enable_direct:
            return None, ToolResult("此会话没有开放该只读快捷操作。", True, "tool_not_exposed")
        try:
            from creatoros.tools.action_catalog import DIRECT_READ_ACTIONS, resolve_direct_read
            if DIRECT_READ_ACTIONS.get(call.name) not in self.catalog:
                return None, ToolResult("只读操作不在当前允许的范围内。", True, "tool_not_exposed")
            name, args = resolve_direct_read(call.name, _parse(call.arguments), self.catalog)
            raw = _encoded(args)
            return ToolCall(id=call.id, name=name, arguments=_validated_canonical(name, raw, self.context)), None
        except AccountArgumentRejected as error:
            return None, ToolResult(str(error), True, "agent_scope_rejected")
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            return None, _invalid_parameters(error)
        except Exception as error:
            return None, ToolResult("操作验证异常，未执行，请核查。", True, "tool_exception",
                                    details={"exception_type": type(error).__name__, "pre_dispatch": True})

    def _visible(self, messages, current_id):
        prior = _before_current(messages, current_id)
        ids = Counter(cid for message in prior if isinstance(message, dict)
                      for cid, _, _ in _calls(message) if isinstance(cid, str))
        discovered_calls, found = {}, {}
        for message in prior:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "assistant":
                for cid, name, raw in _calls(message):
                    if name != DISCOVER or ids[cid] != 1:
                        continue
                    try:
                        requested = _parse(raw).get("names")
                        if isinstance(requested, list) and all(isinstance(value, str) for value in requested):
                            discovered_calls[cid] = set(requested)
                    except (ValueError, TypeError):
                        pass
            if (message.get("role") != "tool" or message.get("creatoros_source") != DISCOVERY_SOURCE
                    or message.get("tool_call_id") not in discovered_calls):
                continue
            try:
                receipt = json.loads(message.get("content", ""))
                if not isinstance(receipt, dict) or receipt.get("format") != FORMAT or receipt.get("scope") != self.scope:
                    continue
                items = receipt.get("tools")
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    key = item.get("id")
                    if (key in discovered_calls[message["tool_call_id"]] and key in self.catalog
                            and item.get("version") == self.versions[key]
                            and item.get("schema") == self.catalog[key]["schema"]):
                        found.setdefault(key, message["tool_call_id"])
            except (ValueError, TypeError):
                continue
        return found

    def discover(self, call, visible_messages):
        try:
            args = _parse(call.arguments)
            names = args.get("names")
            if set(args) != {"names"} or not isinstance(names, list) or not 1 <= len(names) <= 4:
                raise ValueError("names 必须为 1 到 4 个操作名。")
            if any(not isinstance(name, str) or name not in self.catalog for name in names) or len(set(names)) != len(names):
                raise ValueError("操作名不存在、不在当前账号范围内或重复。")
            found = self._visible(visible_messages, call.id)
            new = [{"id": key, "version": self.versions[key], "schema": self.catalog[key]["schema"]}
                   for key in names if key not in found]
            refs = [{"id": key, "version": self.versions[key], "tool_call_id": found[key]}
                    for key in names if key in found]
            return ToolResult(_encoded({"format": FORMAT, "scope": self.scope, "tools": new, "references": refs,
                                       "notice": "资料仅说明操作；权限、账号和资源版本仍以执行时校验为准。"}))
        except (TypeError, ValueError, KeyError):
            return ToolResult("操作发现参数无效，未发现或执行任何操作。", True, "invalid_tool_discovery")

    def resolve(self, call, visible_messages, *, prepare=None):
        try:
            args = _parse(call.arguments)
            key, arguments = args.get("name"), args.get("arguments")
            if set(args) != {"name", "arguments"} or not isinstance(key, str) or not isinstance(arguments, dict):
                raise ValueError("调用必须给出操作名称和参数对象。")
            if key not in self.catalog:
                return None, ToolResult("操作不在当前账号允许的目录中。", True, "tool_not_exposed")
            if key not in self._visible(visible_messages, call.id):
                return None, ToolResult("此操作尚未在当前可见会话中发现，或说明已变化；请先重新发现。", True, "tool_not_discovered")
            item = self.catalog[key]
            canonical_args = deepcopy(arguments)
            action = item.get("action")
            if action is not None:
                if "action" in canonical_args and canonical_args["action"] != action:
                    return None, ToolResult("操作名和 action 不一致。", True, "invalid_arguments")
                canonical_args["action"] = action
            raw = _encoded(canonical_args)
            canonical = ToolCall(id=call.id, name=item["name"], arguments=raw)
            if prepare is not None:
                prepared, error, _origin = prepare(self.context, canonical, visible_messages)
                if error is not None:
                    return None, error
                if (not isinstance(prepared, ToolCall) or prepared.id != canonical.id
                        or prepared.name != canonical.name):
                    return None, ToolResult("操作预处理结果不可信，未执行。", True, "invalid_arguments")
                prepared_args = _parse(prepared.arguments)
                if action is not None and prepared_args.get("action") != action:
                    return None, ToolResult("操作预处理改变了 action，未执行。", True, "invalid_arguments")
                if prepared_args != canonical_args:
                    allowed = (item["name"] == "manage_topics" and action == "update"
                               and "expected_digest" not in canonical_args
                               and isinstance(prepared_args.get("expected_digest"), str))
                    remainder = {key: value for key, value in prepared_args.items() if key != "expected_digest"}
                    if not allowed or remainder != canonical_args:
                        return None, ToolResult("操作预处理改变了请求内容，未执行。", True, "invalid_arguments")
                canonical = prepared
            # Validate before exposing the canonical call; execute_tool_call
            # performs the same real validation and authorization again.
            validated = _validated_canonical(item["name"], canonical.arguments, self.context)
            return ToolCall(id=canonical.id, name=canonical.name, arguments=validated), None
        except AccountArgumentRejected as error:
            return None, ToolResult(str(error), True, "agent_scope_rejected")
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            return None, _invalid_parameters(error)
        except Exception as error:
            return None, ToolResult("操作验证异常，未执行，请核查。", True, "tool_exception",
                                    details={"exception_type": type(error).__name__, "pre_dispatch": True})
