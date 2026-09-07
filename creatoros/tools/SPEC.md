# CreatorOS Tool Exposure SPEC

## Web 宿主范围（2026-09-08）

- RuntimeContext.allowed_tools 为可选宿主范围；Loop 过滤模型 schema，执行器再次拒绝范围外请求。默认 CLI 保留既有完整工具集合。
- Web 只使用五个 Studio 工具。RuntimeContext.studio_url 显式传到同一 HTTP Client，测试库/自定义端口不通过改全局环境变量实现，避免宿主串线。
- 验证见 `docs/agent-studio/web-chat/SPEC.md`，本轮没有新增另一套业务工具或自动批准能力。

## 本轮目标

- 保留完整 `tool_registry` 供执行器、Skill Runner 和后台编排使用。
- 允许某些组合工具只作为内部 Python 能力存在，不默认暴露给 LLM。
- 默认模型工具列表只发送标记为可暴露的工具 schema；原子工具仍保持现有参数和执行行为。

## 当前假设

- `route_and_answer` 是 `route_hotspots → 选择 → ask_author` 的组合 Runner，交互 Skill 应优先让 LLM 编排原子工具，因此暂不放入默认模型 schema。
- `tool_registry` 仍必须保留 `route_and_answer`，已有 Runner 和 smoke test 继续通过统一执行入口调用它。
- “不暴露”只影响发给模型的 `tools` 参数，不影响直接通过 `execute_tool_call` 执行。
- `route_hotspots` 的作者队列为每个候选提供作者内 `position`，让 Skill 可以把一次用户选择明确绑定到一个作者和一个热点。

## 验收

- `route_and_answer` 出现在 `tool_registry`，但不出现在默认 `tools` schema。
- `read_file`、`route_hotspots`、`ask_author` 等原子工具仍出现在默认 schema。
- 既有工具执行 smoke 通过，新增验证能证明模型工具暴露边界稳定。

## Codex 内容生产 Tool

- `produce_content_pack(creator_id, series_id, topic_id, topic_title)` 把已选题目交给固定的 `knowledge-to-carousel` Skill，不让 Codex 决定栏目或选题。
- 一次 Tool 调用对应一个新 Codex thread；结果返回内容包路径与 `thread_id`，前台等待期间不重复调用主 Agent LLM。
- 生产失败返回结构化 ToolResult；成功结果只向模型投影摘要，不把图片字节或完整 JSONL 塞入上下文。
- `codex_producer_smoke=passed`，Registry schema、参数路径约束、回执解析、图片归属和 Manifest 验收通过。

## Agent → Studio（2026-09-07）

- 新增 5 个模型工具：list_creators、list_creator_series、list_series_topics、start_content_run、get_content_run，复用 Studio HTTP 接口。
- 旧 produce_content_pack 留给底层兼容调用，从默认 tools 隐藏。Agent Loop 用 model_requested=True 执行工具，拒绝历史消息或幻觉调用隐藏工具；宿主内部调用兼容保留。
- start 仅接 topic_id，返回 accepted/status/run_id/url；只提交尚未尝试的首版 queued Run，其余返回现状。查询状态不返回完整 Revision、图片或 Trace。
- 查询分页保留 total；busy/版本冲突/未知网络结果不自动重试。规范与验证见 docs/agent-studio/SPEC.md。
