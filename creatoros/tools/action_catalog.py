"""Candidate action catalog. Does not change registered tools or dispatch.

IDs describe a canonical tool and, where its public schema has an action,
one action. Tool and action names are taken from the actual visible schemas;
description/routing metadata is deliberately curated, not sliced from prose.
"""
from copy import deepcopy


TOOL_LABELS = {
    "query_feedback": "读取账号近7天已发布笔记的本地反馈，不刷新平台。",
    "prepare_publication": "准备已有图文的发布预览，不发布。",
    "get_publication": "查询发布单或核对平台结果，不重新发送。",
    "read_file": "搜索、读取当前会话的工具归档文件。",
    "read_tool_result": "按引用分页读取当前会话历史工具结果。",
    "list_creators": "查询可见运营账号目录。",
    "research_series_topics": "明确要求联网或最新外部来源时调研选题。",
    "get_topic_research": "查看同一选题调研批次及候选状态。",
    "prepare_topic_selection": "把选中的调研候选准备成人工确认预览，不入队。",
    "compose_series": "用户明确要求时为当前账号创建栏目。",
    "update_series_composition": "修改现有栏目名称或技能绑定。",
    "list_producer_skills": "查询已安装Skill及可见版本，不读正文。",
    "get_producer_skill": "读取指定Skill正文或文件树及版本摘要。",
    "update_producer_skill_file": "用户要求时编辑Skill正文或参考图。",
    "list_creator_series": "读取当前账号栏目目录。",
    "get_content_run": "查询指定作品Run进度和版本。",
    "discuss_content_run": "对指定作品版本创建只读讨论，不返工。",
    "get_content_discussion": "读取指定作品讨论及任务状态。",
    "request_content_revision": "明确要求返工时创建revision，不执行生产。",
    "get_creator_tasks": "查询账号任务或指定选题任务状态。",
    "read_image": "查看本会话附件、账号Skill参考图或产物版本图片。",
    "extract_skill_from_images": "按用户要求从聊天图片提炼Skill草稿。",
    "create_skill_draft": "把用户现成提示词原样保存为待检查Skill草稿。",
    "import_content_images": "明确为成品的图片导入待人工验收内容，不生产。",
    "get_skill_extraction": "查询Skill提炼任务或历史，不连续轮询。",
    "save_extracted_skills": "用户确认后将Skill草稿加入库，不生产。",
    "cancel_skill_extraction": "用户要求时取消指定提炼任务。",
    "edit_extracted_skills": "按当前digest编辑尚未入库的Skill草稿。",
}


