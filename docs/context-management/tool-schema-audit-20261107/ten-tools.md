# 十个代表性工具：实际发给模型的完整 Schema

**版本：优化前基线 `2720cfa`。** 本次机械精简后的完整模型可见声明见[优化后十个工具](after/ten-tools.md)；此文件保留修改前内容，便于对照。

对照查看 [十个合成参数示例](arguments-examples.md)；示例经过解析校验，但没有执行真实操作。

以下是当前代码生成的 **Web 账号会话模型可见工具声明**，不是一次实际工具调用，也不是账号数据。每段完整保留 JSON 的描述、属性、类型、枚举、默认值、required、嵌套定义及可见字段；仅排版缩进，不改内容。工具 description 是给模型的用途和正负规则，parameters 是其可传参数及限制。

账号已由宿主绑定的工具不会在这些模型可见 Schema 中显示 `creator_id`。宿主执行前会注入当前账号，模型若试图显式指定其他账号会被拒绝；其他工具的实际权限还由各工具和业务接口校验。`read_file` 仅展示账号会话的当前会话归档版本。

## 导航

1. [选题查询与编辑 · manage_topics](#tool-manage-topics)
2. [多步骤计划 · manage_plan](#tool-manage-plan)
3. [内容生产提交 · start_content_run](#tool-start-content-run)
4. [读取 Skill · get_producer_skill](#tool-get-producer-skill)
5. [编辑 Skill 文件 · update_producer_skill_file](#tool-update-producer-skill-file)
6. [账号 Skill 库 · manage_skill_library](#tool-manage-skill-library)
7. [账号音乐素材 · manage_material](#tool-manage-material)
8. [查询账号任务 · get_creator_tasks](#tool-get-creator-tasks)
9. [发布准备 · prepare_publication](#tool-prepare-publication)
10. [会话归档回读 · read_file](#tool-read-file)

<a id="tool-manage-topics"></a>
## manage_topics

**选题查询与编辑** · 估算 1,236 tokens · 不属于自动注入账号字段的七个工具。

```json
{
  "type": "function",
  "function": {
    "name": "manage_topics",
    "description": "管理选题，不生产。list按栏目/状态分页：已入队仅返回身份、标题、状态和brief_preview；同栏目同筛选的完整列表已在上下文且没有已知增删改时复用，未读完继续翻页；要求最新或状态已变化再查。需要完整场景、来源或编辑摘要时get原topic_id；用户明确要全文时逐字呈现，包括重复段落和结尾，不用次数概括替代。制作结果未知时get原topic_id及返回的production_request_id只读核对，回执unknown不等于未提交，勿换ID重做。create仅用户明确新增时入队；update用原topic_id、changes和刚读取的edit_digest直接修改标题或完整切入点，不新建替代；remove按用户要求移除，保留旧产物，运行中拒绝。待选remove带真实candidate来源；待选入队用create保留reference_id。management_actions是管理权限，available_actions是生产操作。冲突重读，未知结果不另建；修改选题不等于更新图片。",
    "parameters": {
      "$defs": {
        "QueueTopicItem": {
          "additionalProperties": false,
          "properties": {
            "title": {
              "description": "选题标题。",
              "maxLength": 240,
              "minLength": 1,
              "type": "string"
            },
            "brief": {
              "anyOf": [
                {
                  "maxLength": 4000,
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "description": "切入点/简介；保留候选来源信息。"
            },
            "source": {
              "default": "manual",
              "description": "调研候选为 research，手动添加为 manual。",
              "enum": [
                "research",
                "manual"
              ],
              "type": "string"
            },
            "reference_id": {
              "anyOf": [
                {
                  "pattern": "^[a-f0-9]{64}$",
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "description": "候选返回的reference_id原样保留，使生产继续读取原图和要求；没有就省略，不能猜测。"
            }
          },
          "required": [
            "title"
          ],
          "title": "QueueTopicItem",
          "type": "object"
        },
        "ResearchCandidate": {
          "additionalProperties": false,
          "properties": {
            "batch_id": {
              "pattern": "^[a-f0-9]{32}$",
              "type": "string"
            },
            "candidate_id": {
              "maxLength": 80,
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "batch_id",
            "candidate_id"
          ],
          "title": "ResearchCandidate",
          "type": "object"
        },
        "TopicChanges": {
          "additionalProperties": false,
          "properties": {
            "title": {
              "anyOf": [
                {
                  "maxLength": 240,
                  "minLength": 1,
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null
            },
            "brief": {
              "anyOf": [
                {
                  "maxLength": 10000,
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "description": "完整切入点/场景；空字符串清空，不修改则省略。"
            }
          },
          "title": "TopicChanges",
          "type": "object"
        },
        "TopicQuery": {
          "additionalProperties": false,
          "properties": {
            "state": {
              "default": "all",
              "enum": [
                "all",
                "pending",
                "queued"
              ],
              "type": "string"
            },
            "offset": {
              "default": 0,
              "minimum": 0,
              "type": "integer"
            },
            "limit": {
              "default": 20,
              "maximum": 100,
              "minimum": 1,
              "type": "integer"
            }
          },
          "title": "TopicQuery",
          "type": "object"
        }
      },
      "additionalProperties": false,
      "properties": {
        "action": {
          "enum": [
            "list",
            "get",
            "create",
            "update",
            "remove"
          ],
          "type": "string"
        },
        "series_id": {
          "anyOf": [
            {
              "maxLength": 80,
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "list/create的真实栏目ID；账号由宿主会话约束。"
        },
        "topic_id": {
          "anyOf": [
            {
              "maxLength": 80,
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "get/update/remove操作的原选题ID，不新建替代。"
        },
        "production_request_id": {
          "anyOf": [
            {
              "maxLength": 64,
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "仅 get：使用未知制作结果中返回的原 request_id 只读核对该次回执。"
        },
        "query": {
          "anyOf": [
            {
              "$ref": "#/$defs/TopicQuery"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "仅list：筛选与分页。"
        },
        "topics": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/QueueTopicItem"
              },
              "maxItems": 50,
              "minItems": 1,
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "仅create：用户明确要求新建的选题，保留reference_id。"
        },
        "changes": {
          "anyOf": [
            {
              "$ref": "#/$defs/TopicChanges"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "仅update：只提供需要修改的标题或完整场景。"
        },
        "expected_digest": {
          "anyOf": [
            {
              "pattern": "^[a-f0-9]{64}$",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "仅update：先get取得当前edit_digest，不能猜或沿用旧摘要。"
        },
        "candidate": {
          "anyOf": [
            {
              "$ref": "#/$defs/ResearchCandidate"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "仅remove待选建议时提供列表中实际的批次和候选ID；已入队选题不提供。"
        }
      },
      "required": [
        "action"
      ],
      "title": "ManageTopicsArgs",
      "type": "object"
    }
  }
}
```

<a id="tool-manage-plan"></a>
## manage_plan

**多步骤计划** · 估算 1,481 tokens · 不属于自动注入账号字段的七个工具。

```json
{
  "type": "function",
  "function": {
    "name": "manage_plan",
    "description": "管理当前账号对话的通用执行计划。明确要求生产完成后核验、继续处理或汇报结果时，即使只有一项生产也创建计划，start_content_run步骤设wait_for_completion=true；宿主观察原任务并在计划完成或失败后唤醒本会话。create 提交步骤和依赖，由宿主执行；仅需等待完成并汇报（包括先核验结果）时，不要添加request_review；计划终态会唤醒本会话。只有需要根据结果决定、调整或追加后续动作时才添加request_review，参数 question 说明要判断的问题，后台暂停并唤醒本会话。get 当前计划后 update 只传需修改或新增的完整步骤：修改用原步骤 ID，新 ID 只追加；其他步骤保留。删除未开始或阻塞的步骤须明确填写 remove_step_ids，并同时调整引用它的依赖；已开始步骤不可删除。paused 计划调整后 resume，needs_attention 计划调整后自动继续。失败时先 get 最新版本，只可 skip 标记可安全跳过的无副作用步骤，再修改后续依赖。已提交或结果未知的生产必须先核对，不可更名重提。引用依赖结果用 {$ref:'步骤ID.字段'}。不要轮询或把受理说成完成。",
    "parameters": {
      "type": "object",
      "$defs": {
        "CreatePlanArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "const": "create",
              "type": "string"
            },
            "title": {
              "maxLength": 240,
              "minLength": 1,
              "type": "string"
            },
            "steps": {
              "items": {
                "$ref": "#/$defs/PlanStepArgs"
              },
              "maxItems": 100,
              "minItems": 1,
              "type": "array"
            }
          },
          "required": [
            "action",
            "title",
            "steps"
          ],
          "title": "CreatePlanArgs",
          "type": "object"
        },
        "GetPlanArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "const": "get",
              "type": "string"
            },
            "plan_id": {
              "maxLength": 128,
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "action",
            "plan_id"
          ],
          "title": "GetPlanArgs",
          "type": "object"
        },
        "ListPlansArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "const": "list",
              "type": "string"
            }
          },
          "required": [
            "action"
          ],
          "title": "ListPlansArgs",
          "type": "object"
        },
        "PlanStepActionArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "enum": [
                "retry",
                "skip"
              ],
              "type": "string"
            },
            "plan_id": {
              "maxLength": 128,
              "minLength": 1,
              "type": "string"
            },
            "expected_version": {
              "minimum": 0,
              "type": "integer"
            },
            "step_id": {
              "maxLength": 64,
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "action",
            "plan_id",
            "expected_version",
            "step_id"
          ],
          "title": "PlanStepActionArgs",
          "type": "object"
        },
        "PlanStepArgs": {
          "additionalProperties": false,
          "properties": {
            "id": {
              "description": "本计划内唯一的简短步骤标识。",
              "maxLength": 64,
              "minLength": 1,
              "pattern": "^[a-zA-Z0-9_-]+$",
              "type": "string"
            },
            "title": {
              "description": "用户能理解的步骤目标。",
              "maxLength": 240,
              "minLength": 1,
              "type": "string"
            },
            "tool": {
              "enum": [
                "request_review",
                "start_content_run",
                "manage_topics",
                "manage_material",
                "manage_media_collection",
                "manage_skill_library",
                "get_content_run",
                "get_creator_tasks",
                "get_producer_skill"
              ],
              "type": "string"
            },
            "arguments": {
              "additionalProperties": true,
              "description": "对应工具当前契约的参数；生产必须明确action与任务kind，仅用skill_ids。依赖结果可用 {$ref: '步骤ID.字段'}，保存前检查固定字段、执行前完整校验。",
              "type": "object"
            },
            "depends_on": {
              "items": {
                "type": "string"
              },
              "maxItems": 100,
              "type": "array"
            },
            "wait_for_completion": {
              "default": false,
              "description": "仅用于start_content_run；等待原生产真实终态，再解锁依赖步骤。计划完成或失败后宿主唤醒原会话。",
              "type": "boolean"
            }
          },
          "required": [
            "id",
            "title",
            "tool",
            "arguments"
          ],
          "title": "PlanStepArgs",
          "type": "object"
        },
        "PlanVersionActionArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "enum": [
                "pause",
                "resume"
              ],
              "type": "string"
            },
            "plan_id": {
              "maxLength": 128,
              "minLength": 1,
              "type": "string"
            },
            "expected_version": {
              "minimum": 0,
              "type": "integer"
            }
          },
          "required": [
            "action",
            "plan_id",
            "expected_version"
          ],
          "title": "PlanVersionActionArgs",
          "type": "object"
        },
        "UpdatePlanArgs": {
          "additionalProperties": false,
          "anyOf": [
            {
              "properties": {
                "steps": {
                  "minItems": 1
                }
              },
              "required": [
                "steps"
              ]
            },
            {
              "properties": {
                "remove_step_ids": {
                  "minItems": 1
                }
              },
              "required": [
                "remove_step_ids"
              ]
            }
          ],
          "properties": {
            "action": {
              "const": "update",
              "type": "string"
            },
            "plan_id": {
              "maxLength": 128,
              "minLength": 1,
              "type": "string"
            },
            "expected_version": {
              "minimum": 0,
              "type": "integer"
            },
            "steps": {
              "description": "要修改或新增的完整步骤；修改现有步骤时保留其 ID，新 ID 只追加，未提及步骤保留。",
              "items": {
                "$ref": "#/$defs/PlanStepArgs"
              },
              "maxItems": 100,
              "type": "array"
            },
            "remove_step_ids": {
              "description": "明确删除的未开始或阻塞步骤 ID；已执行步骤不可删除，不能依靠更换 ID 删除原步骤。",
              "items": {
                "type": "string"
              },
              "maxItems": 100,
              "type": "array"
            }
          },
          "required": [
            "action",
            "plan_id",
            "expected_version"
          ],
          "title": "UpdatePlanArgs",
          "type": "object"
        }
      },
      "discriminator": {
        "mapping": {
          "create": "#/$defs/CreatePlanArgs",
          "get": "#/$defs/GetPlanArgs",
          "list": "#/$defs/ListPlansArgs",
          "pause": "#/$defs/PlanVersionActionArgs",
          "resume": "#/$defs/PlanVersionActionArgs",
          "retry": "#/$defs/PlanStepActionArgs",
          "skip": "#/$defs/PlanStepActionArgs",
          "update": "#/$defs/UpdatePlanArgs"
        },
        "propertyName": "action"
      },
      "oneOf": [
        {
          "$ref": "#/$defs/CreatePlanArgs"
        },
        {
          "$ref": "#/$defs/ListPlansArgs"
        },
        {
          "$ref": "#/$defs/GetPlanArgs"
        },
        {
          "$ref": "#/$defs/UpdatePlanArgs"
        },
        {
          "$ref": "#/$defs/PlanVersionActionArgs"
        },
        {
          "$ref": "#/$defs/PlanStepActionArgs"
        }
      ]
    }
  }
}
```

<a id="tool-start-content-run"></a>
## start_content_run

**内容生产提交** · 估算 2,104 tokens · 不属于自动注入账号字段的七个工具。

```json
{
  "type": "function",
  "function": {
    "name": "start_content_run",
    "description": "用户明确要求生产且仅需提交时调用；明确要求完成后核验、继续处理或汇报结果时，通过manage_plan的生产步骤设置wait_for_completion=true。选择action：start用topic_ids列表按栏目默认生产，单条也填列表；tasks用topic_id和tasks拆分制作，每项显式kind和具体brief、可选skill_ids；fresh用source_run_id和所见expected_version清空旧上下文重做，可指定skill_ids；reference用series_id、title、当前会话attachment_ids及完整要求直接制作，可用skill_ids选择本次一份或多份精确版本，不改栏目绑定；省略时沿用默认。同一参考请求改选Skill会冲突，先核对原记录。25张独立理由图填25项，各自一个Codex会话，共用同题素材集。先图后视频用manage_plan等待上游，视频inputs携带collection_id与bgm_material_id或bgm_reference，分享原文直接交Codex，不先解析。受理不等于完成，不自动重提或发布。",
    "parameters": {
      "type": "object",
      "$defs": {
        "FreshProductionRunArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "const": "fresh",
              "type": "string"
            },
            "source_run_id": {
              "description": "先读取的原 Run ID；从同一选题清空旧上下文重做。",
              "maxLength": 80,
              "minLength": 1,
              "type": "string"
            },
            "expected_version": {
              "description": "读取来源 Run 时取得的当前 version。",
              "exclusiveMinimum": 0,
              "type": "integer"
            },
            "skill_ids": {
              "anyOf": [
                {
                  "items": {
                    "maxLength": 120,
                    "minLength": 1,
                    "pattern": "^(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})$",
                    "type": "string"
                  },
                  "maxItems": 12,
                  "minItems": 1,
                  "type": "array"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "description": "本次重做使用的精确版本 ID；省略使用现有重做规则，不改栏目默认。"
            }
          },
          "required": [
            "action",
            "source_run_id",
            "expected_version"
          ],
          "title": "FreshProductionRunArgs",
          "type": "object"
        },
        "ImageProductionTask": {
          "additionalProperties": false,
          "properties": {
            "title": {
              "description": "该独立制作任务的简短名称。",
              "maxLength": 240,
              "minLength": 1,
              "type": "string"
            },
            "brief": {
              "description": "本项独立画面或视频的完整要求；独立理由图一图一项，25张填25项。",
              "maxLength": 10000,
              "minLength": 1,
              "type": "string"
            },
            "skill_ids": {
              "anyOf": [
                {
                  "items": {
                    "maxLength": 120,
                    "minLength": 1,
                    "pattern": "^(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})$",
                    "type": "string"
                  },
                  "maxItems": 12,
                  "minItems": 1,
                  "type": "array"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "description": "本次使用的精确 Skill 版本 ID，可选择一项或多项；省略使用栏目对应阶段默认。"
            },
            "kind": {
              "const": "image",
              "type": "string"
            }
          },
          "required": [
            "title",
            "brief",
            "kind"
          ],
          "title": "ImageProductionTask",
          "type": "object"
        },
        "MaterialBgmInputs": {
          "additionalProperties": false,
          "properties": {
            "collection_id": {
              "maxLength": 128,
              "minLength": 1,
              "type": "string"
            },
            "export_id": {
              "anyOf": [
                {
                  "pattern": "^[a-f0-9]{64}$",
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null
            },
            "bgm_material_id": {
              "description": "已有音频或视频的素材 ID。",
              "pattern": "^[a-f0-9]{64}$",
              "type": "string"
            }
          },
          "required": [
            "collection_id",
            "bgm_material_id"
          ],
          "title": "MaterialBgmInputs",
          "type": "object"
        },
        "ReferenceBgmInputs": {
          "additionalProperties": false,
          "properties": {
            "collection_id": {
              "maxLength": 128,
              "minLength": 1,
              "type": "string"
            },
            "export_id": {
              "anyOf": [
                {
                  "pattern": "^[a-f0-9]{64}$",
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null
            },
            "bgm_reference": {
              "description": "用户给的 BGM 分享原文或链接，直接交给 Codex；无需预解析或先导入。",
              "maxLength": 10000,
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "collection_id",
            "bgm_reference"
          ],
          "title": "ReferenceBgmInputs",
          "type": "object"
        },
        "ReferenceProductionRunArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "const": "reference",
              "type": "string"
            },
            "series_id": {
              "description": "按用户给的参考作品直接制作的目标栏目。",
              "minLength": 1,
              "type": "string"
            },
            "title": {
              "description": "仅用于展示的简短选题名称。",
              "maxLength": 240,
              "minLength": 1,
              "type": "string"
            },
            "attachment_ids": {
              "description": "当前会话中已有参考图片的附件 ID。",
              "items": {
                "minLength": 1,
                "type": "string"
              },
              "maxItems": 20,
              "minItems": 1,
              "type": "array"
            },
            "instructions": {
              "default": "",
              "description": "参考制作的完整要求。",
              "maxLength": 3000,
              "type": "string"
            },
            "reference_presentation": {
              "default": false,
              "description": "默认只借鉴内容；用户明确要求参考画面风格或版式时才设 true。",
              "type": "boolean"
            },
            "skill_ids": {
              "anyOf": [
                {
                  "items": {
                    "maxLength": 120,
                    "minLength": 1,
                    "pattern": "^(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})$",
                    "type": "string"
                  },
                  "maxItems": 12,
                  "minItems": 1,
                  "type": "array"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "description": "本次参考制作使用的一份或多份精确 Skill 版本 ID；省略沿用栏目默认，不改栏目绑定。"
            }
          },
          "required": [
            "action",
            "series_id",
            "title",
            "attachment_ids"
          ],
          "title": "ReferenceProductionRunArgs",
          "type": "object"
        },
        "StartDefaultRunArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "const": "start",
              "type": "string"
            },
            "topic_ids": {
              "description": "使用栏目默认 Skill 的真实选题 ID 列表；单个选题也填列表。",
              "items": {
                "maxLength": 80,
                "minLength": 1,
                "type": "string"
              },
              "maxItems": 100,
              "minItems": 1,
              "type": "array"
            }
          },
          "required": [
            "action",
            "topic_ids"
          ],
          "title": "StartDefaultRunArgs",
          "type": "object"
        },
        "StartProductionTasksArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "const": "tasks",
              "type": "string"
            },
            "topic_id": {
              "maxLength": 80,
              "minLength": 1,
              "type": "string"
            },
            "tasks": {
              "description": "同一选题的独立制作单位，每项一个 Codex thread；kind 必须显式选择。",
              "items": {
                "discriminator": {
                  "mapping": {
                    "image": "#/$defs/ImageProductionTask",
                    "video": "#/$defs/VideoProductionTask"
                  },
                  "propertyName": "kind"
                },
                "oneOf": [
                  {
                    "$ref": "#/$defs/ImageProductionTask"
                  },
                  {
                    "$ref": "#/$defs/VideoProductionTask"
                  }
                ]
              },
              "maxItems": 100,
              "minItems": 1,
              "type": "array"
            },
            "instructions": {
              "default": "",
              "description": "本批每项制作共同遵守的要求。",
              "maxLength": 3000,
              "type": "string"
            }
          },
          "required": [
            "action",
            "topic_id",
            "tasks"
          ],
          "title": "StartProductionTasksArgs",
          "type": "object"
        },
        "VideoProductionTask": {
          "additionalProperties": false,
          "properties": {
            "title": {
              "description": "该独立制作任务的简短名称。",
              "maxLength": 240,
              "minLength": 1,
              "type": "string"
            },
            "brief": {
              "description": "本项独立画面或视频的完整要求；独立理由图一图一项，25张填25项。",
              "maxLength": 10000,
              "minLength": 1,
              "type": "string"
            },
            "skill_ids": {
              "anyOf": [
                {
                  "items": {
                    "maxLength": 120,
                    "minLength": 1,
                    "pattern": "^(knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})$",
                    "type": "string"
                  },
                  "maxItems": 12,
                  "minItems": 1,
                  "type": "array"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "description": "本次使用的精确 Skill 版本 ID，可选择一项或多项；省略使用栏目对应阶段默认。"
            },
            "kind": {
              "const": "video",
              "type": "string"
            },
            "inputs": {
              "anyOf": [
                {
                  "$ref": "#/$defs/MaterialBgmInputs"
                },
                {
                  "$ref": "#/$defs/ReferenceBgmInputs"
                }
              ],
              "description": "素材集及 BGM 素材ID或分享原文，两种 BGM 输入二选一。"
            },
            "depends_on": {
              "description": "用户明确选择的本选题生图任务 ID；后台等待完成，不轮询。",
              "items": {
                "maxLength": 80,
                "minLength": 1,
                "type": "string"
              },
              "maxItems": 100,
              "type": "array"
            }
          },
          "required": [
            "title",
            "brief",
            "kind",
            "inputs"
          ],
          "title": "VideoProductionTask",
          "type": "object"
        }
      },
      "discriminator": {
        "mapping": {
          "fresh": "#/$defs/FreshProductionRunArgs",
          "reference": "#/$defs/ReferenceProductionRunArgs",
          "start": "#/$defs/StartDefaultRunArgs",
          "tasks": "#/$defs/StartProductionTasksArgs"
        },
        "propertyName": "action"
      },
      "oneOf": [
        {
          "$ref": "#/$defs/StartDefaultRunArgs"
        },
        {
          "$ref": "#/$defs/StartProductionTasksArgs"
        },
        {
          "$ref": "#/$defs/FreshProductionRunArgs"
        },
        {
          "$ref": "#/$defs/ReferenceProductionRunArgs"
        }
      ]
    }
  }
}
```

<a id="tool-get-producer-skill"></a>
## get_producer_skill

**读取 Skill** · 估算 476 tokens · 不属于自动注入账号字段的七个工具。

```json
{
  "type": "function",
  "function": {
    "name": "get_producer_skill",
    "description": "读取指定Skill。编辑正文和参考图时一次传path=SKILL.md、list_files=true，取得全文、文件树和同一版本的真实digest；只读已知文件可直接path，无需先列目录。默认分页正文无写入digest。include_versions=true仅在需要定位历史版/候选时查家族，不与文件读取混用；已有准确ID不必再列家族。看图片用read_image。",
    "parameters": {
      "additionalProperties": false,
      "properties": {
        "skill_id": {
          "description": "从 list_producer_skills 或栏目组合元数据取得的 Skill ID。",
          "pattern": "^(?:knowledge-to-carousel|[a-z0-9-]+--[a-f0-9]{16})$",
          "type": "string"
        },
        "offset": {
          "default": 0,
          "description": "正文字符分页起点；有更多内容时按 page.has_more 继续。",
          "minimum": 0,
          "type": "integer"
        },
        "limit": {
          "default": 2000,
          "description": "本页最多字符数，最大 4000。",
          "maximum": 4000,
          "minimum": 1,
          "type": "integer"
        },
        "path": {
          "anyOf": [
            {
              "maxLength": 512,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "只读取指定 Markdown/UTF-8 文本；已知根 SKILL.md 可直接读取，无需先列文件。可与 list_files=true 一次返回文件树和该正文。图片路径仍不返回图像字节。"
        },
        "list_files": {
          "default": false,
          "description": "true 时列出受管工作副本文件；与 path 同传可同时读取指定正文。",
          "type": "boolean"
        },
        "include_versions": {
          "default": false,
          "description": "true 时只读取该 Skill 家族历史版本、候选和 digest；与 path/list_files 互斥。",
          "type": "boolean"
        }
      },
      "required": [
        "skill_id"
      ],
      "title": "ProducerSkillArgs",
      "type": "object"
    }
  }
}
```

<a id="tool-update-producer-skill-file"></a>
## update_producer_skill_file

**编辑 Skill 文件** · 估算 876 tokens · 不属于自动注入账号字段的七个工具。

```json
{
  "type": "function",
  "function": {
    "name": "update_producer_skill_file",
    "description": "编辑已有Skill，先读取真实digest。单项write_text改完整文本，add_image/replace_image/delete_image管理assets；多项修改用operation=batch和changes一次提交，按添加图片→改正文引用→删旧图排序，新图用当前会话attachment_id。宿主逐项跟随实际候选ID/digest，失败停止并返回已完成项，不是原子事务；outcome_unknown不能整批重发。发布版另存完整候选，后续用回执新skill_id/digest。账号对话中可用的非内置发布版即使原件can_edit=false，用户明确要求时也可另存当前账号私有候选；不覆盖共享原件，不访问其他账号私有候选。只问权限或明确不写入时不调用本工具。只保存候选，不改栏目、旧Run或自动生产；试产用start_content_run指定版本，循环用manage_skill_iteration。",
    "parameters": {
      "$defs": {
        "SkillEditChangeArgs": {
          "additionalProperties": false,
          "properties": {
            "operation": {
              "enum": [
                "write_text",
                "add_image",
                "replace_image",
                "delete_image"
              ],
              "type": "string"
            },
            "path": {
              "maxLength": 512,
              "minLength": 1,
              "type": "string"
            },
            "content": {
              "anyOf": [
                {
                  "maxLength": 524288,
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null
            },
            "attachment_id": {
              "anyOf": [
                {
                  "maxLength": 128,
                  "minLength": 1,
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null
            }
          },
          "required": [
            "operation",
            "path"
          ],
          "title": "SkillEditChangeArgs",
          "type": "object"
        }
      },
      "additionalProperties": false,
      "properties": {
        "skill_id": {
          "description": "从 list_producer_skills 取得的已安装 Skill ID。",
          "pattern": "^[a-z0-9-]+--[a-f0-9]{16}$",
          "type": "string"
        },
        "path": {
          "anyOf": [
            {
              "maxLength": 512,
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "单项文字/图片操作的相对路径；批量编辑时每个 changes 项各自提供 path。"
        },
        "content": {
          "anyOf": [
            {
              "maxLength": 524288,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "write_text 时提供完整的新 UTF-8 文本；图片操作不传此字段。"
        },
        "operation": {
          "default": "write_text",
          "description": "默认单项改文字；图片使用 add_image/replace_image/delete_image；多项使用 batch。",
          "enum": [
            "write_text",
            "add_image",
            "replace_image",
            "delete_image",
            "batch"
          ],
          "type": "string"
        },
        "attachment_id": {
          "anyOf": [
            {
              "maxLength": 128,
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "新增或替换图片时提供当前会话真实附件ID，不传任意图片路径或base64。"
        },
        "expected_digest": {
          "description": "get_producer_skill(list_files=true) 或 path 读取返回的当前工作副本 digest。分页正文不提供该值；禁止猜测或使用 source_digest。",
          "pattern": "^[a-f0-9]{64}$",
          "type": "string"
        },
        "changes": {
          "anyOf": [
            {
              "items": {
                "$ref": "#/$defs/SkillEditChangeArgs"
              },
              "maxItems": 20,
              "minItems": 1,
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "operation=batch 时按顺序执行 1–20 项；使用每项真实回执更新候选 ID 与 digest，失败即停，不回滚。"
        }
      },
      "required": [
        "skill_id",
        "expected_digest"
      ],
      "title": "UpdateProducerSkillFileArgs",
      "type": "object"
    }
  }
}
```

<a id="tool-manage-skill-library"></a>
## manage_skill_library

**账号 Skill 库** · 估算 588 tokens · 宿主注入账号。

```json
{
  "type": "function",
  "function": {
    "name": "manage_skill_library",
    "description": "管理当前账号 Skill 库和栏目池，账号由宿主绑定。get可带series_id读取栏目池；已有覆盖所需范围的目录且未变化时复用。add只传skill_id引用可用Skill；remove传skill_id和所见expected_version；set_pool只传series_id、expected_version、skill_ids保存栏目池。宿主保留仍有效的旧默认，不传defaults或主辅。共享引用不授予修改原件权限。",
    "parameters": {
      "type": "object",
      "$defs": {
        "AddSkillLibraryArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "const": "add",
              "type": "string"
            },
            "skill_id": {
              "maxLength": 120,
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "action",
            "skill_id"
          ],
          "title": "AddSkillLibraryArgs",
          "type": "object"
        },
        "GetSkillLibraryArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "const": "get",
              "type": "string"
            },
            "series_id": {
              "anyOf": [
                {
                  "maxLength": 80,
                  "minLength": 1,
                  "type": "string"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "description": "省略读取账号库；指定时读取此栏目池。"
            }
          },
          "required": [
            "action"
          ],
          "title": "GetSkillLibraryArgs",
          "type": "object"
        },
        "RemoveSkillLibraryArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "const": "remove",
              "type": "string"
            },
            "skill_id": {
              "maxLength": 120,
              "minLength": 1,
              "type": "string"
            },
            "expected_version": {
              "minimum": 0,
              "type": "integer"
            }
          },
          "required": [
            "action",
            "skill_id",
            "expected_version"
          ],
          "title": "RemoveSkillLibraryArgs",
          "type": "object"
        },
        "SetSkillPoolArgs": {
          "additionalProperties": false,
          "properties": {
            "action": {
              "const": "set_pool",
              "type": "string"
            },
            "series_id": {
              "maxLength": 80,
              "minLength": 1,
              "type": "string"
            },
            "expected_version": {
              "minimum": 0,
              "type": "integer"
            },
            "skill_ids": {
              "items": {
                "type": "string"
              },
              "maxItems": 100,
              "type": "array"
            }
          },
          "required": [
            "action",
            "series_id",
            "expected_version",
            "skill_ids"
          ],
          "title": "SetSkillPoolArgs",
          "type": "object"
        }
      },
      "discriminator": {
        "mapping": {
          "add": "#/$defs/AddSkillLibraryArgs",
          "get": "#/$defs/GetSkillLibraryArgs",
          "remove": "#/$defs/RemoveSkillLibraryArgs",
          "set_pool": "#/$defs/SetSkillPoolArgs"
        },
        "propertyName": "action"
      },
      "oneOf": [
        {
          "$ref": "#/$defs/GetSkillLibraryArgs"
        },
        {
          "$ref": "#/$defs/AddSkillLibraryArgs"
        },
        {
          "$ref": "#/$defs/RemoveSkillLibraryArgs"
        },
        {
          "$ref": "#/$defs/SetSkillPoolArgs"
        }
      ]
    }
  }
}
```

<a id="tool-manage-material"></a>
## manage_material

**账号音乐素材** · 估算 387 tokens · 宿主注入账号。

```json
{
  "type": "function",
  "function": {
    "name": "manage_material",
    "description": "管理当前账号 BGM：list/get 查询；import_link 后台导入分享原文并分析，import_status 查看；analyze 后台分析已有音频，analysis_status 查询状态，analysis 获取摘要。后台任务运行时不要反复查询。",
    "parameters": {
      "additionalProperties": false,
      "properties": {
        "action": {
          "enum": [
            "list",
            "get",
            "import_link",
            "import_status",
            "analyze",
            "analysis",
            "analysis_status"
          ],
          "type": "string"
        },
        "material_id": {
          "anyOf": [
            {
              "pattern": "^[a-f0-9]{64}$",
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null
        },
        "source_url": {
          "anyOf": [
            {
              "maxLength": 10000,
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "用户提供的音乐/视频分享原文。"
        },
        "import_id": {
          "anyOf": [
            {
              "maxLength": 100,
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null
        },
        "start": {
          "default": 0,
          "description": "音乐分析片段起点，秒。",
          "minimum": 0,
          "type": "number"
        },
        "duration": {
          "anyOf": [
            {
              "exclusiveMinimum": 0,
              "maximum": 300,
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "音乐分析片段时长，秒；不填使用默认。"
        },
        "force": {
          "default": false,
          "description": "明确要求重新分析时设为 true。",
          "type": "boolean"
        },
        "offset": {
          "default": 0,
          "minimum": 0,
          "type": "integer"
        },
        "limit": {
          "default": 20,
          "maximum": 100,
          "minimum": 1,
          "type": "integer"
        }
      },
      "required": [
        "action"
      ],
      "title": "ManageMaterialArgs",
      "type": "object"
    }
  }
}
```

<a id="tool-get-creator-tasks"></a>
## get_creator_tasks

**查询账号任务** · 估算 329 tokens · 宿主注入账号。

```json
{
  "type": "function",
  "function": {
    "name": "get_creator_tasks",
    "description": "查询当前账号任务；查一个选题优先用已知topic_id/run_id，不遍历全账号；确需目录时按栏目和状态筛选。已有结果覆盖本次问题时复用，问最新进展时再查。包括尚无Run的waiting制作与needs_attention；返回原选题/素材集入口，不恢复或重新提交。",
    "parameters": {
      "additionalProperties": false,
      "properties": {
        "series_id": {
          "anyOf": [
            {
              "maxLength": 80,
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "可选真实栏目 ID，用于缩小任务范围。"
        },
        "statuses": {
          "anyOf": [
            {
              "items": {
                "enum": [
                  "queued",
                  "researching",
                  "running",
                  "producing",
                  "validating",
                  "ready",
                  "completed",
                  "awaiting_approval",
                  "approved",
                  "interrupted",
                  "failed",
                  "cancelled",
                  "unknown",
                  "stale",
                  "waiting_production",
                  "paused",
                  "exhausted",
                  "waiting",
                  "needs_attention"
                ],
                "type": "string"
              },
              "maxItems": 22,
              "minItems": 1,
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "只返回指定状态的任务及对应入口；用户只要失败时填 failed，省略表示全部状态。"
        }
      },
      "required": [],
      "title": "CreatorTasksArgs",
      "type": "object"
    }
  }
}
```

<a id="tool-prepare-publication"></a>
## prepare_publication

**发布准备** · 估算 255 tokens · 不属于自动注入账号字段的七个工具。

```json
{
  "type": "function",
  "function": {
    "name": "prepare_publication",
    "description": "准备已有图文的发布预览，不发布。返回内容页供用户确认；重复请求复用发布单。",
    "parameters": {
      "additionalProperties": false,
      "properties": {
        "run_id": {
          "description": "已有内容的 Run ID。",
          "maxLength": 80,
          "minLength": 1,
          "type": "string"
        },
        "title": {
          "anyOf": [
            {
              "maxLength": 500,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "可选发布标题；不改原产物。"
        },
        "body": {
          "anyOf": [
            {
              "maxLength": 10000,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "可选发布正文；不改原产物。"
        },
        "tags": {
          "anyOf": [
            {
              "items": {
                "type": "string"
              },
              "maxItems": 10,
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "default": null
        },
        "visibility": {
          "default": "公开可见",
          "enum": [
            "公开可见",
            "仅自己可见",
            "仅互关好友可见"
          ],
          "type": "string"
        }
      },
      "required": [
        "run_id"
      ],
      "title": "PreparePublicationArgs",
      "type": "object"
    }
  }
}
```

<a id="tool-read-file"></a>
## read_file

**会话归档回读** · 估算 361 tokens · 不属于自动注入账号字段的七个工具。

```json
{
  "type": "function",
  "function": {
    "name": "read_file",
    "description": "读取当前会话的工具结果归档文本；查找未知片段先传 search 精确搜索，按返回 offset 再读取上下文；complete=false 时按 next_offset 继续。不能读取项目文件、Skill 文件或其他会话。",
    "parameters": {
      "additionalProperties": false,
      "properties": {
        "path": {
          "description": "当前会话归档结果中提供的路径。",
          "type": "string"
        },
        "offset": {
          "default": 1,
          "description": "从第几行开始读取，第一行是 1。",
          "minimum": 1,
          "type": "integer"
        },
        "limit": {
          "anyOf": [
            {
              "minimum": 1,
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "最多读取多少行，不填写则读取到文件结尾。"
        },
        "unit": {
          "default": "lines",
          "description": "归档大文件可用 chars 按字符分页，offset 从 1 起。",
          "enum": [
            "lines",
            "chars"
          ],
          "type": "string"
        },
        "search": {
          "anyOf": [
            {
              "maxLength": 200,
              "minLength": 1,
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "description": "仅当前会话归档：区分大小写的精确字符串搜索。offset 为 1 起的字符位置，limit 为最多返回匹配数（上限 5）；按 next_offset 继续，complete=true 才表示已扫描至末尾。"
        }
      },
      "required": [
        "path"
      ],
      "title": "ReadFileArgs",
      "type": "object"
    }
  }
}
```
