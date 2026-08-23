# CreatorOS Runtime SPEC

Progressive SPEC, not a form.

## 当前理解

- CreatorOS 是面向创作者 / MCN 场景的长期项目；当前阶段只从零学习并手写极简 Python Agent Runtime。
- 用户具备 Python 基础和简单 Agent Loop 经验，希望逐层理解 Agent Harness / Runtime，并能在求职和面试中讲清工程取舍。
- Pi Agent 只作为架构参考；实现应从 Python 的最小可运行版本逐步演进。
- 第一个可运行切片已经完成：Python 通过 OpenAI-compatible SDK 调用 DeepSeek，并打印一条回复。
- Pi 当前给我们的一个具体启发是：模型适配和 Agent 状态属于不同问题；本轮只把消息历史显式化，不复制 Pi 的 TypeScript 包结构。
- 第二个可运行切片开始使用同一份消息列表进行两次调用；重复的调用代码已经成为真实的设计痛点。
- 第三个可运行切片把重复的两轮调用收敛成一个最小 `while` Agent Loop。
- 第四个可运行切片加入一个无副作用的 `get_current_time` Tool，开始观察模型请求工具、Runtime 执行工具、模型继续回答的闭环。
- 第五个可运行切片加入第二个无副作用的 `get_current_date` Tool，工具定义和执行分支的重复已经出现。
- 第六个可运行切片加入最小 `tool_registry`，用名称查找替换工具执行处的 `if/elif`。
- 第七个可运行切片把名称、描述、参数 schema 和执行函数合并到一个 `Tool` 对象中，并让它生成模型 schema。
- 第八个可运行切片加入第一个带参数的 `read_file(path)` Tool，开始验证模型返回的 JSON arguments 如何进入本地函数。
- 第九个可运行切片给 `read_file` 增加可选的 `offset` 和 `limit`，开始验证同一个 Tool 的可选参数和分段结果。
- 第十个可运行切片把工具查找、参数解析和异常转换收敛到 `execute_tool_call`，避免单个坏调用击穿 Agent Loop。
- 第十一个可运行切片加入第一个有副作用的 `write_file(path, content)` Tool，默认拒绝覆盖已有文件。
- 第十二个可运行切片把 `read_file` 的参数模型迁移到 Pydantic：同一个 `ReadFileArgs` 同时生成模型 schema 和执行前的运行时校验。
- 第十三个可运行切片把 `write_file` 的参数模型也迁移到 Pydantic，并删除它的手写参数 schema。
- 第十四个可运行切片加入最小 `DeepSeekProvider`，把模型 SDK 调用从 Agent Loop 中移出。
- 第十五个可运行切片定义 `ModelProvider`、`ModelResponse` 和 `ToolCall`，让 Agent Loop 不再读取 OpenAI SDK 的响应对象。
- 第十六个可运行切片让 `run_agent(provider)` 接收 Provider，实现依赖注入，并让导入模块不再自动启动 CLI。
- 第十七个可运行切片把一次模型请求命名为 Runtime 层的 `llm(...)`，让 Agent Loop 不直接调用 Provider 方法。
- 第十八个可运行切片把一条 `role="system"` 消息预置到 Agent 的内部消息历史中，并验证 Provider 保留该角色。
- 第十九个可运行切片为每次工具调用和工具结果增加最小终端 trace，让 Runtime 的中间过程可见。
- 第二十个可运行切片把完整 `messages` 快照保存到本地 JSON 文件，支持启动恢复、`/reset` 和 Ctrl+C 保存。
- 第二十一个可运行切片接入真实 DeepSeek Streaming：Provider 通过 OpenAI-compatible SDK 消费 SSE，Runtime 用内部增量事件累积文本和工具参数，终端即时显示文本，完整 assistant/tool 消息仍在每个模型 turn 结束后保存。
- 第二十二个可运行切片让 `stream_llm` 通过可选 `on_event` 把增量事件交给上层，并在完整工具调用形成后发出 `ToolCallEnd`；本轮只建立事件可见性，不提前执行工具。
- 第二十三个可运行切片只做目录结构重构：把单文件 Runtime 拆成 `ai`、`agent`、`tools`、`session` 和 `cli` 责任域；根目录 `main.py` 保留为兼容入口，不改变工具、Provider、Streaming、会话或 CLI 行为。
- 第二十四个可运行切片加入最小 `AgentState`：用一个 dataclass 持有当前运行的 `messages`、`status` 和模型调用 `turn` 计数；本轮不加入 Context、pending tool、取消或持久化状态机。
- 第二十五个可运行切片加入统一 `ToolResult`：所有内置工具返回同一种结果对象，保留原有 `content` 文本，同时增加 `is_error`、`error_type`、`retryable` 和 `details`；本轮不自动重试、不执行终端命令。
- 第二十六个可运行切片增加 `ToolResult.to_model_content()`：把内部结果投影为安全的模型可见文本；成功结果保持原文，错误结果增加稳定的 `tool_error` 类型前缀，不暴露 `details` 或自动重试策略。
- 第二十七个可运行切片加入最小 `MaxTurnGuard`：按单个用户任务限制模型调用次数，在下一次模型请求前停止；本轮不加入重复调用检测、自动重试或超时。
- 第二十八个可运行切片把 `MaxTurnGuard` 的默认单任务上限从 12 调整为 30，并集中为 `DEFAULT_MAX_TURNS`；本轮不改变 Guard 的检查时机或累计计数语义。
- 设计决定：当前不实现通用 `RepetitionGuard`。先让模型利用工具结果自行修正，保留 `MaxTurnGuard` 作为确定性的资源保险丝；只有出现可复现的无进展循环证据时，才引入最小、可解释的提醒或停止策略。Pi 核心提供停止/工具钩子，重复检测主要存在于第三方扩展，而不是核心 Runtime 的强制行为。
- Guardrail 审计结论：当前 `MaxTurnGuard` 只覆盖模型调用次数；Pydantic、路径边界和 `ToolResult` 已覆盖一部分输入/结果正确性，但仍缺少敏感文件保护、内容/大小上限、Provider 超时/取消、工具调用预算、风险分级/审批、审计记录和不可信工具结果边界。
- 面向未来 CreatorOS 创作者运营 Agent，Guardrail 应按阶段和副作用分层：研究阶段重视来源与不可信内容隔离，创作阶段重视结构/品牌/平台规则，发布阶段重视账号范围、预览、幂等键和人工审批，分析阶段默认只读并要求数据来源与异常校验。
- 存储校准：Pi 默认按工作目录把会话保存为 JSONL 文件；OpenAI Agents SDK 提供文件型 SQLite、SQLAlchemy、Redis 等 Session；LangGraph 使用 Checkpointer，可选内存、SQLite、Postgres、Redis 等后端。参考：[Pi Sessions](https://pi.dev/docs/latest/sessions)、[OpenAI Agents Sessions](https://github.com/openai/openai-agents-python/blob/main/docs/sessions/index.md)、[LangGraph Checkpointers](https://docs.langchain.com/oss/python/integrations/checkpointers/index)。CreatorOS 当前选择最小的本地 JSON 快照，不提前引入数据库或完整 Session 抽象。
- 架构校准：OpenAI Agents SDK 提供 `ModelProvider` / `FunctionTool`，AutoGen 提供 `ChatCompletionClient` / `CreateResult`，LangChain 为不同厂商提供统一 Chat Model 接口；Pi 的 `Provider` 负责认证、模型目录和流式请求，`Models` 负责 Provider 集合。参考：[OpenAI Agents](https://openai.github.io/openai-agents-python/models/)、[AutoGen](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/components/model-clients.html)、[LangChain](https://docs.langchain.com/oss/python/concepts/providers-and-models)、[Pi](https://github.com/earendil-works/pi/blob/main/packages/agent/docs/models.md)。CreatorOS 当前只翻译最小的同步 `complete` 边界。

## 长期路线与当前定位

CreatorOS 按“先 Runtime、后业务产品”的路线推进，不按临时问题随机堆功能：

1. **Runtime 学习基础（当前阶段）**：LLM 调用、消息、Agent Loop、Tool Calling、Tool Registry、Pydantic、Provider、Streaming、Session、最小 State。
2. **Runtime 正确性与可恢复性**：Agent Context、Agent Message / LLM Message 分离、Compaction、错误与重试、Max Turn、重复调用、取消和超时。
3. **Runtime 运行能力**：Events、Observability、Hooks、并发工具、Human-in-the-loop、MCP 和 Evaluation。
4. **CreatorOS 业务能力**：Trend Discovery、Creator Routing、PersonaForge Service / Tool、Research、Content Planning、Content Generation、Judge / Review。
5. **产品闭环**：Human Approval、Publishing、Analytics Feedback、Working Memory / Long-term Memory、权限、账号隔离和多用户运行。

当前位于第 1 阶段；第 4 阶段的 Creator 业务不是遗漏，而是等 Runtime 边界稳定后再接入。现有四个工具是 `get_current_time`、`get_current_date`、`read_file`、`write_file`，它们只是用来验证 Runtime 的工具机制，不是 CreatorOS 最终业务工具。

## 本轮目标

本轮只加入最小的 `MaxTurnGuard`，并保持现有工具执行、Streaming 和 CLI 行为：

- `creatoros/agent/guards.py` 定义 `MaxTurnGuard(max_turns)`，拒绝小于 1 的上限。
- `run_agent(..., max_turns=30)` 在每个用户任务开始时记录累计 `state.turn`，用差值计算本任务已用模型调用次数；调用方仍可传入更小或更大的值。
- Guard 在递增下一次 `state.turn` 之前检查；达到上限后把 State 置回 `idle`、打印 Guard 提示并保存已有消息。
- 本轮不加入重复调用检测、自动重试、终端执行、超时、Event 总线或 Agent Context。

## 当前假设

- 第一段 Runtime 代码只验证一次 OpenAI-compatible LLM 调用，不包含循环、工具或抽象层；本轮已验证。
- 初始模型使用 `deepseek-v4-flash`，通过 `https://api.deepseek.com` 调用；后续仍可在理解 Provider 后调整。
- `.env` 由 `python-dotenv` 在程序启动时加载，且由 `.gitignore` 排除。
- 当前 `messages` 是一次 CLI 运行内的工作状态：每轮按 user → assistant 顺序追加消息。
- `/exit` 只控制本地循环，不会发起模型请求。
- 外层循环处理用户轮次，内层循环处理同一轮中的“模型 → Tool → 模型”子轮次。
- `get_current_time` 和 `get_current_date` 都没有参数和副作用；未知工具暂时返回错误文本给模型。
- 当前每个 Tool 同时提供执行函数和模型 schema；`tools` 列表由 Registry 中的 Tool 对象生成。
- `read_file` 接收相对于项目根目录的路径；路径会先解析并拒绝项目目录之外的目标；文件按 UTF-8 文本读取。
- `read_file.offset` 从 1 开始，默认为 1；`read_file.limit` 可选，省略时读取到文件结尾；分段结果会提示下一次 `offset`。
- `execute_tool_call` 捕获单次工具调用的普通 `Exception` 并返回 `ToolResult`；`ValidationError` 标记为 `invalid_arguments`，未知工具标记为 `unknown_tool`，其他异常标记为 `tool_exception`。
- `write_file` 是当前第一个有副作用的 Tool；它创建新文件但不覆盖已有文件，路径边界和错误仍由 Tool 自己处理。
- `read_file` 的 JSON arguments 在进入函数前由 `ReadFileArgs` 校验；`strict=True` 不把字符串自动转换成整数，`extra="forbid"` 拒绝未声明字段。
- `write_file` 的 JSON arguments 在进入函数前由 `WriteFileArgs` 校验；写入函数不再重复检查 `path` 和 `content` 的类型。
- `Tool` 可选持有一个 Pydantic args model；有 model 时由它生成 JSON schema 并解析 arguments，没有 model 时继续使用原来的手写解析。
- `read_file` 和 `write_file` 已迁移到 Pydantic；无参数 Tool 仍使用手写空参数 schema，便于保持当前实现简单。
- `DeepSeekProvider` 持有 OpenAI-compatible client 和模型配置；`complete` 负责一次模型请求，Agent Loop 不再直接调用 SDK。
- 当前只有一个 DeepSeek Provider；Provider 只是边界，不等同于已经完成可插拔的多模型架构。
- `ModelProvider` 是结构化协议；`DeepSeekProvider` 返回 `ModelResponse`，其中的 `ToolCall` 不携带 OpenAI SDK 对象。
- Agent Loop 使用内部 assistant message 保存历史；DeepSeek Provider 在请求前把内部 assistant tool calls 转换为 OpenAI 的嵌套 function 格式。
- `run_agent` 只依赖 `ModelProvider`，FakeProvider 可以替换 DeepSeekProvider；`messages` 成为一次 `run_agent` 调用的局部状态。
- `llm(...)` 只负责一次模型 turn，并把 Provider 返回的 `ModelResponse` 原样交给循环；它暂不处理错误、统计、事件或重试。
- `stream_llm(...)` 只负责消费 Provider 的内部流式事件；文本立即刷新到终端，工具调用按 index 拼接，流结束后才交给 Agent Loop。
- `stream_llm(..., on_event=...)` 仍返回完整 `ModelResponse`，同时把运行时事件通知观察器；观察器可以记录事件，但本轮不改变执行时机。
- `run_agent` 仍负责消息历史、工具执行和循环控制；Provider 仍负责厂商 SDK 胶水代码。
- `AgentState` 是一次 `run_agent` 调用内的内存工作状态；其中 `messages` 仍是原来发给模型和保存到 Session 的消息列表，`status` 当前只使用 `idle` / `running`，`turn` 按模型请求次数递增。
- 本轮 `run_agent` 仍不返回 State；先验证 State 能承载消息和最小运行元数据，再决定是否开放快照、观察器或恢复接口。
- 本轮最小 `AgentState` 不代表已经完成 Agent Context、pending tool 状态、并发执行、取消或事件总线；这些仍保持在后续范围。
- `SYSTEM_PROMPT` 当前是源码中的固定常量，作为第一条 `role="system"` 消息发送；后续再决定是否由配置或 Runtime Context 提供。
- 当前 Tool trace 直接写到终端 stdout；模型消息经过 `to_model_content()` 投影，错误可能增加类型前缀，结果可能较长，截断和结构化展示留到后续 Observability/UI 步骤。
- `ToolResult.content` 是给模型和当前终端显示的文本；`details` 只保存结构化诊断，当前不写入消息快照，也不包含终端命令输出。
- `ToolResult.retryable` 只是工具提供的保守提示，本轮没有任何自动重试逻辑；Guard 以后再消费它。
- `ToolResult.to_model_content()` 是内部结果到 LLM 消息的投影，不是让模型填写 `ToolResult` 的输入 schema；模型仍只生成 `ToolCall`。
- `MaxTurnGuard` 是 Harness 层的运行时保险丝，不是 Tool，也不是发给模型的指令；`AgentState.turn` 仍然是整个 `run_agent` 调用期间累计的总次数。
- `DEFAULT_MAX_TURNS` 当前为 30，只是学习项目的默认保险丝，不代表所有任务都应该运行 30 次；生产系统还需结合预算、超时和工具风险设置。
- Guardrail 在 CreatorOS 中先只表示运行时边界检查，例如模型调用上限、参数/结果校验和未来的副作用审批；它不替代基模对任务策略的判断。
- 当前不实现通用 `RepetitionGuard`，也不把工具名重复、A-B-C 周期等启发式判断提前塞进 Agent Loop；若真实运行中出现无进展循环，再根据证据设计最小规则。
- 当前不把所有未来 Guardrail 提前抽象成大框架；先在引入真实副作用工具前补齐最小的敏感路径/大小限制，再根据工具风险演进到审批、预算和审计。
- Pi 的 `AgentToolResult<T>` 同样把模型内容与通用 `details` 分开，并支持 `usage`、动态工具名和 `terminate`；Pi 的工具执行契约要求失败抛出异常，Runtime 再把失败纳入工具结果和事件。参考：[Pi Agent types](https://github.com/earendil-works/pi/blob/main/packages/agent/src/types.ts)。
- 当前会话存储是单个 JSON 快照；每次保存先写临时文件再替换目标文件，避免直接覆盖时留下半个 JSON 文件。
- `sessions/latest.json` 只用于本地恢复，可能包含用户输入、工具参数和文件内容，因此必须被 `.gitignore` 忽略。
- 项目早期所有模块共享根级 `SPEC.md`；出现稳定模块边界后，再在最近模块建立独立 `SPEC.md`。

## 对外影响

- 本轮让 Agent Loop 在每个用户任务的下一次模型请求前执行 `MaxTurnGuard`；达到上限时不再发起新请求，之前的 assistant/tool 消息仍被保存。
- 本轮让 Provider 成为 `run_agent` 的输入依赖，CLI 启动只发生在直接运行 `main.py` 时；导入模块可进行离线测试。
- 本轮为 Runtime 增加 `llm(...)` 命名边界；未来可以在这个边界逐步加入统一错误、统计或事件，但当前行为不变。
- 本轮为 Streaming 增加可选事件观察器；默认不传观察器时终端和会话行为保持不变。
- 本轮让内部消息历史显式包含系统指令；Chat Completions Provider 原样保留该角色，未来 Responses Provider 可将其映射为 `instructions`。
- 本轮让用户能看到工具调用和工具结果，但它们仍只存在内存和终端，不形成持久化记录。
- 本轮将当前消息历史持久化到本地快照；这不是多用户数据库，也不支持会话树、并发写入或跨机器共享。
- `.env` 只存在于本地工作区，不会被提交或推送；代码依赖 `openai`、`python-dotenv` 与 Pydantic 2.x。

暂未确认：

- `creatoros` 包的长期公共导出和版本化接口；本轮只保留 `main.py` 兼容导出。
- 未来 Provider 抽象如何承载 DeepSeek 与其他模型；这留到后续步骤。
- `AgentState`、Session 和 Agent Context 的最终边界；本轮只确定 State 是运行时容器，Session 负责持久化，Context 仍未实现。
- 模型请求失败时如何恢复、自动重试、超时和取消；这些属于后续 Guard/错误处理步骤；通用重复检测当前明确暂缓。
- 无参数 Tool 是否也使用 Pydantic，以及是否为所有 Tool 统一 args model；当前空参数 schema 仍足够简单。
- 如何支持第二个模型、模型能力差异、认证和模型目录；先用一个真实 Provider 验证接口，再扩展。
- Provider 请求失败如何转换为统一错误结果；留到后续错误处理步骤。
- Streaming 当前假设一次只处理一个模型 turn；事件中断时不会保存半截 assistant 消息，取消、重试、超时和增量恢复留到后续步骤。
- `SYSTEM_PROMPT` 是否应该成为 `run_agent` 参数、Provider 配置还是 Context 字段；当前先固定在源码中观察实际需求。
- Tool trace 是否应升级为统一 Event、日志级别或可关闭输出；当前只使用两个固定前缀。
- 是否从单个 JSON 快照迁移到 Pi 风格 JSONL、SQLite Checkpoint 或生产数据库；等会话查询、并发和分支需求出现后再决定。
- 会话 ID、多个用户、多会话列表和历史恢复 UI；当前只有 `latest.json`。
- Runtime 层 `llm(...)` 未来是否需要接收 Context、模型选项或取消信号；当前只接收 Provider、messages 和 tools。
- `ToolCallEnd` 目前由 Runtime 在整轮流结束后派生；未来是否由 Provider 提供每个工具调用的原生结束事件，留到 Provider 能力扩展时决定。
- Pydantic 验证错误的用户展示格式和自动重试策略仍未确定；本轮已增加 `invalid_arguments` 类型和原始校验详情，但不自动重试。
- 文件内容过大、二进制文件、并发读取和工具超时；这些属于后续 Guard/资源限制步骤。
- Pi 风格的字节级截断、图片内容块、AbortSignal 和 UI 渲染；这些暂不翻译到 Python 版本。
- 工具异常是否需要重试、完整日志和用户可见诊断仍未确定；本轮只保存异常类型，不保存 traceback 或终端输出。
- 有副作用 Tool 是否需要每次调用前的人类确认、显式覆盖参数和更细粒度的写入范围；这些留到 Guards / Human-in-the-loop。
- 拆分后的模块是否进一步细分为多个 Python 包；当前单个 `creatoros` 包足够，先不复制 Pi 的 monorepo 和 workspace 层级。

## 验收与验证草案

- `main.py` 能从本地 `.env` 读取 `DEEPSEEK_API_KEY`，从 `Tool` 对象生成 schema，通过 `execute_tool_call` 解析 JSON arguments 并执行 `read_file` 或 `write_file`，再追加正确的 `tool_call_id`。
- `read_file` 对项目内 UTF-8 文件返回指定行段，对项目外路径、目录、缺失文件、非 UTF-8 文件和越界行号返回可供模型理解的错误文本。
- `read_file` 的 schema 包含 `path`、`offset`、`limit` 约束，并标记禁止额外字段。
- `read_file` 拒绝字符串形式的整数、零或负数范围、缺少 `path` 和未知字段；合法参数仍能读取指定行段。
- 坏 JSON、非 object 参数、未知工具和 Tool 内部异常不会让 Agent Loop 直接退出，而会变成工具结果文本。
- `write_file` 对项目内新路径写入 UTF-8 文本，对项目外路径、已有文件、缺失父目录和非字符串参数返回错误文本。
- `write_file` 的 schema 包含 `path` 和 `content` 两个字符串字段，并标记两个字段为必填、禁止额外字段。
- `write_file` 的字符串类型和额外字段错误在进入写入函数前被 Pydantic 拦截。
- 所有内置 Tool 的 `execute_tool_call` 返回值都是 `ToolResult`；成功结果的 `content` 与上一轮字符串结果一致。
- 文件缺失、路径越界、参数错误和未知工具结果分别带有稳定的 `error_type`；异常详情至少包含验证错误或异常类名。
- `ToolResult` 的 `details` 不会被自动拼进模型消息，避免把内部诊断和未来的原始终端输出无限扩大到上下文。
- 成功结果的 `to_model_content()` 与 `content` 完全一致；失败结果包含 `[tool_error type=...]`，但不包含 `details` 字段内容。
- `MaxTurnGuard(2)` 在使用 0、1 次模型调用时继续，在第 2 次调用前停止；`MaxTurnGuard(0)` 拒绝创建。
- 未传 `max_turns` 时，`run_agent`、根入口 `main.run_agent` 和 `MaxTurnGuard` 都使用同一个 `DEFAULT_MAX_TURNS == 30`。
- `run_agent(..., max_turns=1)` 的 Fake Loop 只发起一次模型请求，执行其工具结果后停止，不影响下一次用户任务重新计数。
- 没有 args model 的现有 Tool 仍能解析原来的 JSON object 参数。
- `DeepSeekProvider.complete` 使用原来的 DeepSeek endpoint、模型和 `thinking` 配置，并把完整响应返回给 Agent Loop。
- `ModelProvider` 的结果包含 CreatorOS 内部的 `ModelResponse` 和 `ToolCall`，Agent Loop 源码不再读取 `response.choices[0].message`。
- DeepSeek 请求中的 assistant tool calls 仍能转换为 OpenAI-compatible 的 `function` 嵌套格式。
- `run_agent(FakeProvider)` 能完成普通回答和一次工具调用闭环；导入 `main` 不会触发输入提示或真实 API 初始化。
- `llm(FakeProvider, messages, tools)` 返回内部 `ModelResponse`，且 Agent Loop 源码不再直接调用 `provider.complete(...)`。
- `run_agent(FakeProvider)` 的第一条请求包含 `role="system"`，第二条工具请求仍保留 system、assistant 和 tool 消息。
- `DeepSeekProvider._to_openai_messages` 转换 system 消息后仍保留相同角色和内容。
- FakeProvider Tool Calling smoke 的 stdout 包含 `[Tool call]`、`[Tool result]` 和最终 Agent 回复。
- 会话快照 smoke 能保存并恢复 system、assistant、tool 消息；`/reset` 只保留新的 system 消息；KeyboardInterrupt 后文件仍存在。
- `git check-ignore -v sessions/latest.json` 能命中 `sessions/` 规则。
- `stream_llm(FakeStreamingProvider)` 能把文本 delta 拼成完整回答，把分片工具参数拼成合法 JSON，并完成工具闭环。
- `stream_llm(..., on_event=...)` 能按 `TextDelta`、`ToolCallDelta`、`ToolCallEnd`、`StreamEnd` 顺序通知观察器。
- `run_agent(FakeProvider, on_stream_event=...)` 仍能完成普通回答和工具调用；未提供观察器时行为与上一轮相同。
- `AgentState(messages=[])` 默认处于 `idle`、`turn == 0`，并能在 Fake Agent Loop 中承载消息列表、切换为 `running` 和递增 `turn`。
- `run_agent(FakeProvider)` 使用 `state.messages` 后，保存的消息内容和上一轮完全一致；模型调用次数等于 `state.turn` 的递增次数。
- `python main.py` 仍从根目录启动同一个 CLI；`from main import ...` 的现有内部符号仍可用，作为兼容层验证。
- 拆分后的所有 `.py` 文件均可通过 `deepcode` 编译，包内导入不产生循环导入。
- 现有 Fake Streaming、会话恢复和工具参数 smoke 在新目录结构下仍通过。
- 真实 `.env` 下 `DeepSeekProvider.stream(...)` 能收到至少一个流式事件和最终 `StreamEnd`，不输出 API Key。
- `requirements.txt` 包含 Pydantic 2.x 及其他运行所需依赖；`.env` 被 `.gitignore` 忽略。
- 仓库没有提交 API Key、完整 `.env`、Python 缓存或其他秘密信息。
- 本轮出现最小 `Tool` 类和 Registry，不出现 Session、权限或其他未学习的抽象。

优先运行：

```powershell
python -m pip install -r requirements.txt
python -m py_compile main.py
python main.py
git check-ignore -v .env
git diff --check
```

## 最近验证

- 日期：2026-08-23
- 状态：最小 AgentState、ToolResult 和模型内容投影已通过既有 smoke；MaxTurnGuard 默认值调整已完成验证并在 `8483c13` 提交、推送；`502b9d7` 已记录“不实现通用 RepetitionGuard”；本轮完成 Guardrail 缺口与 CreatorOS 风险分层审计，未改运行逻辑。
- `conda run --no-capture-output -n deepcode python -m compileall -q main.py creatoros` 通过。
- `tool_result_smoke=passed`：成功读取、文件不存在、Pydantic 参数错误和未知工具均返回结构化 `ToolResult`。
- `compat_smoke=passed`：根入口 `main.read_file`、`main.get_current_date` 等兼容函数仍返回字符串，`main.execute_tool_call` 暴露 `ToolResult`。
- `model_content_smoke=passed`：成功结果保持原文，文件错误带有 `tool_error` 类型前缀，内部 `details` 未进入模型内容。
- `max_turn_guard_smoke=passed`：Guard 的阈值判断和 `max_turns=1` 的 Fake Loop 均通过，只发起一次模型请求。
- `default_max_turns_smoke=passed`：`DEFAULT_MAX_TURNS`、`MaxTurnGuard()`、`run_agent` 和根入口默认值统一为 30。
- `git diff --check` 和 staged diff 检查通过；`8483c13` 已推送到 `origin/main`。