# The short label is for discovery only. The description is shown on first
# discovery and contains operation-specific consequences and prohibitions.
ACTION_INFO = {
    "manage_production_task": {
        "list": ("查看选题的制作任务", "只读列出指定选题制作任务；不停止或移除。"),
        "stop": ("停止指定制作任务", "仅用户明确要求时停止真实task_id；停止请求受理不等于任务已退出。"),
        "remove": ("移除终态制作任务", "仅用户明确要求时移除终态任务，保留产物和历史。"),
    },
    "manage_media_collection": {
        "list": ("查询账号素材集", "按名称筛选账号素材集；include_sources可取任务索引，不读取所有图片。"),
        "get": ("读取素材集或导出回执", "读取指定素材集或已有export_id回执，不重新导出。"),
        "create": ("创建素材集", "用户明确要求时创建素材集，可关联选题及额外Run；不生产、不发布。"),
        "update": ("修改素材集", "使用用户看到的expected_version修改名称、来源、顺序或排除项；不删除原图。"),
        "remove": ("删除素材集", "按用户要求用所见expected_version删除集合，保留原图。"),
        "export": ("导出素材集ZIP", "导出当前选片ZIP；使用所见快照时同时提供version和digest；未知先查询，不另起生产。"),
    },
    "manage_plan": {
        "create": ("创建多步骤执行计划", "提交步骤及依赖；需等待生产后核验或汇报时设置生产步骤wait_for_completion，不将受理当完成。仅需等待汇报不加request_review；需根据结果决定后续动作时添加并说明问题；依赖结果用{$ref:'步骤ID.字段'}。"),
        "list": ("列出当前对话计划", "只读查询本会话的计划目录。"),
        "get": ("读取计划及执行状态", "读取当前账号本会话指定计划，不修改或重试。"),
        "update": ("增量修改计划步骤", "先get当前版本；只传新增或修改的完整步骤，其他保留；明确删除用remove_step_ids。未知生产先核对，不重提。"),
        "pause": ("暂停执行计划", "按用户要求用当前expected_version暂停计划，不取消已提交任务。"),
        "resume": ("恢复暂停计划", "按用户要求用当前expected_version恢复计划；未知结果先核对。"),
        "retry": ("重试可重试计划步骤", "按用户要求先核对当前计划与步骤，用当前版本处理；未知或已提交生产不得盲目重提。"),
        "skip": ("跳过计划步骤", "仅跳过可安全跳过的无副作用步骤；必须使用当前计划版本与步骤ID。"),
    },
    "manage_topics": {
        "list": ("分页列出栏目选题", "只读列出栏目选题。已有完整未变化列表时复用；问最新或发生变化重新查，未读完继续翻页。"),
        "get": ("读取选题完整正文与回执", "按原topic_id读取完整正文；用户要全文时逐字呈现。制作结果未知时使用原production_request_id只读核对。"),
        "create": ("创建或入队选题", "仅用户明确新增时创建或入队，保留来源reference_id；不生产。"),
        "update": ("修改选题标题或完整场景", "先读取原选题；用topic_id和changes修改。唯一可信已读版本由宿主绑定；多个版本需确认或显式给expected_digest。冲突重读，不新建替代。"),
        "remove": ("移除选题或待选候选", "仅按用户要求移除；待选候选附真实batch_id/candidate_id，保留旧产物；运行中拒绝。"),
    },
    "manage_skill_library": {
        "get": ("读取账号Skill库或栏目池", "读取账号Skill库；提供series_id则读取该栏目池。已有完整未变化目录时复用。"),
        "add": ("添加Skill到账号库", "用户明确要求时引用可用Skill加入当前账号库；不绑定栏目、不生产。"),
        "remove": ("从账号库移除Skill", "用户明确要求时用所见expected_version移除Skill；不删除共享原件。"),
        "set_pool": ("设置栏目的Skill池", "用户明确要求时以所见expected_version提交栏目Skill池；不生产。"),
    },
    "manage_material": {
        "list": ("列出账号音乐素材", "只读分页列出当前账号BGM素材。"),
        "get": ("读取指定音乐素材", "只读获取当前账号指定material_id。"),
        "import_link": ("导入音乐分享链接", "用户要求导入时提交分享原文；后台任务运行时不要反复查询。"),
        "import_status": ("查询音乐导入状态", "使用原import_id查询当前账号音乐导入状态，不重复导入。"),
        "analyze": ("分析账号已有音乐", "分析已有material_id，可选片段；仅明确要求重新分析时force=true。"),
        "analysis": ("读取音乐分析结果", "只读获取material_id的音乐分析摘要，不重新分析。"),
        "analysis_status": ("查看音乐分析状态", "查询已有material_id分析状态，不反复轮询。"),
    },
    "manage_skill_iteration": {
        "start": ("启动Skill循环迭代", "用户明确要求循环迭代时，使用真实栏目、Skill版本及已入队选题启动；默认3轮含基线。"),
        "get": ("查询Skill迭代任务", "用真实iteration_id查询任务，不传expected_revision，不反复轮询。"),
        "pause": ("暂停Skill迭代", "用户要求时使用最近get的任务expected_revision暂停。"),
        "resume": ("恢复Skill迭代", "用户要求时使用最近get的任务expected_revision恢复。"),
        "continue": ("继续Skill迭代", "用户明确要求时使用最近get的任务expected_revision继续，沿用原输入及预算。"),
    },
    "start_content_run": {
        "start": ("按栏目默认生产选题", "用户明确要求生产且仅需提交时，用topic_ids按栏目默认生产；需完成后核验/继续/汇报时通过计划等待。受理不等于完成，未知不重提。"),
        "tasks": ("按任务拆分生产选题", "用户明确要求时用topic_id和tasks拆分生产，每项明确kind和brief；需完成后继续时使用计划等待。未知不重提。"),
        "fresh": ("按原Run清空上下文重做", "用户明确要求清空旧上下文重新制作时提供source_run_id及所见expected_version；不可把结果未知当作未提交。"),
        "reference": ("参考会话附件直接制作", "用户明确要求按参考制作时提供series_id、title、当前会话attachment_ids及完整要求；不改栏目Skill绑定，受理不代表完成。"),
    },
}


# Tighten flat action schemas only where the canonical Pydantic validator has a
# complete required/optional set for each action. Unlisted flat actions keep
# their full fields; this avoids silently losing valid parameters.
FLAT_FIELDS = {
    "manage_topics": {
        "list": ({"series_id"}, {"query"}), "get": ({"topic_id"}, {"production_request_id"}),
        "create": ({"series_id", "topics"}, set()), "update": ({"topic_id", "changes", "expected_digest"}, set()),
        "remove": ({"topic_id"}, {"candidate"}),
    },
    "manage_media_collection": {
        "list": (set(), {"name", "include_sources", "offset", "limit"}),
        "get": ({"collection_id"}, {"export_id", "offset", "limit"}),
        "create": ({"name"}, {"topic_id", "run_ids"}),
        "update": ({"collection_id", "expected_version", "changes"}, set()),
        "remove": ({"collection_id", "expected_version"}, set()),
        "export": ({"collection_id"}, {"expected_version", "snapshot_digest"}),
    },
    "manage_production_task": {
        "list": ({"topic_id"}, set()), "stop": ({"topic_id", "task_id"}, set()),
        "remove": ({"topic_id", "task_id"}, set()),
    },
}


