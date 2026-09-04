# External Integrations SPEC

## 本轮目标：Codex Content Producer

- 把已登录的 Codex CLI 作为端到端图片轮播生产工具，通过 `codex exec --json --output-schema` 获取结构化生产回执。
- 一篇内容新建一个可恢复的 Codex thread；CreatorOS 保存 `thread_id`，后续返工可按该 ID resume。
- Codex 只返回卡片内容与生成图片的源路径；CreatorOS 负责复制图片、写入最终 `social_content_pack.json` 并使用 `SocialContentPack.load()` 验收。
- Tool 只接收 Creator/Series/Topic 身份与题目，固定使用 `knowledge-to-carousel` Skill；不让模型自行选择栏目或 Skill。

## 当前边界

- CLI 可前台同步等待；Studio 通过 `ManagedRunExecutor` 在单独线程中提交生产，HTTP 只返回已持久化的 Run 状态。JSONL 出现 `thread.started` 时仍立刻持久化句柄；中断与技术重试由 runs 模块管理。
- Producer 支持向指定 Revision/Attempt 目录生产，并用 `codex exec resume` 续接同一篇内容的 thread。
- 执行器只接收一个显式任务；忙时返回当前 Run，不自动排队或调度。
- 不实现长期栏目会话；每个新 topic 使用新 thread。
- 不信任 Codex 返回的最终身份字段，也不允许它指定任意目标目录。
- Producer prompt 接收 `topic_brief`、`series_description` 和 `audience`；老的 `produce_to` 调用不传这些字段时保持兼容。
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

## S4 验证与子进程边界（2026-09-04）

- `produce_to` 接收可选停止 Event 和进程生命周期 callback；旧 Tool 参数保持兼容。完整 JSONL 保存到 Attempt 目录的 codex_trace.jsonl，HTTP 仅暴露 trace 是否存在。
- Windows 先以 suspended/no-window 启动进程，加入 kill-on-close Job Object，记录 PID/创建时间后再恢复执行；超时、宿主关闭和异常均只回收本次 Job 内的进程树。POSIX 用独立进程组，尚未进行 Linux 集成验收。
- `smoke_studio_process` 在本机 Windows 通过真实无费用子进程故障注入；`smoke_codex_producer` 的纯解析/落盘回归通过。
- 收尾运行 `live_codex_resume_protocol`：真实新建与 resume 共 2 次请求，同一 thread `01a06b41-bb2d-7332-958c-4a064019eee0` 通过，不调用生图。该旧探针未打印 usage，不能据此报告 token 数为 0。
- 本阶段早期还曾从隔离页面提交真实生产并停止；没有把它算作完整生图验收。最终页面耗时/故障 QA 使用隔离受控 Producer，完整真实图片生产留到 S7。
