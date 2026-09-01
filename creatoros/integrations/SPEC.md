# External Integrations SPEC

## 本轮目标：Codex Content Producer

- 把已登录的 Codex CLI 作为端到端图片轮播生产工具，通过 `codex exec --json --output-schema` 获取结构化生产回执。
- 一篇内容新建一个可恢复的 Codex thread；CreatorOS 保存 `thread_id`，后续返工可按该 ID resume。
- Codex 只返回卡片内容与生成图片的源路径；CreatorOS 负责复制图片、写入最终 `social_content_pack.json` 并使用 `SocialContentPack.load()` 验收。
- Tool 只接收 Creator/Series/Topic 身份与题目，固定使用 `knowledge-to-carousel` Skill；不让模型自行选择栏目或 Skill。

## 当前边界

- 前台同步等待一次生产完成，不实现后台队列、调度、取消、重试或发布。
- 不实现长期栏目会话；每个新 topic 使用新 thread。
- 不信任 Codex 返回的最终身份字段，也不允许它指定任意目标目录。
- 本轮 smoke 隔离 JSONL 解析与落盘；低频真实 `codex exec` 另行验证。

## 验收

- 能解析 `thread.started`、最终结构化 agent message 与 `turn.completed` usage。
- 图片只能从 Codex 生成图片目录读取，复制后生成有效 `SocialContentPack`。
- `produce_content_pack` 进入 Tool Registry，结果包含 pack 目录、Manifest 和可恢复 thread ID。

## 真实验证

- `live_codex_producer=passed cards=6`：真实 Codex thread `01a05ef3-c515-7600-b6d9-57f02ac34473` 生成 6 张图片，CreatorOS 复制后重新加载 Manifest 通过。
- 本次 usage 为 input 371,066、cached input 287,232、output 7,608；完整 JSONL 和图片不进入 Agent messages。
- 使用保存的 thread ID 执行 `codex exec resume`，WebSocket 重试后自动回退 HTTPS 并返回 `RESUME_OK`；续接请求为 input 47,341、cached input 46,720，证明同篇内容可恢复且前缀缓存命中。
- 真实画面逐张检查通过：中文清晰、风格一致、机制/原因/对比/处理顺序完整；未执行平台发布。
- 全量 42 个 smoke 中 41 个通过；唯一失败的 `smoke_routing_projection` 会真实连接 PersonClone，当前本地 8000 端口未监听，未用 Fake 掩盖该外部依赖失败。全包 `compileall` 通过。