def _refs(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/"):
                yield item.rsplit("/", 1)[-1]
            yield from _refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _refs(item)


def _prune_defs(schema, original_defs):
    needed = set(_refs(schema))
    todo = list(needed)
    while todo:
        name = todo.pop()
        if name not in original_defs:
            raise ValueError(f"missing schema definition: {name}")
        for ref in _refs(original_defs[name]):
            if ref not in needed:
                needed.add(ref)
                todo.append(ref)
    if needed:
        schema["$defs"] = {name: deepcopy(original_defs[name]) for name in original_defs if name in needed}
    else:
        schema.pop("$defs", None)


def _action_schema(parameters, name, action):
    original = deepcopy(parameters)
    mapping = original.get("discriminator", {}).get("mapping", {})
    if action in mapping:
        ref = mapping[action].rsplit("/", 1)[-1]
        definitions = original.get("$defs", {})
        result = deepcopy(definitions[ref])
        prop = result.get("properties", {}).get("action")
        if not isinstance(prop, dict):
            raise ValueError("action branch lacks action property")
        prop.pop("enum", None)
        prop["const"] = action
        _prune_defs(result, definitions)
        return result
    result = deepcopy(original)
    props = result.get("properties", {})
    if action not in props.get("action", {}).get("enum", []):
        raise ValueError("unknown flat action")
    props["action"].pop("enum", None)
    props["action"]["const"] = action
    if name in FLAT_FIELDS:
        required, optional = FLAT_FIELDS[name][action]
        # A non-account scope may retain a canonical creator_id parameter;
        # account-bound schemas hide it before the catalog is built.
        inherited_required = set(result.get("required", [])) - {"action"}
        allowed = {"action"} | required | optional | inherited_required
        if "creator_id" in props:
            allowed.add("creator_id")
        result["properties"] = {key: value for key, value in props.items() if key in allowed}
        result["required"] = [key for key in props if key in required or key in inherited_required or key == "action"]
        if name == "manage_topics" and action == "update":
            # Host may fill an omitted CAS digest only from one trusted,
            # model-visible scoped read. Explicit older digests remain valid
            # inputs and the original business parser/CAS remains authoritative.
            result["required"] = [key for key in result["required"] if key != "expected_digest"]
            if isinstance(result["properties"].get("expected_digest"), dict):
                result["properties"]["expected_digest"]["description"] = (
                    "可选：用户明确指定的已读版本摘要；不传时宿主仅在唯一可信读取时绑定。")
        definitions = result.pop("$defs", {})
        _prune_defs(result, definitions)
    return result


def build_action_catalog(schemas):
    """Build stable visible actions; reject missing or extra curated metadata."""
    catalog = {}
    for source in schemas:
        function = source["function"]
        name = function["name"]
        params = function["parameters"]
        mapping = params.get("discriminator", {}).get("mapping", {})
        enum = params.get("properties", {}).get("action", {}).get("enum", [])
        actions = list(mapping) if mapping else list(enum)
        if actions:
            if name not in ACTION_INFO or set(actions) != set(ACTION_INFO[name]):
                raise ValueError(f"unmapped action set for {name}")
            for action in actions:
                action_id = f"{name}.{action}"
                label, detail = ACTION_INFO[name][action]
                schema = {"type": "function", "function": {"name": name, "description": detail,
                          "parameters": _action_schema(params, name, action)}}
                catalog[action_id] = {"id": action_id, "name": name, "action": action,
                                      "label": label, "schema": schema}
        else:
            if name not in TOOL_LABELS:
                raise ValueError(f"unmapped tool {name}")
            catalog[name] = {"id": name, "name": name, "action": None,
                             "label": TOOL_LABELS[name], "schema": deepcopy(source)}
    return catalog


# These aliases only expose canonical reads. Scope is inherited from the
# caller-provided catalog, which is built from already-filtered model schemas.
DIRECT_READ_ACTIONS = {"read_topic": "manage_topics.get", "list_topics": "manage_topics.list"}


def build_direct_read_schemas(catalog):
    """Return compact read-only aliases plus the original task read if allowed."""
    result = []
    for alias, action_id in DIRECT_READ_ACTIONS.items():
        entry = catalog.get(action_id)
        if entry is None:
            continue
        schema = deepcopy(entry["schema"])
        schema["function"]["name"] = alias
        parameters = schema["function"]["parameters"]
        parameters.get("properties", {}).pop("action", None)
        parameters["required"] = [key for key in parameters.get("required", []) if key != "action"]
        result.append(schema)
    if "get_creator_tasks" in catalog:
        result.append(deepcopy(catalog["get_creator_tasks"]["schema"]))
    return result


def resolve_direct_read(name, arguments, catalog):
    """Resolve an allowed alias; reject any attempt to set its fixed action.

    This function only constructs canonical arguments. The caller must pass
    them through the existing account binder, parser and business executor.
    """
    action_id = DIRECT_READ_ACTIONS.get(name)
    if action_id is None or action_id not in catalog:
        raise ValueError("unknown or unavailable read alias")
    if not isinstance(arguments, dict) or "action" in arguments:
        raise ValueError("read alias does not accept an action")
    entry = catalog[action_id]
    return entry["name"], {**deepcopy(arguments), "action": entry["action"]}
