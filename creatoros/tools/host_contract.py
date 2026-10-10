"""Small host-specific schema views; the business tools remain shared."""
from copy import deepcopy
import json


ACCOUNT_BOUND_TOOLS = frozenset({"list_creator_series", "get_creator_tasks", "compose_series"})


class AccountArgumentRejected(ValueError):
    pass


def model_tool_schemas(schemas, context):
    allowed = context.allowed_tools
    result = deepcopy([schema for schema in schemas
                       if allowed is None or schema["function"]["name"] in allowed])
    for schema in result:
        function = schema["function"]
        name = function["name"]
        if context.archive_only_reads and name == "read_file":
            function["description"] = "读取当前会话的工具结果归档文本；不能读取项目文件、Skill 文件或其他会话。"
            function["parameters"]["properties"]["path"]["description"] = "当前会话归档结果中提供的路径。"
        if context.creator_id and name in ACCOUNT_BOUND_TOOLS:
            parameters = function["parameters"]
            parameters["properties"].pop("creator_id", None)
            parameters["required"] = [key for key in parameters.get("required", []) if key != "creator_id"]
            function["description"] = {
                "list_creator_series": "查询当前绑定账号的栏目；账号由宿主确定。",
                "get_creator_tasks": "查询当前绑定账号的调研、生产与讨论任务；可按栏目筛选，账号由宿主确定。",
                "compose_series": "用户明确要求时为当前账号创建栏目：使用一份完整制作 Skill 或内容与呈现 Skill 组合。账号由宿主绑定。",
            }[name]
    return result


def bind_account_arguments(name, raw_arguments, context):
    if not context or not context.creator_id or name not in ACCOUNT_BOUND_TOOLS:
        return raw_arguments
    arguments = json.loads(raw_arguments or "{}")
    if not isinstance(arguments, dict):
        raise ValueError("工具参数必须是 JSON object。")
    if "creator_id" in arguments and arguments["creator_id"] != context.creator_id:
        raise AccountArgumentRejected("该对话不能访问其他账号；账号范围由宿主确定。")
    arguments["creator_id"] = context.creator_id
    return json.dumps(arguments, ensure_ascii=False)
