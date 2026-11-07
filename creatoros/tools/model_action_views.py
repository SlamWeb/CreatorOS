"""Curated model-facing action schemas, separate from backend validation.

The catalog is built from the already scoped business schemas.  This module
only changes what a model sees; the gateway still maps to the registered tool
and validates its arguments using the original binder and parser.
"""
from copy import deepcopy

from creatoros.plan_capabilities import PLAN_TOOL_NAMES


V1 = "v1"
V2 = "v2"


def _string(description):
    return {"type": "string", "description": description}


def _iteration_control(description):
    return {
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "iteration_id": _string("要操作的迭代任务 ID"),
                "expected_revision": {"type": "integer", "description": "最近读取到的该迭代任务 revision"},
            },
            "required": ["iteration_id", "expected_revision"],
        },
    }


_MEDIA_FIELDS = {
    "collection_id": _string("目标素材集 ID"),
    "name": _string("list 筛选名称；create 新素材集名称"),
    "topic_id": _string("create 自动收集该选题所有 Run 与版本"),
    "run_ids": {"type": "array", "items": {"type": "string"},
                "description": "create 的额外来源 Run，独立于选题自动收集"},
    "changes": {
        "type": "object",
        "description": "update 修改至少一项；列表整体替换，空数组清空",
        "properties": {
            "name": _string("新名称"),
            "run_ids": {"type": "array", "items": {"type": "string"},
                        "description": "完整额外 Run 列表，不删除原图"},
            "order_ids": {"type": "array", "items": {"type": "string"},
                          "description": "优先排序图片 ID；可只列部分，其余保留"},
            "excluded_ids": {"type": "array", "items": {"type": "string"},
                             "description": "完整排除图片 ID 列表"},
        },
    },
    "expected_version": {"type": "integer", "description": "update/remove 已读版本；export 与摘要同时提供或都省略"},
    "snapshot_digest": _string("export 的已读快照摘要"),
    "export_id": _string("get 读取已有 ZIP 回执，不重新打包"),
    "include_sources": {"type": "boolean", "description": "list 附可加入的任务索引；默认 false"},
    "offset": {"type": "integer", "description": "list/get 的分页起点；默认 0"},
    "limit": {"type": "integer", "description": "list/get 的分页数量；默认 20"},
}


def _media_collection(description, fields, required=()):
    return {
        "description": description,
        "parameters": {"type": "object", "properties": {name: deepcopy(_MEDIA_FIELDS[name]) for name in fields},
                       "required": list(required)},
    }


