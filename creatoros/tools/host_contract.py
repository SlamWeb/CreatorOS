"""Small host-specific schema views; the business tools remain shared."""
from copy import deepcopy
import json


ACCOUNT_BOUND_TOOLS = frozenset({"list_creator_series", "get_creator_tasks", "compose_series", "query_feedback", "manage_media_collection", "manage_skill_library", "manage_material"})


class AccountArgumentRejected(ValueError):
    pass


def _strip_redundant_property_titles(schema):
    """Drop only generated field titles that repeat their JSON property name.

    Traverse schema locations, not arbitrary JSON values: defaults, examples,
    enum values and a real property named ``title`` must remain untouched.
    """
    if not isinstance(schema, dict):
        return
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, child in properties.items():
            if not isinstance(child, dict):
                continue
            if isinstance(name, str) and child.get("title") == name.replace("_", " ").title():
                child.pop("title")
            _strip_redundant_property_titles(child)
    for key in ("$defs", "definitions", "patternProperties", "dependentSchemas"):
        children = schema.get(key)
        if isinstance(children, dict):
            for child in children.values():
                _strip_redundant_property_titles(child)
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        children = schema.get(key)
        if isinstance(children, list):
            for child in children:
                _strip_redundant_property_titles(child)
    for key in ("items", "additionalProperties", "contains", "not", "if", "then", "else", "propertyNames"):
        _strip_redundant_property_titles(schema.get(key))


def _hide_account_field(schema):
    """Hide internal ownership fields inside referenced action branches as well."""
    if isinstance(schema, dict):
        schema.get("properties", {}).pop("creator_id", None)
        if "required" in schema:
            schema["required"] = [key for key in schema["required"] if key != "creator_id"]
        for value in schema.values():
            _hide_account_field(value)
    elif isinstance(schema, list):
        for value in schema:
            _hide_account_field(value)


def _simplify_schema_annotations(node):
    """Compact equivalent schema annotations without traversing user data.

    This changes only the model-facing copy. Runtime argument validation keeps
    using the original tool models and defaults.
    """
    if not isinstance(node, dict):
        return
    if node.get("default", object()) is None:
        del node["default"]
    branches = node.get("anyOf")
    if (isinstance(branches, list) and len(branches) == 2 and "type" not in node
            and all(isinstance(branch, dict) and set(branch) == {"type"}
                    and isinstance(branch["type"], str) for branch in branches)
            and len({branch["type"] for branch in branches}) == 2
            and "null" in {branch["type"] for branch in branches}):
        node["type"] = [branch["type"] for branch in branches]
        del node["anyOf"]
    for key in ("$defs", "definitions"):
        definitions = node.get(key)
        if isinstance(definitions, dict):
            for name, definition in definitions.items():
                if isinstance(definition, dict) and definition.get("title") == name:
                    del definition["title"]
    for key in ("$defs", "definitions", "properties", "patternProperties", "dependentSchemas"):
        children = node.get(key)
        if isinstance(children, dict):
            for child in children.values():
                _simplify_schema_annotations(child)
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        children = node.get(key)
        if isinstance(children, list):
            for child in children:
                _simplify_schema_annotations(child)
    for key in ("items", "additionalProperties", "contains", "not", "if", "then", "else", "propertyNames"):
        _simplify_schema_annotations(node.get(key))


def model_tool_schemas(schemas, context):
    allowed = context.allowed_tools
    result = deepcopy([schema for schema in schemas
                       if allowed is None or schema["function"]["name"] in allowed])
    for schema in result:
        function = schema["function"]
        name = function["name"]
        if name == "manage_material":
            function["parameters"]["properties"]["action"]["enum"] = ["list", "get", "import_link", "import_status", "analyze", "analysis", "analysis_status"]
            function["description"] = "管理当前账号 BGM：list/get 查询；import_link 后台导入分享原文并分析，import_status 查看；analyze 后台分析已有音频，analysis_status 查询状态，analysis 获取摘要。后台任务运行时不要反复查询。"
        if context.archive_only_reads and name == "read_file":
            function["description"] = "读取当前会话的工具结果归档文本；查找未知片段先传 search 精确搜索，按返回 offset 再读取上下文；complete=false 时按 next_offset 继续。不能读取项目文件、Skill 文件或其他会话。"
            function["parameters"]["properties"]["path"]["description"] = "当前会话归档结果中提供的路径。"
        if context.creator_id and name in ACCOUNT_BOUND_TOOLS:
            parameters = function["parameters"]
            _hide_account_field(parameters)
            function["description"] = {
                "manage_media_collection": function["description"],
                "manage_skill_library": function["description"],
                "manage_material": function["description"],
                "list_creator_series": "查询当前绑定账号的栏目；已有当前账号目录且栏目未变化时复用，不为查一个已知栏目重列。账号由宿主确定。",
                "query_feedback": "只读当前账号最近 7 天已发布笔记的本地反馈，按指标比较、分页，返回栏目与采样时间；不刷新平台。账号由宿主绑定。",
                "get_creator_tasks": "查询当前账号任务；查一个选题优先用已知topic_id/run_id，不遍历全账号；确需目录时按栏目和状态筛选。已有结果覆盖本次问题时复用，问最新进展时再查。包括尚无Run的waiting制作与needs_attention；返回原选题/素材集入口，不恢复或重新提交。",
                "compose_series": "用户明确要求时为当前账号创建栏目，可仅填名称，之后配置常用池/阶段默认；也兼容旧完整Skill或Mind＋Visualize组合。账号由宿主绑定。",
            }[name]
        _strip_redundant_property_titles(function.get("parameters"))
        if context.creator_id and context.archive_only_reads:
            _simplify_schema_annotations(function.get("parameters"))
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
