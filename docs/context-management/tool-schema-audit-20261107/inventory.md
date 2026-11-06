# 账号 Agent 工具 Schema 占用：优化前基线

以下为 `2720cfa` 优化前基线。机械精简后当前账号工具总量为 **14,622** 估算 tokens，逐项完整结果见 [优化后清单](after/inventory.md)。

本文件是优化前基线：工作树起始提交 `2720cfa`，采样时未修改工具定义；后续代码变更后的当前 Schema 请重新运行同目录 `generate.py` 导出并比较。

本次从注册表生成 36 个工具，完整数组合计估算 **15,123 tokens**；每工具单独估算相加与数组总数可能因序列化取整略有差别。

此估算使用 `creatoros.ai.context.estimate_tokens`：序列化后 ASCII 字符约 4 个算 1 token、非 ASCII 字符按 1 个算 1 token，再取整；不是模型 tokenizer 或供应商实际 usage。它衡量工具声明体积，不代表这些内容都冗余，也不含 system、账号资料、历史和用户消息。

范围为 `ACCOUNT_TOOLS`、`creator_id` 已绑定、`archive_only_reads=True` 的 Web 账号会话；宿主先筛选工具、重写特定 description、隐藏 `creator_id`、移除可机械还原的参数标题。`creator_id` 由 `bind_account_arguments` 为表中‘是’的工具注入；这不说明表中‘否’的工具没有账号权限校验。数据仅为工具元数据，不含真实账号或工具调用。

选中的十个工具完整模型可见 JSON 见 [10 个工具完整说明](ten-tools.md)。

| 排名 | 工具 | 估算 token | 宿主绑定账号 |
|---:|---|---:|---|
| 1 | `start_content_run` | 2,104 | 否 |
| 2 | `manage_plan` | 1,481 | 否 |
| 3 | `manage_topics` | 1,236 | 否 |
| 4 | `update_producer_skill_file` | 876 | 否 |
| 5 | `manage_media_collection` | 810 | 是 |
| 6 | `manage_skill_iteration` | 721 | 否 |
| 7 | `manage_skill_library` | 588 | 是 |
| 8 | `research_series_topics` | 500 | 否 |
| 9 | `get_producer_skill` | 476 | 否 |
| 10 | `update_series_composition` | 391 | 否 |
| 11 | `manage_material` | 387 | 是 |
| 12 | `read_file` | 361 | 否 |
| 13 | `create_skill_draft` | 352 | 否 |
| 14 | `edit_extracted_skills` | 344 | 否 |
| 15 | `compose_series` | 336 | 是 |
| 16 | `get_creator_tasks` | 329 | 是 |
| 17 | `prepare_topic_selection` | 319 | 否 |
| 18 | `discuss_content_run` | 295 | 否 |
| 19 | `prepare_publication` | 255 | 否 |
| 20 | `manage_production_task` | 244 | 否 |
| 21 | `request_content_revision` | 219 | 否 |
| 22 | `read_image` | 214 | 否 |
| 23 | `get_topic_research` | 206 | 否 |
| 24 | `list_producer_skills` | 204 | 否 |
| 25 | `get_publication` | 197 | 否 |
| 26 | `save_extracted_skills` | 187 | 否 |
| 27 | `query_feedback` | 186 | 是 |
| 28 | `extract_skill_from_images` | 185 | 否 |
| 29 | `read_tool_result` | 178 | 否 |
| 30 | `get_skill_extraction` | 176 | 否 |
| 31 | `import_content_images` | 174 | 否 |
| 32 | `get_content_run` | 143 | 否 |
| 33 | `list_creators` | 136 | 否 |
| 34 | `get_content_discussion` | 111 | 否 |
| 35 | `cancel_skill_extraction` | 106 | 否 |
| 36 | `list_creator_series` | 98 | 是 |