# Action identity and dispatch come from the original scoped catalog entry,
# not this model-facing schema.
_V2_ACTIONS = {
    "manage_skill_library.get": {
        "description": "读取当前账号 Skill 库；可指定栏目读取栏目池。",
        "parameters": {"type": "object", "properties": {
            "series_id": _string("栏目 ID；省略读取账号库")}, "required": []},
    },
    "manage_skill_library.add": {
        "description": "将可用 Skill 引用加入账号库。",
        "parameters": {"type": "object", "properties": {
            "skill_id": _string("要加入或移除的 Skill ID")}, "required": ["skill_id"]},
    },
    "manage_skill_library.remove": {
        "description": "从账号库移除 Skill；仍被栏目绑定时不能移除。",
        "parameters": {"type": "object", "properties": {
            "skill_id": _string("要加入或移除的 Skill ID"),
            "expected_version": {"type": "integer", "description": "最近读取的账号库版本"}},
            "required": ["skill_id", "expected_version"]},
    },
    "manage_skill_library.set_pool": {
        "description": "用完整 Skill 列表替换栏目池；空列表清空，仍有效的旧默认由宿主保留。",
        "parameters": {"type": "object", "properties": {
            "series_id": _string("栏目 ID；省略读取账号库"),
            "expected_version": {"type": "integer", "description": "最近读取的栏目池版本"},
            "skill_ids": {"type": "array", "items": {"type": "string"},
                          "description": "完整栏目池 Skill ID 列表；空列表清空"}},
            "required": ["series_id", "expected_version", "skill_ids"]},
    },
    "manage_media_collection.list": _media_collection(
        "列出当前账号的素材集，可按名称筛选。", ("name", "include_sources", "offset", "limit")),
    "manage_media_collection.get": _media_collection(
        "查看素材集或读取已有导出回执。", ("collection_id", "export_id", "offset", "limit"), ("collection_id",)),
    "manage_media_collection.create": _media_collection(
        "按用户要求创建素材集；选题来源自动收集，Run 为额外来源。", ("name", "topic_id", "run_ids"), ("name",)),
    "manage_media_collection.update": _media_collection(
        "按用户要求用已读版本修改素材集。", ("collection_id", "expected_version", "changes"),
        ("collection_id", "expected_version", "changes")),
    "manage_media_collection.remove": _media_collection(
        "按用户要求用已读版本移除素材集，保留原图。", ("collection_id", "expected_version"),
        ("collection_id", "expected_version")),
    "manage_media_collection.export": _media_collection(
        "导出选片 ZIP；版本和摘要同时提供或同时省略。", ("collection_id", "expected_version", "snapshot_digest"),
        ("collection_id",)),
    "manage_skill_iteration.pause": _iteration_control(
        "用户明确要求时暂停进行中的迭代；已提交试产不取消。先读取该任务取得当前 revision。"),
    "manage_skill_iteration.resume": _iteration_control(
        "用户明确要求时恢复暂停、中断或可恢复的失败阶段；沿用冻结输入和预算，不增加轮数。先读取该任务取得当前 revision。"),
    "manage_skill_iteration.continue": _iteration_control(
        "用户明确要求时继续耗尽或失败的迭代；可增加最多3轮，总上限30轮；部分可恢复的失败先恢复当前阶段，不增加轮数。先读取该任务取得当前 revision。"),
    "manage_skill_iteration.start": {
        "description": "仅用户明确要求循环迭代或本轮开启 RSI 时启动。任务会在后台评审和试产；受理不等于完成，不自动发布或切换栏目。",
        "parameters": {
            "type": "object",
            "properties": {
                "series_id": _string("目标栏目 ID"),
                "skill_id": _string("栏目绑定家族中本次使用的精确 Skill 版本 ID"),
                "topic_id": _string("该栏目的已入队选题 ID"),
                "instructions": _string("本次迭代要求；省略为空"),
                "reference_mode": {"type": "string", "enum": ["skill_defaults", "attachments_only", "selected_skill_images"],
                                   "description": "skill_defaults 用 Skill 全部参考图及所选附件；attachments_only 仅用所选附件；selected_skill_images 用所选 Skill 图及附件"},
                "attachment_ids": {"type": "array", "items": {"type": "string"},
                                   "description": "本会话用户指定的参考图片附件 ID，最多 6 个"},
                "reference_paths": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}],
                                    "description": "selected_skill_images 必填非空路径；skill_defaults 不传或传 null；attachments_only 不传或传空列表"},
                "max_rounds": {"type": "integer", "description": "首次迭代 1–3 轮，默认 3 轮，含基线"},
            },
            "required": ["series_id", "skill_id", "topic_id", "reference_mode"],
        },
    },
    "manage_skill_iteration.get": {
        "description": "查询已有 Skill 迭代任务的状态和 revision；运行中后台继续，不连续查询，不控制或启动任务。",
        "parameters": {
            "type": "object",
            "properties": {"iteration_id": _string("要查询的迭代任务 ID")},
            "required": ["iteration_id"],
        },
    },
    "manage_topics.get": {
        "description": "读取指定选题的完整正文；用户要全文时逐字呈现，包括重复段落和结尾。核对未知制作时可提供原请求 ID。",
        "parameters": {
            "type": "object",
            "properties": {
                "topic_id": _string("要读取的选题 ID"),
                "production_request_id": _string("需要核对的原制作请求 ID；一般省略"),
            },
            "required": ["topic_id"],
        },
    },
    "manage_topics.list": {
        "description": "按栏目列出选题。未变化的完整列表可复用；查最新或有变化时重新查，未读完继续分页。",
        "parameters": {
            "type": "object",
            "properties": {
                "series_id": _string("目标栏目 ID"),
                "query": {
                    "type": "object",
                    "description": "可选的状态筛选与分页",
                    "properties": {
                        "state": {"type": "string", "enum": ["all", "pending", "queued"],
                                  "description": "all 全部、pending 待选、queued 已入队；默认 all"},
                        "offset": {"type": "integer", "description": "起始位置；默认 0"},
                        "limit": {"type": "integer", "description": "每页数量；默认 20"},
                    },
                },
            },
            "required": ["series_id"],
        },
    },
    "manage_topics.create": {
        "description": "用户明确要求新增或将指定候选入队时使用；不生产。调研候选保留原来源。",
        "parameters": {
            "type": "object",
            "properties": {
                "series_id": _string("目标栏目 ID"),
                "topics": {
                    "type": "array",
                    "description": "按用户选定顺序提供的选题",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": _string("选题标题"),
                            "brief": _string("选题切入点；空字符串原样保留"),
                            "source": {"type": "string", "enum": ["manual", "research"],
                                       "description": "手动 manual、调研候选 research；默认 manual"},
                            "reference_id": _string("选中候选返回的原 reference_id；没有则省略"),
                        },
                        "required": ["title"],
                    },
                },
            },
            "required": ["series_id", "topics"],
        },
    },
    "manage_topics.update": {
        "description": "先读取原选题，再修改标题或完整切入点；未指定的字段不变，不改变已有作品。",
        "parameters": {
            "type": "object",
            "properties": {
                "topic_id": _string("要修改的选题 ID"),
                "changes": {
                    "type": "object",
                    "properties": {
                        "title": _string("新标题；不修改则省略"),
                        "brief": _string("完整切入点；空字符串清空，不修改则省略"),
                    },
                },
                "expected_digest": _string("明确选择已读版本时填写；通常省略，由宿主绑定唯一可信读取"),
            },
            "required": ["topic_id", "changes"],
        },
    },
    "manage_topics.remove": {
        "description": "仅用户明确要求时移除选题；已有作品保留。待选项需提供原候选来源，已入队不传。",
        "parameters": {
            "type": "object",
            "properties": {
                "topic_id": _string("要移除的选题 ID"),
                "candidate": {
                    "type": "object",
                    "description": "仅待选项：使用列表中返回的真实调研来源",
                    "properties": {
                        "batch_id": _string("调研批次 ID"),
                        "candidate_id": _string("候选 ID"),
                    },
                    "required": ["batch_id", "candidate_id"],
                },
            },
            "required": ["topic_id"],
        },
    },
    "manage_plan.list": {
        "description": "只读列出当前会话的计划及状态。",
        "parameters": {"type": "object", "properties": {}},
    },
    "manage_plan.get": {
        "description": "只读查看当前会话中指定计划的步骤、状态和版本。",
        "parameters": {
            "type": "object",
            "properties": {"plan_id": _string("要查看的计划 ID")},
            "required": ["plan_id"],
        },
    },
    "manage_plan.create": {
        "description": "创建并执行计划。仅等待完成并汇报时用 wait_for_completion，不加 request_review；需要根据结果判断后续时才加检查点。",
        "parameters": {
            "type": "object",
            "properties": {
                "title": _string("计划目标"),
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": _string("计划内唯一的步骤 ID"),
                            "title": _string("步骤目标"),
                            "tool": {"type": "string", "enum": list(PLAN_TOOL_NAMES),
                                     "description": "要执行的工具；request_review 是判断检查点"},
                            "arguments": {"type": "object", "description": "工具参数；生产步骤填 action，tasks 的每项填 kind，仅用 skill_ids。依赖结果可用 {\"$ref\":\"步骤ID.字段\"}；request_review 填 question。"},
                            "depends_on": {"type": "array", "items": {"type": "string"},
                                           "description": "本步骤依赖的前面步骤 ID"},
                            "wait_for_completion": {"type": "boolean",
                                                    "description": "仅生产步骤：等待真实终态后继续"},
                        },
                        "required": ["id", "title", "tool", "arguments"],
                    },
                },
            },
            "required": ["title", "steps"],
        },
    },
}

# Reuse the same step contract for creating and incrementally editing plans.
_V2_ACTIONS["manage_plan.update"] = {
    "description": "先读取目标计划，再增量修改：同 ID 替换完整步骤，新 ID 追加，未提及的步骤保留；仅明确要求删除时填 remove_step_ids。",
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": _string("要修改的计划 ID"),
            "steps": deepcopy(_V2_ACTIONS["manage_plan.create"]["parameters"]["properties"]["steps"]),
            "remove_step_ids": {"type": "array", "items": {"type": "string"},
                                "description": "明确删除的未开始步骤 ID"},
            "expected_version": {"type": "integer", "description": "明确选择已读版本时填写；通常省略，由宿主绑定唯一可信读取"},
        },
        "required": ["plan_id"],
    },
}

_V2_ACTIONS["manage_plan.pause"] = {
    "description": "按用户要求暂停运行中计划；先读取，不取消已提交任务。",
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": _string("计划 ID"),
            "expected_version": {"type": "integer", "description": "通常省略；选择已读版本时填写"},
        },
        "required": ["plan_id"],
    },
}

_V2_ACTIONS["manage_plan.resume"] = {
    "description": "按用户要求或原计划续接恢复暂停的计划；先读取，结果未知先核对。",
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": _string("计划 ID"),
            "expected_version": {"type": "integer", "description": "通常省略；选择已读版本时填写"},
        },
        "required": ["plan_id"],
    },
}

_V2_ACTIONS["manage_plan.skip"] = {
    "description": "先读计划，仅跳过 skippable 的失败步骤；未知或已提交生产不可跳，依赖需另行调整。",
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": _string("计划 ID"),
            "step_id": _string("失败步骤 ID"),
            "expected_version": {"type": "integer", "description": "通常省略；选择已读版本时填写"},
        },
        "required": ["plan_id", "step_id"],
    },
}

_V2_ACTIONS["manage_plan.retry"] = {
    "description": "先读计划；仅重试 retryable 的失败步骤。生产仅派发前参数错误可重试，未知或已提交不可重提。",
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": _string("计划 ID"),
            "step_id": _string("失败步骤 ID"),
            "expected_version": {"type": "integer", "description": "通常省略；选择已读版本时填写"},
        },
        "required": ["plan_id", "step_id"],
    },
}

_V2_ACTIONS["start_content_run.tasks"] = {
    "description": "用户明确要求时，为同一选题提交独立图片或视频任务；每张独立图片各一项。只需提交时使用；要求完成后核验或汇报则用计划等待。受理不等于完成。",
    "parameters": {
        "type": "object",
        "properties": {
            "topic_id": _string("目标选题 ID"),
            "instructions": _string("本批所有任务共有的要求；宿主会合并进每项 brief"),
            "tasks": {
                "type": "array",
                "description": "每项是一个独立制作任务；共同要求放 instructions，brief 写本项自身的完整要求。",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["image", "video"],
                                 "description": "image 图片、video 视频"},
                        "title": _string("本项名称"),
                        "brief": _string("本项自身的完整制作要求；共同要求放在 instructions"),
                        "skill_ids": {"type": "array", "items": {"type": "string"},
                                      "description": "本项使用的精确 Skill 版本 ID；可选多项，省略沿用栏目阶段默认"},
                        "inputs": {
                            "type": "object",
                            "description": "仅 video 必填：素材集和 BGM；素材 ID 与分享原文二选一。",
                            "properties": {
                                "collection_id": _string("视频使用的素材集 ID"),
                                "export_id": _string("选用的素材集导出 ID；不指定则省略"),
                                "bgm_material_id": _string("已有音频或视频素材 ID；与 bgm_reference 二选一"),
                                "bgm_reference": _string("用户提供的 BGM 分享原文或链接；与 bgm_material_id 二选一，无需预解析或先导入"),
                            },
                            "required": ["collection_id"],
                        },
                        "depends_on": {"type": "array", "items": {"type": "string"},
                                       "description": "仅 video：用户选中的本选题生图任务 ID，后台等待这些任务完成"},
                    },
                    "required": ["kind", "title", "brief"],
                },
            },
        },
        "required": ["topic_id", "tasks"],
    },
}

_V2_ACTIONS["start_content_run.start"] = {
    "description": "用户明确要求时，按栏目默认 Skill 提交指定选题生产；逐项核对受理或拒绝，受理不等于完成。需要完成后核验或汇报时用计划等待。",
    "parameters": {
        "type": "object",
        "properties": {
            "topic_ids": {"type": "array", "items": {"type": "string"},
                          "description": "用户指定的选题 ID；单个选题也用列表"},
        },
        "required": ["topic_ids"],
    },
}

_V2_ACTIONS["start_content_run.reference"] = {
    "description": "用户明确要求根据参考图片制作时使用；默认只借鉴内容，不自动修改栏目 Skill。受理不等于完成；需要完成后核验或汇报时用计划等待。",
    "parameters": {
        "type": "object",
        "properties": {
            "series_id": _string("目标栏目 ID"),
            "title": _string("本次选题的简短展示标题"),
            "attachment_ids": {"type": "array", "items": {"type": "string"},
                               "description": "当前会话参考图片的真实附件 ID，可多张"},
            "instructions": _string("参考制作的完整要求"),
            "reference_presentation": {"type": "boolean",
                                       "description": "仅用户明确要求借鉴画面风格或版式时设 true；默认 false"},
            "skill_ids": {"type": "array", "items": {"type": "string"},
                          "description": "本次使用的精确 Skill 版本 ID，可选一项或多项；省略沿用栏目默认，不更改绑定"},
        },
        "required": ["series_id", "title", "attachment_ids"],
    },
}

_V2_ACTIONS["start_content_run.fresh"] = {
    "description": "用户明确要求清空旧上下文从头重做时使用；先读取指定来源 Run，不改旧作品。受理不等于完成，需要完成后核验或汇报时用计划等待。",
    "parameters": {
        "type": "object",
        "properties": {
            "source_run_id": _string("已读取的来源 Run ID"),
            "expected_version": {"type": "integer", "description": "读取来源 Run 时取得的版本"},
            "skill_ids": {"type": "array", "items": {"type": "string"},
                          "description": "本次使用的精确 Skill 版本 ID，可选一项或多项；省略沿用现有重做规则"},
        },
        "required": ["source_run_id", "expected_version"],
    },
}

_V2_ACTIONLESS = {
    "cancel_skill_extraction": {
        "description": "按用户要求取消指定 Skill 提炼任务；取消请求不等于任务已停止，不影响其他任务。",
        "parameters": {
            "type": "object",
            "properties": {"job_id": _string("待取消的提炼任务 ID")},
            "required": ["job_id"],
        },
    },
    "list_creator_series": {
        "description": "查询当前账号栏目；已有未变化的目录可复用。",
        "parameters": {"type": "object", "properties": {}},
    },
    "list_creators": {
        "description": "分页查询可见的 Studio 运营账号。",
        "parameters": {
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "description": "分页起点，默认 0"},
                "limit": {"type": "integer", "description": "每页数量，默认 20，最多 100"},
            },
        },
    },
    "get_content_discussion": {
        "description": "读取 Run 的讨论；有活跃任务时优先返回或观察该任务，否则返回历史。不新建讨论。",
        "parameters": {
            "type": "object",
            "properties": {"run_id": _string("已有作品 Run ID")},
            "required": ["run_id"],
        },
    },
    "get_content_run": {
        "description": ("查询作品 Run 状态、链接和版本目录（revision_id、artifact_digest）。"
                        "awaiting_approval 是待验收，approved 不代表已发布。"),
        "parameters": {
            "type": "object",
            "properties": {"run_id": _string("已有作品 Run ID")},
            "required": ["run_id"],
        },
    },
    "read_image": {
        "description": "读取图片供模型查看。三种来源只能选一种：当前会话附件；当前账号 Skill 图片；指定作品版本图片。不猜图片内容或版本，不接受任意本地路径。",
        "parameters": {
            "type": "object",
            "properties": {
                "attachment_id": _string("当前会话图片附件 ID"),
                "skill_id": _string("当前账号可访问的 Skill ID"),
                "path": _string("Skill 内图片相对路径"),
                "run_id": _string("作品 Run ID"),
                "revision_id": _string("作品版本 revision ID"),
                "image_index": {"type": "integer", "description": "该版本图片序号，从 1 开始"},
            },
            "oneOf": [
                {"required": ["attachment_id"]},
                {"required": ["skill_id", "path"]},
                {"required": ["run_id", "revision_id", "image_index"]},
            ],
        },
    },
    "get_skill_extraction": {
        "description": ("查询当前会话的提炼任务；运行中给进度和入口，不连续轮询。"
                        "ready 是待确认草稿，operation 非空仍在处理；saved 才是已入库。"),
        "parameters": {
            "type": "object",
            "properties": {"job_id": _string("当前会话可访问的提炼任务 ID")},
            "required": ["job_id"],
        },
    },
    "import_content_images": {
        "description": "仅按用户明确要求导入已有成品图片，创建待人工验收内容（awaiting_approval）；不生图、不自动批准或发布。",
        "parameters": {
            "type": "object",
            "properties": {
                "series_id": _string("当前账号的目标栏目 ID"),
                "attachment_ids": {"type": "array", "items": {"type": "string"},
                                   "description": "当前会话的 1–20 张不同成品图片，按作品顺序排列，首张为封面"},
                "title": _string("作品标题"),
                "body": _string("作品正文；可省略"),
            },
            "required": ["series_id", "attachment_ids", "title"],
        },
    },
    "read_tool_result": {
        "description": "按已知 result_ref 分段回读当前会话工具结果；有 next_offset 时可继续。",
        "parameters": {
            "type": "object",
            "properties": {
                "result_ref": _string("工具结果标记中的引用"),
                "offset": {"type": "integer", "description": "起始字符位置，从 1 开始；默认 1"},
                "limit": {"type": "integer", "description": "最多字符数；默认 8000，最大 16000"},
            },
            "required": ["result_ref"],
        },
    },
    "extract_skill_from_images": {
        "description": "按用户要求从会话图片提炼 Skill 草稿；任务后台执行，ready 后供用户检查确认，不自动入库、绑定或生产。未完成时提供任务入口，不连续查询。",
        "parameters": {
            "type": "object",
            "properties": {
                "attachment_ids": {"type": "array", "items": {"type": "string"},
                                   "description": "当前会话的图片附件 ID，1–6 张且不重复"},
                "mode": {"type": "string", "enum": ["single", "mind", "visual", "pair"],
                         "description": "默认 single 完整方法；mind 内容方法；visual 呈现方法；pair 分别提炼内容和呈现"},
                "instruction": _string("用户对提炼的要求"),
            },
            "required": ["attachment_ids"],
        },
    },
    "query_feedback": {
        "description": ("读取当前账号近 7 天已核实发布笔记的本地反馈；每篇取最新采样的累计值，缺失不等于 0。"
                        "按指标降序分页，返回栏目和采样时间；不刷新平台。"),
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": ["favorites", "likes", "comments", "views", "shares"],
                           "description": "比较指标；默认 favorites（收藏）"},
                "offset": {"type": "integer", "description": "分页起点；默认 0"},
                "limit": {"type": "integer", "description": "每页数量；默认 10，最多 20"},
            },
        },
    },
    "save_extracted_skills": {
        "description": "用户查看草稿并明确确认后加入 Skill 库；不绑定栏目、不生产、不发布。",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": _string("草稿任务 ID"),
                "expected_digest": _string("已查看草稿查询结果中的 digest，原样填写"),
            },
            "required": ["job_id", "expected_digest"],
        },
    },
    "get_publication": {
        "description": "查询发布单；核对或刷新可能更新本地记录，不重新发布。unknown 不代表失败。",
        "parameters": {
            "type": "object",
            "properties": {
                "publication_id": _string("准备发布返回的发布单 ID"),
                "reconcile": {"type": "boolean", "description": "按要求核对平台结果；默认 false"},
                "refresh_metrics": {"type": "boolean", "description": "按要求刷新已核实作品的互动数据；默认 false"},
            },
            "required": ["publication_id"],
        },
    },
    "list_producer_skills": {
        "description": "列出共享的已安装发布 Skill；账号库和栏目池用 manage_skill_library，正文用 get_producer_skill。",
        "parameters": {
            "type": "object",
            "properties": {
                "include_versions": {"type": "boolean", "description": "true 查询当前账号可访问的历史版本和候选；默认 false"},
                "family_skill_id": _string("配合 include_versions=true 筛选该 Skill 所属家族"),
            },
        },
    },
    "get_topic_research": {
        "description": ("查看已有调研批次及完整候选；仅 ready 且非 stale 可选，queued 候选不重复入队。"
                        "requested_count 是上限，returned_count 是实际数量；以 status 判断完成。"
                        "unknown 不代表失败或远端停止，勿重提；原样使用返回的 url。"),
        "parameters": {
            "type": "object",
            "properties": {"batch_id": _string("调研返回的真实批次 ID")},
            "required": ["batch_id"],
        },
    },
    "request_content_revision": {
        "description": "用户明确要求返工时创建待执行修订，不执行生产、批准或发布；先查询 Run 取得当前版本。",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": _string("目标 Run ID"),
                "instruction": _string("用户要求的返工内容"),
                "expected_version": {"type": "integer", "description": "查询得到的该 Run 当前版本"},
            },
            "required": ["run_id", "instruction", "expected_version"],
        },
    },
    "prepare_publication": {
        "description": "为已有作品创建或更新发布草稿，返回内容页供用户确认；不发布。",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": _string("已有作品的 Run ID"),
                "title": _string("发布标题；省略使用作品文案"),
                "body": _string("发布正文；省略使用作品文案"),
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "发布标签；省略使用作品标签，空列表清空"},
                "visibility": {"type": "string", "enum": ["公开可见", "仅自己可见", "仅互关好友可见"],
                               "description": "可见范围；默认公开可见"},
            },
            "required": ["run_id"],
        },
    },
    "discuss_content_run": {
        "description": "讨论指定的已验收作品版本，创建后台讨论；不修改作品或审批，不自动返工。结果未知先查询原讨论。",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": _string("目标作品 Run ID"),
                "request_id": _string("本条讨论请求 ID：8–64 位字母、数字、下划线或连字符；同一请求复用"),
                "revision_id": _string("已验收的目标版本 ID"),
                "artifact_digest": _string("从该版本验收详情读取的产物摘要"),
                "message": _string("用户提出的讨论问题或观察"),
            },
            "required": ["run_id", "request_id", "revision_id", "artifact_digest", "message"],
        },
    },
    "prepare_topic_selection": {
        "description": "按用户选择保存待人工确认的入队预览，不入队、不生产；选择不明确时先询问。",
        "parameters": {
            "type": "object",
            "properties": {
                "batch_id": _string("调研返回的批次 ID"),
                "selections": {
                    "type": "array",
                    "description": "按用户指定顺序列出该批次候选；省略标题或切入点则沿用原文",
                    "items": {
                        "type": "object",
                        "properties": {
                            "candidate_id": _string("候选 ID"),
                            "title": _string("修改后的标题"),
                            "angle": _string("修改后的切入点"),
                        },
                        "required": ["candidate_id"],
                    },
                },
            },
            "required": ["batch_id", "selections"],
        },
    },
    "compose_series": {
        "description": "用户明确要求时为当前账号创建栏目，可仅填名称，之后配置 Skill 池；不制作内容。Skill 可用目录返回的 ID 或 local_path。完整 Skill 与 Mind＋Visualize 组合二选一，也可都不填。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": _string("栏目名称"),
                "description": _string("栏目定位"),
                "audience": _string("目标受众"),
                "skill_name": _string("完整制作 Skill"),
                "mind_skill_id": _string("内容 Skill；与 production_skill_id 同时提供"),
                "production_skill_id": _string("呈现 Skill；与 mind_skill_id 同时提供"),
            },
            "required": ["name"],
        },
    },
    "edit_extracted_skills": {
        "description": "仅编辑就绪且未入库的草稿；已入库用 Skill 编辑工具，不切换栏目。",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": _string("草稿任务 ID"),
                "expected_digest": _string("最近读取的草稿摘要"),
                "skills": {
                    "type": "array",
                    "description": "全部草稿，按原角色顺序提交，包括未修改项",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": _string("名称"),
                            "role": {"type": "string", "enum": ["mind", "production", "legacy_end_to_end"],
                                     "description": "内容、呈现或完整 Skill"},
                            "skill_md": _string("完整正文，保留未要求修改的内容"),
                            "output_kind": {"type": "string", "enum": ["image-carousel", "text"],
                                            "description": "沿用原值；省略默认为 image-carousel"},
                        },
                        "required": ["name", "role", "skill_md"],
                    },
                },
            },
            "required": ["job_id", "expected_digest", "skills"],
        },
    },
    "create_skill_draft": {
        "description": "保存用户给的文本为待检查 Skill 草稿，不提炼、不入库或生产。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": _string("小写字母、数字、连字符；正文有 frontmatter 时与 name 一致"),
                "description": _string("用途；正文有 frontmatter 时与 description 一致"),
                "content": _string("用户提供的提示词或完整 SKILL.md 原文"),
                "role": {"type": "string", "enum": ["legacy_end_to_end", "mind", "production"],
                         "description": "完整、内容方法、制作呈现；默认完整"},
                "attachment_ids": {"type": "array", "items": {"type": "string"},
                                   "description": "用户指定为参考图的本会话附件 ID"},
                "display_name": _string("展示名称；正文已有显示名时须一致"),
            },
            "required": ["name", "description", "content"],
        },
    },
    "get_creator_tasks": {
        "description": "查询当前账号的调研、制作及讨论任务，只读。已有结果可复用；问最新进展时重新查。waiting 制作可能尚无 Run，needs_attention 需检查；不重复提交。",
        "parameters": {
            "type": "object",
            "properties": {
                "series_id": _string("可选：目标栏目 ID"),
                "statuses": {
                    "type": "array",
                    "items": {"type": "string", "enum": [
                        "queued", "researching", "running", "producing", "validating", "ready",
                        "completed", "awaiting_approval", "approved", "interrupted", "failed",
                        "cancelled", "unknown", "stale", "waiting_production", "paused",
                        "exhausted", "waiting", "needs_attention",
                    ]},
                    "description": "按状态精确筛选；例如 failed 表示失败，省略表示全部状态",
                },
            },
        },
    },
    "update_series_composition": {
        "description": "按用户要求修改现有栏目：改名只传 name；换组合须同时传内容和制作 Skill，两种修改不能混用。旧单 Skill 栏目不能直接改为组合。",
        "parameters": {
            "type": "object",
            "properties": {
                "series_id": _string("目标栏目 ID"),
                "name": _string("新栏目名称；仅改名时填写"),
                "mind_skill_id": _string("内容 Skill ID；换组合时与 production_skill_id 一起填写"),
                "production_skill_id": _string("制作 Skill ID；换组合时与 mind_skill_id 一起填写"),
                "expected_revision": {"type": "integer", "description": "已读取的当前栏目 revision"},
            },
            "required": ["series_id", "expected_revision"],
        },
    },
    "read_file": {
        "description": "读取宿主允许的文本。账号对话限本会话归档，CLI 项目文件仍可读。归档不是当前授权。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": _string("文件路径；账号对话限本会话归档或索引"),
                "offset": {"type": "integer", "description": "从 1 开始，默认 1；读取按 unit，搜索固定按字符"},
                "limit": {"type": "integer", "description": "读取数：归档行默认/最多 100，字符默认 8000/最多 16000；项目文件默认至末尾。搜索为匹配数，默认/最多 5"},
                "unit": {"type": "string", "enum": ["lines", "chars"],
                         "description": "默认 lines；归档长文用 chars，项目文件仅 lines；搜索忽略 unit"},
                "search": _string("本会话归档精确区分大小写搜索，1–200 字；按匹配 offset 回读。complete 仅本次起点至末尾；核实全文需从 1 按 next_offset 连续搜索"),
            },
            "required": ["path"],
        },
    },
    "research_series_topics": {
        "description": "仅确需联网、最新趋势或核实外部来源时调研；普通创意和参考图改编直接完成。只产生候选，不入队或生产；结果未知先核对，不自动重提。",
        "parameters": {
            "type": "object",
            "properties": {
                "series_id": _string("目标栏目 ID"),
                "count": {"type": "integer", "description": "最多候选数，1–30，默认 10；不足不凑数"},
                "instructions": _string("完整保留用户的要求、偏好和排除方向"),
                "attachment_ids": {"type": "array", "items": {"type": "string"},
                                   "description": "用户指定的当前会话参考图片附件 ID，最多 20 个；不猜 ID"},
                "reference_presentation": {"type": "boolean",
                                           "description": "默认只参考内容；用户明确要求借鉴配色或版式时设为 true"},
            },
            "required": ["series_id"],
        },
    },
    "get_producer_skill": {
        "description": "读取 Skill。默认正文分页，按 page.has_more 继续；编辑前读取目标文件或文件树取得工作副本 digest。图片用 read_image 查看。",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": _string("要读取的 Skill ID"),
                "path": _string("读取指定文本文件的相对路径；可与 list_files 同传"),
                "list_files": {"type": "boolean", "description": "列出工作副本文件树；默认 false"},
                "include_versions": {"type": "boolean", "description": "只查家族历史版本和候选；不与 path 或 list_files 混用"},
                "offset": {"type": "integer", "description": "默认正文分页的起点；默认 0"},
                "limit": {"type": "integer", "description": "正文每页字符数；默认 2000，最多 4000"},
            },
            "required": ["skill_id"],
        },
    },
    "update_producer_skill_file": {
        "description": "按用户要求编辑 Skill 文件，结果为候选，不发布或切换栏目。可基于可用发布版另存私有候选。批量按顺序执行，失败即停且不回滚；结果未知时先核对，不重发整批。",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": _string("要编辑的 Skill 版本 ID"),
                "expected_digest": _string("读取目标文件或文件树取得的工作副本 digest；默认分页正文没有此值"),
                "operation": {
                    "type": "string",
                    "enum": ["write_text", "add_image", "replace_image", "delete_image", "batch"],
                    "description": "默认 write_text；batch 顺序编辑多项",
                },
                "path": _string("单项编辑的相对文件路径"),
                "content": _string("write_text 的完整新文本，允许空串"),
                "attachment_id": _string("add_image 或 replace_image 使用当前会话附件 ID"),
                "changes": {
                    "type": "array",
                    "description": "batch 的 1–20 项；不与顶层 path、content、attachment_id 混用。有引用依赖时先加图、改正文，再删旧图。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation": {"type": "string", "enum": ["write_text", "add_image", "replace_image", "delete_image"]},
                            "path": _string("相对文件路径"),
                            "content": _string("write_text 的完整新文本，可为空串"),
                            "attachment_id": _string("新增或替换图片的当前会话附件 ID"),
                        },
                        "required": ["operation", "path"],
                    },
                },
            },
            "required": ["skill_id", "expected_digest"],
        },
    },
}


def apply_model_action_views(catalog, version=V1):
    """Return an isolated model view without changing action authorization.

    Missing actions stay missing: a view can never add an action to the scoped
    catalog. Other tools and every v1 schema remain unchanged.
    """
    if version not in {V1, V2}:
        raise ValueError("unknown model action view")
    result = deepcopy(catalog)
    if version == V2:
        for action_id, view in _V2_ACTIONS.items():
            entry = result.get(action_id)
            if entry is None:
                continue
            canonical_name, action = action_id.rsplit(".", 1)
            if entry.get("name") != canonical_name or entry.get("action") != action:
                raise ValueError("model view does not match the scoped action")
            function = entry["schema"]["function"]
            function["description"] = view["description"]
            function["parameters"] = deepcopy(view["parameters"])
        for tool_name, view in _V2_ACTIONLESS.items():
            entry = result.get(tool_name)
            if entry is None:
                continue
            if (entry.get("id") != tool_name or entry.get("name") != tool_name
                    or "action" not in entry or entry["action"] is not None
                    or entry["schema"]["function"].get("name") != tool_name):
                raise ValueError("model view does not match the scoped tool")
            function = entry["schema"]["function"]
            # Only the account-bound schema has its creator_id injected by the
            # host. Unscoped overview and CLI tools still require the caller's ID.
            if (tool_name == "list_creator_series" and
                    "creator_id" in function.get("parameters", {}).get("properties", {})):
                continue
            function["description"] = view["description"]
            function["parameters"] = deepcopy(view["parameters"])
    return result
