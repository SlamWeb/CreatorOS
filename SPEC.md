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
- 第二十九个可运行切片给 `read_file` 增加敏感路径拒绝和 128 KiB 文件大小上限，并加入独立 smoke；本轮不引入统一 Guardrail 框架或改变工具调用时机。
- 第三十个可运行切片加入最小 `RuntimeContext`：记录 `project_root`、操作系统和 Shell，并通过工具执行链传给内置工具；本轮不加入 ModelContext、ArtifactStore、权限、Provider 或消息压缩。
- 第三十一个可运行切片加入最小 CreatorOS 终端品牌启动画面：使用无外部依赖的 ASCII 字母和可选 ANSI 颜色；本轮不改变 Agent Loop、Provider、Tool 或消息行为。
- 第三十二个可运行切片加入最小 `Console` 终端 I/O 适配层：统一输入、普通输出、流式输出和启动画面；本轮不引入 Rich、Textual 或完整 TUI。
- 第三十三个可运行切片加入最小 `AgentEvent`：Runtime 发出模型回合、工具调用、工具结果、Guard 和会话事件，Console 负责默认渲染；本轮不引入完整事件总线或 Rich/TUI。
- 第三十四个可运行切片给 `Console.render_event()` 增加可见状态提示：思考中、工具调用中、工具完成和 Guard 警告；本轮只轮换回合级提示，不启动后台动画线程。
- 第三十五个可运行切片做终端 UI polish：用 `❯` 替代中文输入标签，统一缩进、符号和可选颜色；本轮不改变 Agent 事件和消息语义。
- 第三十六个可运行切片接入 Rich：`RichConsole` 使用 Panel、颜色、Live、Status 和 Markdown 渲染终端；本轮保留自有 Console/Event 接口，不引入 Textual。
- 第三十七个可运行切片收敛 Rich 视觉：删除冗余副标题和大边框，保留彩色字母、Spinner、Live Markdown 和工具状态信息流。
- 第三十八个可运行切片建立 Rich 语义视觉：把大块字母改为单行 `CreatorOS` 字标，把工具 trace 改为 `↳ name` / `✓ done`，并用低饱和品牌紫、灰蓝和浅绿替代高饱和蓝色；本轮不改变 Agent Event、Provider、Session 或消息语义。
- 第三十九个可运行切片修复 Windows 终端流式重绘：恢复大彩色 CreatorOS 字标，并把增长中的 Markdown Live 改为按完整段落追加渲染，避免 PowerShell 中旧帧残留成重复文本；本轮仍保持启动画面只出现一次。
- 第四十个可运行切片收紧 Logo 视觉：用 7×10 半块像素字压缩为五行，同时保留更细的上下像素边缘；本轮不改变正文、工具 trace 或 Status 行为。
- 第四十一个可运行切片接入独立的 PersonClone FastAPI 服务：CreatorOS 通过薄 HTTP Client 和三个 Tool 完成作者列表/选择、添加作者任务和向指定作者提问；本轮不复制 PersonClone 代码，不实现热点发现、自动路由、发布或分析闭环。
- 第四十二个可运行切片加入本地 PersonClone 登录助手：在电脑终端隐藏式读取密码、调用 `/api/auth/login` 并只把会话 Cookie 写入被忽略的 `.env`；本轮不接收或保存用户密码，也不把认证流程塞进 Agent Loop。
- 第四十三个可运行切片加入最小内部异步任务状态：`AgentState.tasks` 持有 `TaskRecord`，用业务状态、heartbeat 和 deadline 区分正常排队、运行中、疑似卡住和已超时；本轮不启动后台 worker、不轮询 PersonClone、不持久化任务表。
- 第四十四个可运行切片加入最小 `ModelContext`：一次模型请求使用只读快照，把开头连续的 system/developer 指令、稳定的工具 schema 和动态消息尾部显式分开；Provider 在发送前还原为“系统消息在前、对话消息在后，工具仍位于独立 tools 字段”的请求。本轮不实现 token 计数、压缩、缓存 key 或 Responses API 迁移。
- 第四十五个可运行切片加入最小上下文预算：对 `ModelContext` 做 Provider 无关的粗略输入 token 估算，预留输出空间；接近或超过预算时发出 `context_warning`，但本轮不自动删除消息、不压缩、不阻断模型请求。
- 第四十六个可运行切片接入 Provider 返回的真实 usage：DeepSeek 非流式响应和流式最后一个 `choices=[]` chunk 都转换为内部 `ModelUsage`；`ModelResponse` 携带 usage，AgentEvent 只做内部 usage 观察，不把统计写进 messages。本轮仍不自动压缩或截断。
- 第四十七个可运行切片修正 PersonClone 外部回答策略：`ask_author` 默认使用不依赖 Narrative Schema 的 `strong_identity`，默认传 `parent_top_k=20`；有 Schema 的作者仍可显式使用 `mrprompt`；`list_authors` 增加 `recommended_writer_prompt`，避免把“有索引”误判为“可使用 mrprompt”。
- 第四十八个可运行切片加入纯本地 `CompactionPlan`：按最近 token 预算从后向前保留完整 user turn，把更早的完整回合划入待摘要区；system/tools 不参与切割，assistant tool call 与对应 tool result 不会被拆开。本轮不调用摘要模型、不修改 Session 或 Agent Loop。
- 第四十九个可运行切片把固定 20k 尾部预算改为 Provider 窗口驱动的动态策略：默认保留可用输入窗口的八分之一，并限制在 8k～128k；DeepSeek 1M 窗口预留 32,768 输出后，保留 120,904 tokens。本轮仍只生成计划，不接入自动压缩。
- 第五十个可运行切片加入纯本地 `CompactionSummaryRequest`：把待摘要消息序列化为明确的 User/Assistant/Tool 历史资料，用独立 system prompt 要求模型只生成结构化 checkpoint，并把单个 ToolResult 截到 4,000 字符；本轮不调用模型、不保存 checkpoint、不接 `/compact`。
- 第五十一个可运行切片加入真实摘要执行边界：`generate_compaction_summary()` 调用注入的 Provider，拒绝 tool call、空响应和缺少必要 Markdown 标题的响应，并返回摘要文本、真实 usage 和请求元数据；使用真实 DeepSeek 验证，不保存 checkpoint、不接 `/compact`。
- 第五十二个可运行切片加入持久化 `CompactionCheckpoint`：在 Session 旁原子保存累计摘要、绝对切分位置、完整 retained tail、源消息数量/哈希、压缩前 tokens 和真实 usage；加载时校验源 Session，损坏或失配则安全回退，不接 ModelContext 投影。
- 第五十三个可运行切片让 Agent Loop 使用有效 checkpoint 投影 `ModelContext`：稳定 system/developer 前缀保持在最前，累计摘要以低权限 user 消息注入，随后拼接 checkpoint retained tail 和 checkpoint 后追加的新消息；原始 Session 不改，`/reset` 清除 checkpoint。
- 第五十四个可运行切片加入内部 `compact_session()`：把 ContextBudget、完整 turn 切分、摘要请求、真实 Provider 生成和 checkpoint 原子保存串成一次事务；无旧 turn 时不调用模型，重复压缩用旧累计摘要更新出一份新累计摘要。本轮不自动触发。
- 第五十五个可运行切片把自动压缩接入 Agent Loop：主模型请求前若 `ContextBudget.needs_attention`，先尝试生成 checkpoint 并重建 `ModelContext`；没有可压缩旧回合或压缩后仍接近上限时才发出警告。本轮不做超限重试或 split-turn。
- 第五十六个可运行切片加入大型 ToolResult 的模型投影：完整内容继续保存在原始 Session，正常主模型请求只接收首尾合计 16,000 字符和 `result_ref`；摘要模型的旧历史副本也改为首尾合计 4,000 字符。本轮不实现按 ID 重读工具。
- 第五十七个可运行切片加入 `read_tool_result`：模型可用投影 marker 中的 `result_ref` 在完整 Session 内精确找到对应 `role="tool"` 消息，并按字符分页读取未截断文本；本轮不引入 ArtifactStore 或数据库索引。
- 第五十八个可运行切片接入知乎官方热榜：薄 HTTP Client 使用 Access Secret 和秒级时间戳读取结构化候选，Agent 只获得标题、链接、摘要与缩略图；本轮不评分、不路由。
- 第五十九个可运行切片接入知乎官方站内搜索：`search_zhihu(query, count)` 返回问题、回答和文章的最小结构化投影，为热榜候选补充作者、互动量、摘要与原文来源；本轮不接 CLI、MCP 或自动选题。
- 第六十个可运行切片收敛工具状态栏：Rich 在工具执行期间只在底部单行动态显示“正在调用 tool_name”，完成后清除状态并只在正文保留一条简洁结果；完整 ToolResult 仍发送模型并保存 Session。
- 第六十一个可运行切片开始消费 PersonClone 作者路由画像：`PersonCloneClient.get_routing_profile(author)` 只通过正式 GET API 和既有登录 Cookie 读取画像；本轮不注册 LLM Tool、不触发 rebuild、不访问 PersonClone 本地文件或 Qdrant，也不实现作者排序。
- 项目学习资料新增 `creatoros-search-routing-guide.pdf`：用 13 页区分当前已实现的热榜/搜索/画像接口与尚待实现的 HotspotBrief、CreatorOS 路由索引、双通道召回和 LLM 重排，并整理 12 个面试高频问答；文档不把目标设计误写成完成项。
- 第六十二个可运行切片把 routing profile 接口响应解析为严格 Pydantic 模型：`RoutingProfileEnvelope` 包含 `AuthorRoutingProfile`，画像下再拆分 domain/perspective prototype、evidence 与不透明 `VectorRef`；本轮不实现索引、向量召回或 LLM 重排。
- 第六十三个可运行切片把两类画像原型投影为 CreatorOS 自有的 `RoutePrototypeDoc`：生成稳定 doc_id、prototype_type、embedding_text、证据 ID、模型/维度和 corpus_version；本轮不安装 embedding 运行时、不产生向量、不连接 Qdrant。
- 第六十四个可运行切片安装 `sentence-transformers` 到 deepcode，并接入本地离线 `BGEEmbeddingProvider`：只加载已有 BAAI/bge-m3 权重，批量生成 1024 维归一化向量；本轮不建立 Qdrant 索引、不做热点召回。
- 第六十五个可运行切片打磨菜单型 CLI 外观：主页改为“运营工作台”产品文案，菜单项补充业务入口说明，作者目录显示接入数量，箭头菜单统一使用低饱和紫色选中态；本轮不改变 Agent、Tool 或业务数据流。
- 第六十六个可运行切片收紧主页品牌文案：主页标题只保留 `CreatorOS`，移除“运营工作台”和“工作区”等额外中文标题；菜单结构、箭头交互和业务入口说明保持不变。
- 第六十七个可运行切片继续收紧主页：移除菜单页的小号 CreatorOS 标题和业务链路副标题；给 `prompt_toolkit` 菜单窗口隐藏文本光标，避免光标默认停在第一个 `↑` 上造成“被选中”的白色方块；大 Logo、真正的 `❯` 选中标记和菜单交互保持不变。
- 第六十八个可运行切片补齐 Agent 菜单返回和最小会话壳：进入 Agent 前显示命令提示，支持 `/help`、`/menu`、`/reset` 和 `/exit`，其中 `/menu` 与 `/exit` 返回上一级菜单；空输入不发给模型。本轮不引入 transcript 全屏查看、后台任务或新的模型语义。
- 第六十九个可运行切片加入 Agent 的 slash command palette：TTY 下使用 `prompt_toolkit` 在输入 `/` 时提供带描述的命令补全并支持前缀过滤；Agent 页面只提示输入 `/`，新增 `/context` 展示当前请求的估算上下文、模型窗口、输出预留和剩余预算；不把估算冒充 Provider 真实 usage，也不改变模型调用语义。
- 第七十个可运行切片收紧 Agent 页面：移除每次进入对话都会重复出现的 `CreatorOS / Agent` 标题和分隔线，只保留一次简短的任务/命令提示；slash command palette、Agent Loop 和返回菜单语义不变。
- 第七十一个可运行切片收敛终端视觉：全局颜色改为低饱和雾青、灰紫、鼠尾草和纸金；slash command palette 使用近黑底色和柔和选中态；`/context` 改为一行环形 glyph + `已用 / 可用输入上限`，不再展开窗口、输出预留和剩余预算明细。本轮不改变补全命令和预算计算语义。
- 第七十二个可运行切片增加 PersonClone 作者任务状态适配器：`PersonCloneClient.get_author_job(job_id)` 将任务响应校验为 `AuthorJobStatus`，保留终态/就绪判断并忽略服务端未来新增字段；本轮不轮询、不启动后台线程、不改变 `add_author` 的用户可见行为。
- 第七十三个可运行切片把 `add_author` 的远端初始状态登记到 `AgentState.tasks`：`TaskRecord.sync_remote_status()` 统一映射 PersonClone 的 queued/running/ready/failed/cancelled/interrupted；本轮只同步 ToolResult 中已有状态，不轮询、不持久化任务、不改变前台等待策略。
- 第七十四个可运行切片增加 `get_author_job(job_id)` Tool：通过 PersonClone 的 GET 接口取得最新任务状态，复用同一个 `TaskRecord` 更新阶段/进度/终态；任务句柄只进入模型消息和内部状态，终端不展示。本轮仍不自动轮询、不持久化任务、不改变前台等待策略。
- 长期终端渲染原则：状态只允许使用底部单行 `Status` 做重绘；正文、工具 trace 和结果只增不改、单向滚动；不再让增长中的正文依赖光标回退或全屏 Live。
- 设计决定：当前不实现通用 `RepetitionGuard`。先让模型利用工具结果自行修正，保留 `MaxTurnGuard` 作为确定性的资源保险丝；只有出现可复现的无进展循环证据时，才引入最小、可解释的提醒或停止策略。Pi 核心提供停止/工具钩子，重复检测主要存在于第三方扩展，而不是核心 Runtime 的强制行为。
- Guardrail 审计结论：当前 `MaxTurnGuard` 只覆盖模型调用次数；Pydantic、路径边界和 `ToolResult` 已覆盖一部分输入/结果正确性，但仍缺少敏感文件保护、内容/大小上限、Provider 超时/取消、工具调用预算、风险分级/审批、审计记录和不可信工具结果边界。
- 面向未来 CreatorOS 创作者运营 Agent，Guardrail 应按阶段和副作用分层：研究阶段重视来源与不可信内容隔离，创作阶段重视结构/品牌/平台规则，发布阶段重视账号范围、预览、幂等键和人工审批，分析阶段默认只读并要求数据来源与异常校验。
- 命名决定：CreatorOS 不使用含义模糊的 `AgentContext` 类名；运行环境和依赖命名为 `RuntimeContext`，发给模型的请求投影命名为 `ModelContext`。`AgentState` 仍只表示可变运行状态。
- 存储校准：Pi 默认按工作目录把会话保存为 JSONL 文件；OpenAI Agents SDK 提供文件型 SQLite、SQLAlchemy、Redis 等 Session；LangGraph 使用 Checkpointer，可选内存、SQLite、Postgres、Redis 等后端。参考：[Pi Sessions](https://pi.dev/docs/latest/sessions)、[OpenAI Agents Sessions](https://github.com/openai/openai-agents-python/blob/main/docs/sessions/index.md)、[LangGraph Checkpointers](https://docs.langchain.com/oss/python/integrations/checkpointers/index)。CreatorOS 当前选择最小的本地 JSON 快照，不提前引入数据库或完整 Session 抽象。
- 架构校准：OpenAI Agents SDK 提供 `ModelProvider` / `FunctionTool`，AutoGen 提供 `ChatCompletionClient` / `CreateResult`，LangChain 为不同厂商提供统一 Chat Model 接口；Pi 的 `Provider` 负责认证、模型目录和流式请求，`Models` 负责 Provider 集合。参考：[OpenAI Agents](https://openai.github.io/openai-agents-python/models/)、[AutoGen](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/components/model-clients.html)、[LangChain](https://docs.langchain.com/oss/python/concepts/providers-and-models)、[Pi](https://github.com/earendil-works/pi/blob/main/packages/agent/docs/models.md)。CreatorOS 当前只翻译最小的同步 `complete` 边界。

## 长期路线与当前定位

CreatorOS 按“先 Runtime、再接入业务边界、最后产品闭环”的路线推进，不按临时问题随机堆功能：

1. **Runtime 学习基础（当前阶段）**：LLM 调用、消息、Agent Loop、Tool Calling、Tool Registry、Pydantic、Provider、Streaming、Session、最小 State。
2. **Runtime 正确性与可恢复性**：RuntimeContext、ModelContext、Agent Message / LLM Message 分离、Compaction、错误与重试、Max Turn、重复调用、取消和超时。
3. **Runtime 运行能力**：Events、Observability、Hooks、并发工具、Human-in-the-loop、MCP 和 Evaluation。
4. **CreatorOS 业务能力**：Trend Discovery、Creator Routing、PersonaForge Service / Tool、Research、Content Planning、Content Generation、Judge / Review。
5. **产品闭环**：Human Approval、Publishing、Analytics Feedback、Working Memory / Long-term Memory、权限、账号隔离和多用户运行。

当前 Runtime 基础仍处于第 1 阶段；本轮只增加一个窄的第 4 阶段外部服务接入切片，验证业务 Tool 如何调用已有服务。热点发现、Creator Routing、内容生成编排、发布和分析反馈仍然暂缓。现有内置工具之外，新增的 PersonClone 工具是第一组真实业务边界，不代表完整 CreatorOS 产品已经完成。

## 本轮目标

本轮修复 Rich 在 Windows 终端的显示正确性，并恢复启动品牌视觉：

- 启动画面删除 `Agent Runtime · learning build` 副标题，使用五行高分辨率大彩色 CreatorOS 字标；只在 CLI 启动时打印一次，后续对话自然向下滚动。
- 流式 assistant 文本不再用增长中的 Live 重绘，而是遇到完整 Markdown 段落就追加渲染；仍然是增量输出，但已打印内容不回退、不重复。
- 工具调用显示为 `↳ tool_name`，完成显示为 `✓ done · result`；Rich Theme 为 logo、提示、思考、工具、成功和警告提供语义样式。
- `tests/smoke_terminal_ui.py`、`tests/smoke_rich_console.py` 和 `tests/smoke_agent_events.py` 验证大字标、语义 trace、段落流式输出、副标题和大框线状态。
- 本轮不引入 Textual、prompt_toolkit、完整布局系统或改变 Agent Event、Provider、Session 语义。

## 本轮目标（PersonClone 最小业务接口）

- PersonClone 继续作为独立运行的 FastAPI 服务；CreatorOS 不复制它的爬虫、RAG、向量库或生成代码，只持有一个可替换的 `PersonCloneClient`。
- 用 `list_authors` 读取可用数字分身，让 Agent 能先查看并选择作者；返回时过滤内部 `index_dir` 等服务实现细节。
- 用 `add_author` 调用 `/api/author-jobs`，返回异步抓取/建库任务状态；本轮不在 CreatorOS 内重复实现抓取，也不等待或编排任务队列。
- 用 `ask_author` 调用 `/api/chat/stream`，解析 `meta`、`token`、`done`、`error` SSE 事件；只把最终回答放进 `ToolResult.content`，来源和 trace 放入 `details`。
- 认证只通过本地环境变量 `PERSONCLONE_SESSION_COOKIE` 传递，不把会话值写入仓库；PersonClone 当前若返回 401，统一转换为 `personclone_auth`，不自动猜测或保存账号密码。
- `scripts/configure_personclone_auth.py` 是一次性本地配置助手；密码只在终端输入和 HTTP 请求内存中短暂存在，脚本不会打印密码或 Cookie，也不会自动提交 `.env`。
- 验收边界是“能列出/选择作者、能提交添加作者、能向已索引作者提问”；热点发现、自动选题、批量创作和发布属于后续业务切片。

## 本轮目标（PersonClone Routing Profile 只读边界）

- 在既有 `PersonCloneClient` 增加 `GET /api/personas/{author}/routing-profile`，复用同一 HTTP Session 的 Cookie、超时与错误映射。
- 作者标识先做 URL 编码；响应暂按 JSON object 原样返回，下一切片再用实际响应校准 Pydantic 模型与 ready/domain-only/pending 状态。
- 路由画像属于 Creator Routing 的内部数据，不直接注册成 LLM Tool，避免把整份画像无条件塞进模型上下文。
- CreatorOS 不调用管理员 rebuild，不读取 `routing_profile.json`、`narrative_schema.json` 或 PersonClone Qdrant；404 继续使用现有 `personclone_not_found` 错误边界。

## 本轮目标（异步任务状态最小切片）

- `TaskStatus` 表示任务业务阶段：`queued`、`running`、`completed`、`failed`、`cancelled`、`timed_out`。
- `TaskRecord.health()` 单独判断运行健康度：排队任务没有 heartbeat 也不算卡住；运行任务超过 heartbeat 窗口时只标记为 `stalled`（疑似无进展），超过绝对 deadline 才标记 `deadline_exceeded`。
- `AgentState.tasks` 只作为当前进程内的内部容器；用户不看到 task id，当前也不把任务状态写入 messages 或 Session。
- `add_author` 的用户可见 `ToolResult.content` 不再暴露 PersonClone job ID；内部句柄先放在 `ToolResult.details`，等 Harness 接入后再登记到 `AgentState.tasks`。
- 本轮不让 LLM 轮询、不创建线程或队列；下一步再决定前台等待、后台恢复和状态持久化如何接入 PersonClone。

## 本轮目标（ModelContext v0 请求边界）

- `ModelContext.from_messages(messages, tools)` 生成一次模型回合的深拷贝快照，避免 Provider 直接持有可变的 `AgentState.messages`。
- 开头连续的 `system` / `developer` 消息进入稳定前缀；工具 schema 保持独立且稳定；其余 user/assistant/tool 消息保持原顺序作为动态尾部。
- `to_request()` 在 Provider 边界还原 `messages = system_prefix + conversation_tail` 和独立 `tools` 字段，确保当前 Chat Completions 行为不变并为后续缓存观察留下边界。
- ModelContext v0 本身不实现 ToolResult 截断、摘要压缩、缓存 key、缓存命中遥测、RuntimeContext 注入或 Responses API 迁移；上下文预算由后续独立切片补上。

## 本轮目标（Context Budget v0）

- `ContextBudget` 记录 `context_window`、`reserve_output_tokens` 和估算出的输入量，并计算可用输入上限、剩余空间及是否需要关注。
- `estimate_tokens()` 只使用 JSON 字符长度启发式：ASCII 按约 4 字符/token，非 ASCII 字符按约 1 token；结果明确是近似值，不冒充厂商 tokenizer。
- 通用 Provider 缺少能力元数据时的 fallback 是 32,768 context tokens、预留 4,096 output tokens；DeepSeek V4 Provider 使用自己的 1,000,000 context window 和 32,768 output reserve，不再把 fallback 当成真实模型规格。
- Agent Loop 只在接近或超过预算时发出 `context_warning`；即使超出，本轮仍继续调用模型，截断、压缩和停止留到后续切片。

## 本轮目标（Provider Usage v0）

- 统一 `input_tokens`、`output_tokens`、`total_tokens`、`cache_hit_tokens` 和 `cache_miss_tokens`，不把厂商字段名泄露给 Agent Loop。
- DeepSeek 流式响应先读取每个 chunk 的 `usage`，再判断 `choices` 是否为空；因此最终 usage chunk 不会被旧的空 choices 分支丢弃。
- `stream_llm()` 把 `StreamEnd.usage` 放进最终 `ModelResponse.usage`；`run_agent` 通过 `model_usage` 事件提供给观察器，默认 Console 不增加噪声。
- `ContextBudget.with_usage()` 用真实 `input_tokens` 覆盖估算值，仅用于本次请求的准确预算判断；估算仍保留用于请求前预警。
- `DeepSeekProvider` 暴露 `context_window` 和 `reserve_output_tokens`，Agent Loop 优先使用 Provider 元数据，只有 Fake/未知 Provider 才回退到通用默认值。
- 本轮不把 usage 写入 Session/messages，不实现跨轮 usage 聚合、计费报表或 Provider tokenizer/count endpoint。

## Context 调研结论与后续切片（2026-08-24）

- Context 不是另一份会话存储，而是一次模型请求的可丢弃投影；完整 Session 应继续保存原始消息，`ModelContext` 只选择本轮需要发送的稳定前缀、压缩摘要和最近完整回合。
- Pi 使用“追加式 Session + CompactionEntry + Context Builder”：旧历史仍留在 JSONL，会话上下文只投影最新摘要和保留尾部；切点优先落在完整 turn 边界，并避免把 assistant tool call 与 tool result 拆开。参考：[Pi Compaction](https://pi.dev/docs/latest/compaction)。
- Claude Code 会先清理旧工具输出，再进行会话摘要；项目根规则和长期记忆从磁盘重新注入，说明长期规则不应只依赖早期聊天消息。参考：[Claude Code Context Window](https://code.claude.com/docs/en/context-window)。
- OpenAI Responses 提供厂商原生 `/responses/compact`，返回可供后续请求继续使用的 opaque compaction item；CreatorOS 当前使用 DeepSeek Chat Completions，因此本阶段不把 Runtime 绑定到该厂商能力。参考：[OpenAI Compact a response](https://developers.openai.com/api/reference/java/resources/responses/methods/compact)。
- DeepSeek 上下文缓存由服务端自动完成，只对可复用前缀生效；缓存降低重复计算的成本和时延，但不替代上下文裁剪、摘要或长期记忆。当前 system/tools 稳定前缀与 `cache_hit_tokens` 观察方向正确。参考：[DeepSeek 上下文硬盘缓存](https://api-docs.deepseek.com/zh-cn/guides/kv_cache/)。
- CreatorOS 按四个独立切片推进：① 纯本地 `CompactionPlan` 只计算完整 turn 切点；② 用真实 DeepSeek 生成结构化摘要并持久化 checkpoint，但不删除原始 Session；③ 根据 `ContextBudget` 自动触发；④ 再把大型工具结果外置为 artifact/reference，按需重新读取。超限重试仍是后续独立切片。
- 行业校准：Pi、Claude Code、LangChain 都采用“接近窗口上限自动压缩旧历史并保留近期消息”的模式；Claude Code 还会先清理旧工具输出，OpenAI Responses 则提供厂商原生 opaque compaction。共同方向是分离完整持久历史与模型工作上下文，具体阈值、摘要格式和 ToolResult 截断长度并无统一标准。参考：[Pi Compaction](https://pi.dev/docs/latest/compaction)、[Claude Code context](https://code.claude.com/docs/en/how-claude-code-works)、[LangChain SummarizationMiddleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)、[OpenAI Compaction](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.2)。
- 第一切片只学习“哪些消息能一起切”：保留 system/tools、最近完整 user turn，以及完整的 assistant tool call → tool result 组合；不调用模型、不改 Session 格式、不引入向量库、长期 Memory、分支摘要、Provider 原生 opaque compaction 或自动重试。

## 本轮目标（CompactionPlan v0）

- `CompactionPlan.from_context()` 只读取 `ModelContext.messages`；system/developer 稳定前缀和 tools 已由 `ModelContext` 分离，因此不会被放进摘要区。
- 默认尾部预算由 `calculate_keep_recent_tokens(input_limit)` 计算：保留可用输入窗口的八分之一，下限 8k、上限 128k，并且绝不超过 `input_limit`；DeepSeek 当前得到 120,904，而不是固定 20k。
- `CompactionPlan.from_context()` 必须接收本轮 `ContextBudget.input_limit`，也允许调用者显式覆盖 `keep_recent_tokens`；非法输入窗口、非正数保留预算和超出输入窗口的覆盖值会被拒绝。
- 最近一个 turn 即使本身超预算也完整保留，并通过 `retained_turn_exceeds_budget` 暴露该事实，split-turn 留到后续。
- 只允许在 `role="user"` 处形成保留边界，因此一个 turn 内的 assistant tool call、一个或多个 tool result 和后续 assistant 回复会整体进入摘要区或保留区。
- ToolResult 采用两阶段原则：新鲜结果向模型提供“最小但足够继续判断”的关键内容；结果变旧后才由 Compaction 或未来 ArtifactStore 缩减。返回内容型工具不能只回复“成功”，大型原始输出未来保存引用和可重读句柄。
- 本轮纯本地确定性切点测试不调用真实 API，因为没有 API 行为需要验证；真实 DeepSeek 调用留给下一切片的手动摘要。

## 本轮目标（CompactionSummaryRequest v0）

- `CompactionSummaryRequest.from_messages()` 把 `CompactionPlan.messages_to_summarize` 转成一次全新的 `ModelContext`；摘要请求只有专用 system prompt 和一个承载历史资料的 user message，`tools=[]`，不会继续原 Agent Tool Loop。
- 序列化结果显式区分 `[User]`、`[Assistant]`、`[Assistant tool calls]` 和 `[Tool result id=...]`，让摘要模型把旧消息当作资料而不是当前对话；工具名称、参数和 call id 保留。
- 单个 ToolResult 最多以首尾合计 4,000 字符进入摘要请求，超出部分在中间写明省略字符数和 `result_ref`；这只缩小摘要请求，原始 Session 和原 ToolResult 不修改。
- 摘要格式固定保留 Goal、约束、进度、关键决策、精确 ID、文件/产物、下一步和未决问题；支持可选 previous summary 与用户 focus，为后续重复压缩和 `/compact [focus]` 留出稳定接口。
- 本轮属于纯本地数据变换，使用确定性 smoke 而非真实 API；下一切片调用 `provider.complete(request.context)` 时按用户规则直接验证真实 DeepSeek，不使用 Fake/Mock。

## 本轮目标（CompactionSummary Generation v0）

- `generate_compaction_summary(provider, request)` 是摘要专用的一次 Provider 调用，不进入正常 Agent Loop；`CompactionSummaryResult` 保存 Markdown、Provider usage、原消息数量和摘要输入截断数量。
- `validate_summary_markdown()` 要求非空响应完整包含 11 个结构标题；摘要请求 `tools=[]`，若 Provider 仍返回 tool call 则拒绝，不把异常输出当成有效 checkpoint。
- 重复压缩采用“previous cumulative summary + newly old turns -> one new cumulative summary”的覆盖语义；正常模型未来只接收最新累计摘要和 retained turns，不并排累积多份摘要。该投影与持久化留给下一切片的 checkpoint。
- 累计摘要必须保持简洁并淘汰过时信息，未来还需增加明确的 summary token budget；当前 v0 只通过结构 prompt 约束，没有把输出上限扩散进通用 Provider 接口。
- 真实 DeepSeek 首次验证发现模型把普通 `[truncated]` 误判为工具未完成；截断标记已校准为 `summary-input truncated` 并明确原结果仍在 Session，复测后不再把摘要副本截断解释成调用失败。

## 本轮目标（CompactionCheckpoint Storage v0）

- `CompactionCheckpoint.create()` 从完整 Session 快照建立自包含存档，保存最新累计 Markdown、`first_retained_index`、`source_message_count`、`retained_messages`、`tokens_before`、摘要 usage 和 UTC 创建时间。
- `source_digest` 对 checkpoint 创建时的全部源消息做稳定 SHA-256；恢复时允许 Session 在末尾追加新消息，但源前缀被重写、Session 变短、checkpoint JSON 损坏或字段非法时返回 `None`，不把旧摘要套到错误会话。
- checkpoint 文件位于 Session 同目录的 `latest.compaction.json`，写入先落临时文件再 replace；原始 `latest.json` 不删除、不重写，checkpoint 可独立清除。
- 本轮只完成存储与失效规则，不让 Agent Loop 读取 checkpoint，也不实现 `/compact`、自动触发、ArtifactStore 或 ToolResult 查询。

## 本轮目标（Compacted ModelContext Projection v0）

- `CompactionCheckpoint.project_messages()` 只生成本轮活动消息副本：`stable prefix + cumulative summary + retained_messages + raw[source_message_count:]`；已经摘要的原始旧消息不进入 Provider 请求，但继续完整保存在 Session。
- 累计摘要使用带 `<summary>` 边界的 `role="user"` 消息，不提升为 system 指令；稳定 system/developer 和 tools 仍由 `ModelContext` 放在请求前部。
- `build_model_context()` 统一普通与压缩后的 Context 构造，Agent Loop 启动时加载一次有效 checkpoint；`/reset` 同时清空内存引用和 sidecar checkpoint，避免旧摘要进入新会话。
- checkpoint 当前只能由代码/测试创建，因此真实 CLI 行为在出现 checkpoint 前不变；本轮不实现 `/compact`、自动压缩、摘要 token 上限、ArtifactStore 或历史 ToolResult 查询。

## 本轮目标（Internal Compaction Operation v0）

- `compact_session()` 先基于 checkpoint-aware 活动上下文计算 `ContextBudget`，再只对未被旧摘要覆盖的 live turns 运行 `CompactionPlan`；切点从 live 索引映射回完整 Session 的绝对 `first_retained_index`。
- 没有 `messages_to_summarize` 时直接返回 `None`，不调用 Provider、不写空 checkpoint；有旧 turns 时依次生成并校验 Markdown、建立自包含 checkpoint，最后才原子 replace sidecar，摘要或写盘失败不会先破坏旧 checkpoint/Session。
- 首次压缩输入为旧 turns；重复压缩输入为 `previous cumulative summary + newly old turns`，输出覆盖为一份新累计摘要。正常 Context 仍只投影最新摘要、retained tail 和新增消息。
- `keep_recent_tokens` 可显式覆盖仅用于确定性/真实集成测试；实际运行默认继续使用 Provider `input_limit` 的动态八分之一策略。
- 本轮加入低成本真实 DeepSeek 全链路验证，但尚未让 Agent Loop 根据 `ContextBudget` 自动调用 `compact_session()`，也不暴露 `/compact`。

## 本轮目标（Automatic Compaction Trigger v0）

- Agent Loop 在每次主模型请求前构造 checkpoint-aware `ModelContext` 并计算预算；只有 `needs_attention` 为真才调用 `compact_session()`，普通短会话不增加摘要 API 调用。
- 成功后更新 Loop 内存中的 checkpoint，重新构造并重新计量本轮请求；主模型不会先收到超限旧上下文。
- 成功发出 `context_compacted` 观察事件，记录压缩前后估算 tokens；若无完整旧 turn 可压缩，或压缩后仍接近预算，则保留 `context_warning`。
- 单个超大回合的 split-turn、摘要失败降级和 Provider 超限后的 retry 暂缓；自动触发只尝试一次，避免 compaction thrashing。
- 触发编排使用小窗口确定性 Provider smoke，因为用真实 DeepSeek 1M 窗口强行触发需要构造约 87 万 tokens，属于明显高成本例外；摘要 API 与 checkpoint 全链路已经由上一切片的真实 DeepSeek 测试覆盖。

## 本轮目标（ToolResult Model Projection v0）

- 区分两个边界：`summary-input projection` 只在压缩旧历史时服务摘要模型；`model-context projection` 在每次正常主模型请求前处理仍处于活动上下文中的大型 ToolResult。
- `project_tool_results_for_model()` 深拷贝活动消息；普通主模型最多看到单个 ToolResult 的首尾合计 16,000 字符，中间标记省略字符数，并把现有 `tool_call_id` 暴露为稳定 `result_ref`。
- 摘要输入沿用更紧的 4,000 字符预算，但从“只留开头”升级为首尾各半；终端日志尾部的 traceback 和最终状态不再必然丢失。
- 原始 `AgentState.messages`、`sessions/latest.json` 和 checkpoint retained messages 都保存完整内容；ContextBudget 对真正发送的投影后请求计量。
- 当前 marker 只能告诉模型完整结果仍在 Session，尚未提供 `read_tool_result(result_ref, offset, limit)`；ArtifactStore、Session 索引和按需重读留到下一独立切片。
- 16,000/4,000 字符是 CreatorOS v0 的可解释启发式，不冒充行业统一标准；Pi、Claude Code 等共同采用旧工具输出裁剪/清理，但具体额度和保留首尾策略不同。

## 本轮目标（ToolResult Read-back v0）

- `read_tool_result(result_ref, offset=1, limit=8000)` 只扫描当前 `sessions/latest.json` 中 `role="tool"` 且 `tool_call_id == result_ref` 的消息；用户/assistant 消息即使带同名字段也不会被读取。
- `offset` 和 `limit` 都按字符计数，不冒充 token；单次最多返回 16,000 字符，并提供总字符数与 `next_offset`。
- 找不到、非文本、offset 越界和参数范围错误均返回结构化 `ToolResult`；读取不修改原 Session，普通 `read_file` 仍禁止访问整个 `sessions/` 敏感目录。
- 当前完整持久化内容是 `ToolResult.to_model_content()` 产生的未截断文本，保存字段为 `role`、`tool_call_id`、`content`；`details`、`retryable` 等内部 metadata 尚未写入 Session。
- v0 每次按 ID 反向扫描 JSON 消息，复杂度 O(n)；等出现多 Session、大型 Artifact 或性能证据后再增加索引/ArtifactStore，不提前引入数据库。

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
- `read_file` 在解析后的相对路径中拒绝 `.env*` 文件、`.git`/`sessions` 目录以及 `.pem`/`.key` 文件；路径比较大小写不敏感。
- `read_file` 在读取前拒绝超过 `MAX_READ_BYTES == 128 * 1024` 字节的文件；当前限制整个文件，即使调用方传入较小 `limit` 也不会读取超大文件。
- `RuntimeContext` 位于顶层 `creatoros/context.py`，只依赖标准库和配置，避免 `tools -> agent -> loop -> tools` 循环导入。
- `RuntimeContext.project_root` 是工具访问范围；`operating_system` 和 `shell` 当前只作为运行时元数据，不自动拼进 messages。
- CLI 启动画面只属于终端表现层；它不进入 messages、Session 或 ModelContext。
- `Console` 是终端 I/O 适配器和当前最小事件渲染器，不是完整 UI 状态机；Agent Loop 发出语义事件，未来可替换为 Rich/TUI 实现。
- `Console` 默认使用真实 `input()` 和 `sys.stdout`，测试或未来 UI 可注入自己的输入/输出对象。
- `AgentEvent` 位于顶层 `creatoros/events.py`，避免 `terminal -> agent package -> loop -> terminal` 循环导入。
- `AgentEvent` 当前使用少量稳定字符串 kind；事件 data 只在内存中传给 Console/观察者，不写入 messages 或 Session。
- `on_agent_event` 是观察接口，不改变 Agent Loop 的控制权；默认不传时仍由 Console 渲染原有文字。
- 当前 Spinner 是每个模型回合轮换一个 Unicode 标记的静态提示，不是定时器驱动的持续动画；避免线程与流式输出交错，真正动画留到后续 UI 步骤。
- 默认用户输入提示为 `❯ `；TTY 下使用青色，非 TTY 或 `NO_COLOR` 下保持纯文本，便于测试和日志捕获。
- `RichConsole` 是表现层后端；`AgentEvent` 和 `stream_llm` 不依赖 Rich，未来仍可替换为 Textual 或其他 UI。
- Rich 视觉使用 `_RICH_THEME` 的语义样式：logo 使用青、蓝、紫、黄、绿的高可读彩色分行，像素字通过 `▀` / `▄` 保留上下边缘细节；思考/工具为低饱和灰蓝，成功为浅绿，警告为柔和黄；回答正文保持终端默认色。
- Rich 流式文本按 `\n\n` 分隔的完整段落追加 Markdown renderable，不依赖光标回退重绘；模型原文仍按原样保留在 `ModelResponse` 和 messages 中。
- Rich 的输出使用 `markup=False`，避免模型回答或工具结果中的 `[text]` 被误解释为 Rich 标记。
- PersonClone 默认地址为 `http://127.0.0.1:8000`，可用 `PERSONCLONE_BASE_URL` 覆盖；请求超时由 `PERSONCLONE_TIMEOUT_SECONDS` 控制。
- PersonClone 的认证 Cookie 名为 `personaforge_session`，CreatorOS 只从 `PERSONCLONE_SESSION_COOKIE` 读取 Cookie 值；不会读取、打印或提交会话数据库和密钥。
- PersonClone 路由画像正式来源仅为 HTTP API；`vector_ref` 是不透明引用且受 `corpus_version` 约束，CreatorOS 当前不自行解析或连接其底层 collection。
- Routing profile Pydantic 使用 `strict=True`、`extra="forbid"`；`field`、`claim_id`、`excerpt` 按真实响应允许为 null，`can_use_domain` 与 `can_use_perspective` 根据 profile.status 提供只读能力判断。
- `RoutePrototypeDoc` 同时覆盖 domain 和 perspective；embedding_text 只由正式画像字段与代表性证据拼成，不能把两类 prototype 平均成一个作者文本。
- `BGEEmbeddingProvider` 强制 `local_files_only=True`、校验画像声明的模型名和维度，返回 `EmbeddedRoutePrototype`；模型加载和向量生成不进入 Agent Tool 或 messages。
- `add_author` 返回的是 PersonClone 的异步 job，不等于作者已经完成索引；`list_authors` 是当前最小的完成状态/选择入口，任务轮询留到后续切片。
- PersonClone 实例服务已通过真实 HTTP `/health` 探测；未提供会话 Cookie 时，真实 `/api/personas` 返回 401，这是预期认证边界，不把它误判为服务离线。
- 长任务不能仅凭“还没有结果”判断卡住：必须同时观察任务阶段、最近 heartbeat 和绝对 deadline；排队阶段可长时间没有 heartbeat，运行阶段才使用 heartbeat 超时。
- `stalled` 是 Harness 的“疑似无进展”信号，不是证明 worker 崩溃；只有服务明确失败、取消或超过 deadline 时，才把任务视为终态。
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
- `ModelContext` 是每次模型请求的不可变快照，不是第二份会话历史；它把前导 system/developer 消息和工具 schema 作为稳定输入，把 user/assistant/tool 消息作为动态尾部。
- `ModelContext.to_request()` 只在 Provider 边界生成新的 `messages` / `tools` 列表；这样 AgentState 可以继续负责可变历史，Provider 不需要知道 ContextAssembler 的拆分细节。
- `ContextBudget` 是一次请求的预算观察值，不写入 messages、Session 或 AgentState；它不会把估算数字发送给模型。
- `ModelUsage` 是 Provider 到 Runtime 的内部遥测对象；`ModelResponse.to_message()` 明确忽略它，避免 token 统计污染模型上下文。
- `run_agent` 仍负责消息历史、工具执行和循环控制；Provider 仍负责厂商 SDK 胶水代码。
- `AgentState` 是一次 `run_agent` 调用内的内存工作状态；其中 `messages` 仍是原来发给模型和保存到 Session 的消息列表，`status` 当前只使用 `idle` / `running`，`turn` 按模型请求次数递增。
- 本轮 `run_agent` 仍不返回 State；先验证 State 能承载消息和最小运行元数据，再决定是否开放快照、观察器或恢复接口。
- 本轮最小 `RuntimeContext` 与 `ModelContext` 都不代表已经完成 pending tool 状态、并发执行、取消或事件总线；这些仍保持在后续范围。
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
- CreatorOS 现在额外依赖 `httpx`，只用于 PersonClone 的同步 JSON/SSE HTTP 边界；模型 Provider 和 PersonClone 服务仍然是两个独立进程。

暂未确认：

- `creatoros` 包的长期公共导出和版本化接口；本轮只保留 `main.py` 兼容导出。
- 未来 Provider 抽象如何承载 DeepSeek 与其他模型；这留到后续步骤。
- `AgentState`、Session、RuntimeContext 和 ModelContext 的最终边界；本轮只确定 State 是可变运行时容器，Session 负责持久化，ModelContext 只实现一次请求的最小投影。
- 模型请求失败时如何恢复、自动重试、超时和取消；这些属于后续 Guard/错误处理步骤；通用重复检测当前明确暂缓。
- 无参数 Tool 是否也使用 Pydantic，以及是否为所有 Tool 统一 args model；当前空参数 schema 仍足够简单。
- 如何支持第二个模型、模型能力差异、认证和模型目录；先用一个真实 Provider 验证接口，再扩展。
- Provider 请求失败如何转换为统一错误结果；留到后续错误处理步骤。
- Streaming 当前假设一次只处理一个模型 turn；事件中断时不会保存半截 assistant 消息，取消、重试、超时和增量恢复留到后续步骤。
- `SYSTEM_PROMPT` 是否应该成为 `run_agent` 参数、Provider 配置还是 Context 字段；当前先固定在源码中观察实际需求。
- Tool trace 是否应升级为统一 Event、日志级别或可关闭输出；当前只使用两个固定前缀。
- 是否从单个 JSON 快照迁移到 Pi 风格 JSONL、SQLite Checkpoint 或生产数据库；等会话查询、并发和分支需求出现后再决定。
- 会话 ID、多个用户、多会话列表和历史恢复 UI；当前只有 `latest.json`。
- Runtime 层 `llm(...)` 当前接收 `Provider + ModelContext`；未来是否增加模型选项、取消信号、预算和缓存遥测仍未确定。
- 当前只有 DeepSeek Provider 转换真实 usage 并提供模型窗口元数据；未来需要其他 Provider 分别映射自己的 usage、缓存字段和模型窗口能力。
- `ToolCallEnd` 目前由 Runtime 在整轮流结束后派生；未来是否由 Provider 提供每个工具调用的原生结束事件，留到 Provider 能力扩展时决定。
- Pydantic 验证错误的用户展示格式和自动重试策略仍未确定；本轮已增加 `invalid_arguments` 类型和原始校验详情，但不自动重试。
- 二进制文件、并发读取和工具超时；文件大小上限已加入，但未来仍可按字节流式读取大文件片段。
- Pi 风格的字节级截断、图片内容块、AbortSignal 和 UI 渲染；这些暂不翻译到 Python 版本。
- 工具异常是否需要重试、完整日志和用户可见诊断仍未确定；本轮只保存异常类型，不保存 traceback 或终端输出。
- 有副作用 Tool 是否需要每次调用前的人类确认、显式覆盖参数和更细粒度的写入范围；这些留到 Guards / Human-in-the-loop。
- 拆分后的模块是否进一步细分为多个 Python 包；当前单个 `creatoros` 包足够，先不复制 Pi 的 monorepo 和 workspace 层级。

## 验收与验证草案

- `main.py` 能从本地 `.env` 读取 `DEEPSEEK_API_KEY`，从 `Tool` 对象生成 schema，通过 `execute_tool_call` 解析 JSON arguments 并执行 `read_file` 或 `write_file`，再追加正确的 `tool_call_id`。
- `read_file` 对项目内 UTF-8 文件返回指定行段，对项目外路径、目录、缺失文件、非 UTF-8 文件和越界行号返回可供模型理解的错误文本。
- `read_file` 对 `.env`、`.env.*`、`.git`、`sessions`、`.pem` 和 `.key` 路径返回 `sensitive_path`，不读取其内容。
- `read_file` 对超过 128 KiB 的文件返回 `file_too_large`，结果包含稳定错误类型和大小详情，不把文件内容加载到结果中。
- `RuntimeContext(project_root=temp_dir)` 通过 `execute_tool_call` 读取临时目录内文件；默认 Context 能检测非空的操作系统和 Shell 字段。
- `from main import RuntimeContext` 和 `from creatoros.agent import RuntimeContext` 均可用，且 Tools 与 Agent 导入不产生循环依赖。
- 直接运行 CLI 时显示一次 CreatorOS ASCII 启动画面；`NO_COLOR` 或非 TTY 捕获输出不包含 ANSI 颜色控制码。
- `Console(input_fn=fake, output=StringIO())` 能驱动一次 prompt、普通文本输出和启动画面；默认 CLI 仍使用真实终端。
- `stream_llm(..., console=console)` 的文本 delta 和结尾换行写入注入的 Console，而不是直接访问 stdout。
- `run_agent(..., on_agent_event=callback)` 能按模型回合、工具调用、工具结果的顺序收到高层 `AgentEvent`；默认终端文字和消息历史保持不变。
- AgentEvent smoke 的输出包含 `思考中`、`↳ tool_name`、`✓ done`，且原有工具调用闭环仍然成功。
- Console smoke 确认默认输入提示为 `❯ `，不再出现 `你：`。
- Rich Console smoke 确认五行高分辨率彩色字标、无 `learning build` 副标题、无 Panel 边框，Markdown 段落流式回答、语义工具状态和非 TTY 无 ANSI 输出均可用。
- `read_file` 的 schema 包含 `path`、`offset`、`limit` 约束，并标记禁止额外字段。
- `read_file` 拒绝字符串形式的整数、零或负数范围、缺少 `path` 和未知字段；合法参数仍能读取指定行段。
- 坏 JSON、非 object 参数、未知工具和 Tool 内部异常不会让 Agent Loop 直接退出，而会变成工具结果文本。
- `write_file` 对项目内新路径写入 UTF-8 文本，对项目外路径、已有文件、缺失父目录和非字符串参数返回错误文本。
- `write_file` 的 schema 包含 `path` 和 `content` 两个字符串字段，并标记两个字段为必填、禁止额外字段。
- `write_file` 的字符串类型和额外字段错误在进入写入函数前被 Pydantic 拦截。
- 所有内置 Tool 的 `execute_tool_call` 返回值都是 `ToolResult`；成功结果的 `content` 与上一轮字符串结果一致。
- 文件缺失、路径越界、参数错误和未知工具结果分别带有稳定的 `error_type`；异常详情至少包含验证错误或异常类名。
- `ToolResult` 的 `details` 不会被自动拼进模型消息，避免把内部诊断和未来的原始终端输出无限扩大到上下文。
- PersonClone smoke 能验证 `list_authors`、`add_author`、`ask_author` 的注册 schema、请求路径、Cookie、异步 job 响应和 SSE `done.answer` 提取；不会伪造真实生成成功。
- PersonClone smoke 能验证路由画像 GET 路径、ready/corpus_version 字段读取，并确认它复用同一 `personaforge_session` Cookie。
- `smoke_routing_models.py` 验证 ready/domain_ready 能力、可空 evidence 字段和额外字段拒绝；`smoke_personclone.py` 验证 HTTP Client 返回 `RoutingProfileEnvelope` 而不是未解析的 dict。
- `personclone_auth_helper_smoke=passed`：本地登录助手能安全更新 `.env` 中的 Cookie 键并保留其他配置，不测试或保存真实凭证。
- `smoke_personclone_tools.py` 使用 Fake Client 验证 Agent ToolCall、Pydantic 参数解析、三个 PersonClone Tool 和 ToolResult 的完整接线，不需要登录或真实 API。
- `smoke_task_state.py` 验证排队任务不因没有 heartbeat 被误判、运行任务 heartbeat 超时进入 `stalled`、deadline 超时进入 `deadline_exceeded`，以及终态不再被误判为活动任务。
- 真实 PersonClone 服务 `/health` 返回 200；未带认证 Cookie 的 `/api/personas` 返回 401，并应由 Client 转换为 `personclone_auth`。
- 成功结果的 `to_model_content()` 与 `content` 完全一致；失败结果包含 `[tool_error type=...]`，但不包含 `details` 字段内容。
- `MaxTurnGuard(2)` 在使用 0、1 次模型调用时继续，在第 2 次调用前停止；`MaxTurnGuard(0)` 拒绝创建。
- 未传 `max_turns` 时，`run_agent`、根入口 `main.run_agent` 和 `MaxTurnGuard` 都使用同一个 `DEFAULT_MAX_TURNS == 30`。
- `run_agent(..., max_turns=1)` 的 Fake Loop 只发起一次模型请求，执行其工具结果后停止，不影响下一次用户任务重新计数。
- 没有 args model 的现有 Tool 仍能解析原来的 JSON object 参数。
- `DeepSeekProvider.complete` 使用原来的 DeepSeek endpoint、模型和 `thinking` 配置，并把完整响应返回给 Agent Loop。
- `ModelProvider` 的结果包含 CreatorOS 内部的 `ModelResponse` 和 `ToolCall`，Agent Loop 源码不再读取 `response.choices[0].message`。
- DeepSeek 请求中的 assistant tool calls 仍能转换为 OpenAI-compatible 的 `function` 嵌套格式。
- `run_agent(FakeProvider)` 能完成普通回答和一次工具调用闭环；导入 `main` 不会触发输入提示或真实 API 初始化。
- `llm(FakeProvider, context)` 返回内部 `ModelResponse`，且 Agent Loop 源码不再直接调用 `provider.complete(...)`。
- `ModelContext` smoke 能确认 system/developer 前缀、工具 schema 和动态消息尾部被正确拆分；输入列表在构造后变化不会污染请求快照。
- `run_agent(FakeProvider)` 传入的请求能还原为 system 消息在前、历史消息顺序不变，tools 列表与 Registry schema 一致。
- `context_budget_smoke.py` 能验证输入估算、输出预留、剩余预算、超限判断和 `context_warning` 的终端提示。
- 短请求不会增加额外提示或摘要调用；接近或超过预算时先尝试自动压缩，无法压缩或压缩后仍接近上限时才发出警告。
- `model_usage_smoke.py` 能验证 DeepSeek 流式最后一个空 `choices` chunk 的 usage 被转换并传到 `ModelResponse`，缓存命中/未命中字段不丢失。
- `run_agent(FakeProvider)` 在收到 usage 后发出 `model_usage` 观察事件，但保存的 assistant message 不包含 usage 字段。
- `DeepSeekProvider` 的 Agent Loop 预算使用 1,000,000 context window 与 32,768 output reserve；缺少 Provider 元数据的 FakeProvider 才使用通用 fallback。
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

- 日期：2026-08-25
- 状态：最小 AgentState、ToolResult 和模型内容投影已通过既有 smoke；MaxTurnGuard 默认值调整已完成验证并在 `8483c13` 提交、推送；`502b9d7` 已记录“不实现通用 RepetitionGuard”；`read_file` 敏感路径/大小 Guardrail 已在 `f187fa4` 提交、推送；RuntimeContext 已在 `5182cd2` 提交、推送；终端启动画面已在 `b8bf0e6` 提交、推送；Console 适配层已在 `bf8b136` 提交、推送；AgentEvent 已在 `d584da9` 提交、推送；状态渲染已在 `86e9e86` 提交、推送；Rich Console 已在 `bd86ccf` 提交、推送；大彩色字/Windows 段落流式修复已在 `28ce5fc` 提交、推送；菜单导航已在 `bc70db4`、`f862ba5` 提交、推送；运营工作台菜单 polish 已在 `3789572` 提交、推送；主页文案收紧已在 `e652d79` 提交、推送；菜单光标与主页留白已在 `5f4e9f2` 提交、推送；Agent 页面与返回命令已在 `9a344dc` 提交、推送；slash command palette 已在 `6f22f94` 提交、推送；本轮 Agent 页面标题收紧 smoke 已通过，待提交。
- `conda run --no-capture-output -n deepcode python -m compileall -q main.py creatoros` 通过。
- `tool_result_smoke=passed`：成功读取、文件不存在、Pydantic 参数错误和未知工具均返回结构化 `ToolResult`。
- `compat_smoke=passed`：根入口 `main.read_file`、`main.get_current_date` 等兼容函数仍返回字符串，`main.execute_tool_call` 暴露 `ToolResult`。
- `model_content_smoke=passed`：成功结果保持原文，文件错误带有 `tool_error` 类型前缀，内部 `details` 未进入模型内容。
- `max_turn_guard_smoke=passed`：Guard 的阈值判断和 `max_turns=1` 的 Fake Loop 均通过，只发起一次模型请求。
- `default_max_turns_smoke=passed`：`DEFAULT_MAX_TURNS`、`MaxTurnGuard()`、`run_agent` 和根入口默认值统一为 30。
- `read_file_guardrail_smoke=passed`：普通 `SPEC.md` 可读取，`.env` 返回 `sensitive_path`，超过 128 KiB 的临时文件返回 `file_too_large` 并清理。
- `runtime_context_smoke=passed`：临时 RuntimeContext 能让 `execute_tool_call` 读取指定项目目录，默认 Context 的 project_root、操作系统和 Shell 均有效。
- `import_boundary_smoke=passed`：`creatoros.agent` 与 `creatoros.tools` 可同时导入，没有循环依赖。
- `terminal_ui_smoke=passed`：启动画面生成五行非空高分辨率彩色字标，不包含副标题。
- `console_smoke=passed`：注入假的输入函数和 `StringIO` 后，Console 能完成 prompt、普通输出和启动画面；编译、既有 RuntimeContext、终端 UI 与 read_file Guardrail smoke 均通过。
- `agent_events_smoke=passed`：FakeProvider 完成一次工具调用闭环后，观察者收到 `turn_start`、`tool_call`、`tool_result`、`turn_start`，Console 输出仍包含工具 trace 和最终文本。
- 状态渲染验证：`agent_events_smoke=passed` 同时确认思考、`↳ tool_name`、`✓ done` 和最终回答均写入注入输出。
- `console_smoke=passed`：默认 prompt 为 `❯ `，捕获输出无 ANSI 颜色；AgentEvent、RuntimeContext、终端 UI 和 read_file Guardrail smoke 均通过。
- `rich_console_smoke=passed`：Rich Panel 启动画面、prompt、Markdown 流式输出、工具状态和非 TTY 纯文本捕获均通过。
- `rich_console_smoke=passed`：Rich 无边框启动画面、prompt、Markdown Live、工具状态和非 TTY 纯文本捕获均通过；旧 subtitle 与边框断言已更新。
- `rich_console_smoke=passed`：额外确认分片 Markdown 段落中的“搞定”与后续文本各只出现一次，避免旧 Live 重绘残留。
- Rich 视觉快照：使用 Rich `save_svg` 和 Chrome headless 截图检查五行高分辨率彩色字标、默认正文色、低饱和工具 trace 与浅绿色完成状态；本轮预览为 `C:\Users\13779\AppData\Local\Temp\creatoros_ui_snapshot_v3.png`，生成物不加入仓库。
- `git diff --check` 和 staged diff 检查通过；`28ce5fc` 已推送到 `origin/main`。
- `personclone_smoke=passed`：MockTransport 验证作者列表、添加作者 job、SSE 回答解析、工具 schema 和 `personaforge_session` Cookie。
- `personclone_auth_helper_smoke=passed`：登录助手的 `.env` 更新逻辑通过；未使用真实账号密码。
- `personclone_tools_smoke=passed`：Fake Client 验证作者列表过滤内部字段、添加作者 job、回答内容/details 投影和 Client 释放。
- `task_state_smoke=passed`：TaskRecord 状态迁移、heartbeat 健康度、deadline 判断和 AgentState 内部任务容器通过。
- `personclone_tools_smoke=passed`：`add_author` 只向模型/终端提供自然语言状态，内部 `task_id` 保留在 `ToolResult.details`。
- 真实 HTTP 探测：`http://127.0.0.1:8000/health` 返回 200，`/api/personas` 返回 401；CreatorOS `list_authors` 已将该响应转换为 `personclone_auth`，当前服务要求登录会话，未进行未授权的作者抓取或生成调用。
- 真实认证联调：配置本地 `PERSONCLONE_SESSION_COOKIE` 后，CreatorOS `PersonCloneClient.list_personas()` 真实返回 7 个作者；`ask_author("22-85-32-51", ...)` 真实收到 `personclone_generation_error`（缺少 `narrative_schema.json`），`ask_author("wu-ren-jun-28", ...)` 真实通过 SSE 返回回答和 `trace_id`。
- 联调事实：作者有已抓取内容（`content_count > 0`）不代表默认 `mrprompt` 可以生成；当前应同时观察 `persona_pack_available` 和 `narrative_schema_available`。`list_authors` 已通过 `recommended_writer_prompt` 暴露该选择依据；下一步回到 Context 压缩，暂不增加更多 PersonClone 端点或任务轮询。
- PersonClone 外部策略真实验收：`an-ling-91` 使用默认 `strong_identity + parent_top_k=20` 成功通过 SSE 返回回答；`wu-ren-jun-28` 显式使用 `mrprompt + parent_top_k=20` 成功返回回答和 `trace_id`；CreatorOS `list_authors` 真实返回每个作者的推荐模式。
- `model_context_smoke=passed`：system 前缀、工具 schema、动态消息尾部和深拷贝快照均通过验证。
- `smoke_agent_events.py`、`smoke_console.py`、`smoke_rich_console.py`、`smoke_task_state.py` 在 `deepcode` 环境通过；Agent Loop 实际传入的 Provider Context 能还原 system 在前、tools 与 Registry 一致。
- `context_budget_smoke=passed`：粗略输入估算、输出空间预留、超限判断和 Console 警告事件通过；既有 Agent Loop/UI smoke 仍通过。
- `model_usage_smoke=passed`：DeepSeek Fake SSE 的最终 usage chunk、`StreamEnd.usage`、`ModelResponse.usage` 和缓存字段映射通过；AgentEvent smoke 同时验证 `model_usage` 事件。
- DeepSeek 官方模型规格已核对：`deepseek-v4-flash` / `deepseek-v4-pro` 当前上下文长度为 1M；CreatorOS 已把该能力作为 Provider 元数据，而不是写死在通用 ContextBudget 中。参考：[DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)。
- `compaction_plan_smoke=passed`：DeepSeek 动态尾部预算为 120,904，通用小窗口下限和极小窗口钳制、完整 user turn 切点、assistant tool call/tool result 成对归属、显式覆盖、最近单回合超预算提示和非法预算拒绝均通过；既有 ModelContext、ContextBudget 与编译验证继续通过。
- 当前真实本地 Session 快照含 18 条消息，连同稳定前缀和工具 schema 粗估为 3,474 input tokens；DeepSeek 可用输入上限 967,232、动态保留尾部 120,904，因此当前 `can_compact=false`。这是本地上下文规划验收，没有调用模型；真实 DeepSeek 摘要留给手动 `/compact` 切片。
- `compaction_summary_smoke=passed`：摘要专用 system/user 边界、空工具列表、角色序列化、工具名称/参数/call id、4,000 字符 ToolResult 截断、previous summary、用户 focus 和空输入拒绝均通过；既有 CompactionPlan、ModelContext 与编译验证继续通过。
- `live_compaction_summary=passed`：真实 `deepseek-v4-flash` 使用 817 input tokens / 313 output tokens，返回完整结构化 Markdown，保留 `D:\CreatorOS\SPEC.md`、`trace-creatoros-42`、status 200 和已完成状态；没有 tool call。测试只读取本地 `.env` 的密钥且未打印或提交。
- `compaction_checkpoint_smoke=passed`：checkpoint 原子保存/加载、ModelUsage 往返、追加消息后继续有效、源消息改写失效、损坏 JSON 回退和显式清除均通过；既有 ModelContext、AgentEvent 与编译验证继续通过。
- `compacted_model_context_smoke=passed`：纯投影和实际 Agent Loop 均验证 system 在前、累计摘要注入、retained tail 与 checkpoint 后新消息保留、旧消息不发送、tools 不变、原始 Session 列表不修改；既有 Checkpoint、AgentEvent、ModelContext 与编译验证继续通过。Fake Provider 仅用于无网络的 Loop 投影隔离，不替代任何需要验证的真实 API 行为。
- `compact_session_smoke=passed`：首次绝对切点、retained tail、无旧 turn 不调用 Provider、重复压缩传入旧累计摘要、旧原文不重复进入摘要和 checkpoint replace 均通过；既有 Checkpoint、Summary、投影与编译验证继续通过。
- `live_compact_session=passed`：真实 `deepseek-v4-flash` 使用 774 input tokens / 283 output tokens，完成 Plan → ToolResult 摘要副本截断 → 结构化 Markdown → 原子 checkpoint 全链路；`first_retained_index=5`、recent turn 完整保留，临时 Session 未触碰真实 `sessions/latest.json`。
- `auto_compaction_smoke=passed`：小窗口 Provider 验证 Agent Loop 只触发一次摘要、更新 checkpoint、重建主模型请求、删除旧原文投影并保留最近与当前 user turn；压缩后估算 tokens 下降。该测试只隔离自动触发编排，不替代上一轮真实 DeepSeek 摘要链路。
- `tool_result_projection_smoke=passed`：验证普通主模型投影保留首尾、省略量和 `result_ref`，短结果不变，`build_model_context()` 确实使用投影，同时原消息不被修改；摘要 smoke 验证 4,000 字符副本也保留首尾。
- `live_tool_result_projection=passed`：真实 `deepseek-v4-flash` 通过 OpenAI-compatible Tool 消息接收投影后的 2,111 input tokens，并只用 12 output tokens 同时识别 `HEAD_MARKER` 与 `TAIL_MARKER`；本地完整 20,024 字符 ToolResult 保持不变。
- `read_tool_result_smoke=passed`：精确 result_ref、角色隔离、字符分页、next_offset、16,000 上限、找不到/越界错误、Registry schema 和 Session 原文不变均通过。
- `live_read_tool_result=passed`：真实 `deepseek-v4-flash` 先看到省略 marker，再调用 `read_tool_result(result_ref="call-source", offset=8950, limit=200)` 取回中间验证码并完成回答；两次请求合计 11,302 input / 88 output tokens，临时 Session 未触碰真实 `sessions/latest.json`。
- 当前本地 `D:\CreatorOS\sessions\latest.json` 含 18 条消息和 3 条未截断 `role="tool"` 文本结果；该文件被 `.gitignore` 排除，不提交 GitHub。
- `zhihu_search_smoke=passed`：官方 Query/Count 请求、字段投影、Registry schema 和空查询拒绝通过；既有热榜、ModelContext、AgentEvent 与编译验证继续通过。真实无凭证探测返回官方 `Code=20001`，本机尚未配置 `ZHIHU_ACCESS_SECRET`，因此没有伪造真实成功结果。
- `rich_console_smoke=passed` 和 `agent_events_smoke=passed`：工具开始时底部 Status 存在并记住工具名，结束后清理；终端只保留 `✓ tool_name`，`tool_result` 事件仍保留完整 `content`、错误标记和工具名。
- 真实 DeepSeek CLI 验收：模型真实请求 `get_current_time`，执行期使用瞬时底部状态，完成后正文只保留 `✓ get_current_time` 和一次最终回答；完整结果仍写入本地忽略的 Session。
- `personclone_smoke=passed`：新增路由画像 GET 契约验证，确认 `/api/personas/alice/routing-profile`、ready 状态、corpus_version 与既有 Cookie 复用。
- 2026-08-26 真实 PersonClone 复验未完成：`http://127.0.0.1:8000` 当前返回 WinError 10061（无服务监听），请求尚未进入认证或画像接口；不得据此宣称真实画像链路已经通过。
- 2026-08-26 PersonClone 服务启动后真实复验通过：CreatorOS 使用本地 Cookie 列出 7 位作者，并逐个通过正式 GET 获取画像；7 份 profile 均为 `ready`，合计 83 个 domain prototypes、37 个 perspective prototypes，统一声明 `BAAI/bge-m3`、1024 维且各自带 corpus_version。
- 真实响应形状确认：envelope 为 `status + profile`；domain/perspective evidence 均额外包含 `claim_id`、`excerpt`、`field`、`source_method`，vector_ref 字段与接口约定一致。下一步 Pydantic 模型以真实 wire shape 为准，不从 PersonClone 文件或 Qdrant 补数据。
- `pdf_validation=passed`：搜索与作者匹配学习手册为 13 页 A4，已逐页渲染检查封面、流程图、表格、公式、面试问答与页脚；Pypdf/pdfplumber 复验页数、元数据、文本和页面边界，无异常替换字符或内容截断。
- `routing_models_smoke=passed`、`personclone_smoke=passed`：严格模型、可空 evidence、状态能力属性、HTTP 边界解析和全包编译均通过。
- `real_routing_models=passed`：本地登录态下 7 位作者的真实 routing profile 全部解析为 Pydantic；7 份均为 `ready`，83 个 domain 与 37 个 perspective prototypes 的计数和之前原始 JSON 验收一致。
- `real_routing_projection=passed`：本地登录态下 7 位作者投影出 120 个 `RoutePrototypeDoc`，domain/perspective 两类均存在且 corpus_version 与画像一致；当前 deepcode 环境没有 sentence-transformers、torch、numpy 或 qdrant-client，因此本轮没有伪造 embedding 或索引成功。
- `live_routing_embedding=passed`：本地 Hugging Face cache 中已有 BAAI/bge-m3；deepcode 离线加载成功，真实 7 位作者的 120 个 RoutePrototypeDoc 全部生成 1024 维归一化向量，未重新下载模型、未连接 Qdrant。

## 本轮目标（domain-only 热点路由）

- 不引入 perspective、回答爬取、回答聚类或 Qdrant；先用知乎热榜的 `Title + Summary` 作为领域查询文本。
- `BGEEmbeddingProvider` 增加批量 `embed_texts` 和单条 `embed_text`，与已有画像原型使用同一个本地 BGE-M3 模型。
- `build_domain_query` 只做标题/介绍的清洗与有上限拼接，不调用 LLM，避免模型自由改写热点语义。
- `rank_domain_matches` 只比较 `prototype_type == "domain"`，按每个作者所有领域原型的最大 cosine similarity 排名；perspective 原型暂时忽略。
- 当前路由结果仅作为候选召回，不代表最终选题；搜索 API 只在候选热点被选中后按需调用。

## 本轮验证（domain-only 热点路由）

- `domain_routing_smoke=passed`：标题/摘要查询拼接、空摘要、作者级最大相似度、perspective 过滤、top-k 和维度错误均通过。
- `live_domain_routing=passed`：真实知乎热榜 5 条、真实 PersonClone 7 位作者的 83 个 domain prototypes，经本地缓存 BGE-M3 生成查询向量并完成 Top-3 作者排序；未调用回答爬虫、LLM 或 Qdrant。
- `live_routing_embedding=passed`：重构后的批量编码仍能处理 120 个 domain/perspective RoutePrototypeDoc，输出 1024 维归一化向量。
- 观察：domain-only 会把“网络热点事件杂谈”等宽泛原型排在前面，因此当前结果是召回候选，不作为最终作者决策；后续质量问题再引入阈值、领域层级或 perspective。

## 本轮目标（作者侧内容队列）

- 将每条热点对每位作者的 domain Max Similarity 结果反转为作者侧候选队列。
- 用 `ContentOpportunity` 表示一张热点候选卡片，用 `DailyPlan` 固定 `hot`、`evergreen`、`experiment` 三条队列。
- 本轮只填充 `hot`，每位作者独立 Top-N；同一热点可以同时进入多个作者队列。
- CLI 与网页暂时不实现，只先稳定共享的数据结构和排序结果。

## 本轮验证（作者侧内容队列）

- `content_planning_smoke=passed`：作者侧排序、Top-N、三队列空位、未知作者和输入校验通过。
- `live_content_planning=passed`：真实知乎热榜、PersonClone 作者画像和本地缓存 BGE-M3 生成每位作者的 Top-3 热点队列；未调用生成或发布接口。

## 本轮文档（README）

- 根目录新增 `README.md`，公开说明项目定位、当前已实现边界、架构分层、环境变量、验证命令和后续路线。
- README 明确区分已实现的 Runtime/路由/作者队列与尚待接入的生成、评审、发布、反馈和 Web 控制台，不把路线图写成完成项。

## 本轮目标（CLI 菜单导航）

- 启动后先进入主菜单，不直接刷出所有作者和队列。
- 实现 `主菜单 → 作者矩阵 → 作者详情` 三层导航，并保留 `Agent 对话` 入口。
- 作者目录只通过既有 `list_authors` Tool 边界读取安全摘要字段；作者队列和画像详情本轮只显示入口，不提前接入业务数据。
- 保持无大边框、正文单向追加和底部状态栏原则；本轮不引入完整 TUI 或新依赖。

## 本轮验证（CLI 菜单导航）

- `cli_menu_smoke=passed`：主菜单、作者目录、作者详情、返回/退出和 Agent 回调入口通过；无边框断言通过。

## 本轮目标（上下键菜单）

- 用 `prompt_toolkit` 为菜单增加 ↑/↓、Enter、Esc 和 q 操作；Agent 对话仍沿用 Rich Console。
- 菜单选择期间允许局部重绘，退出菜单后继续保留正文单向追加和底部状态栏原则。
- 非 TTY 或测试环境自动回退为数字输入，不改变无交互验证方式。

## 本轮验证（上下键菜单）

- `cli_menu_smoke=passed`：数字回退路径、作者目录、详情页、返回/退出和 Agent 回调通过。
- 真实入口启动后立即结束通过；真实 PersonClone 作者目录仍返回 7 位作者。
- `prompt_toolkit` 已加入 `requirements.txt`，本轮不引入 Textual。

## 本轮审计（PersonClone 异步作者入库）

- PersonClone 当前作者任务链路为 `queued → crawling → building → indexing → clustering → activating → ready`；画像在 staging 目录构建并校验通过后才激活，失败不会替换旧作者数据。
- PersonClone 对外提供 `POST /api/author-jobs` 提交任务、`GET /api/author-jobs/{job_id}` 查询状态、`GET /api/personas/{author}/routing-profile` 读取正式画像；作者任务提交和查询需要管理员会话，画像读取需要协作者会话。
- PersonClone 的工作树当前包含其他未提交的认证与 Web 改动，本轮只读审计，没有替它提交或推送；CreatorOS 不读取其本地语料、索引或 Qdrant。
- CreatorOS 先提交 `add_author`，再通过正式任务查询接口刷新状态；不读取 PersonClone 本地语料、索引或 Qdrant。
- 运行中的 PersonClone 服务真实返回 `/health=200`、作者任务列表 `200`、7 位作者和 ready 路由画像；历史任务的新增画像字段为 `null` 属于迁移前数据，不代表画像生成失败。
- 发现的生产边界：PersonClone 当前只在阶段切换时更新 `updated_at`，长时间运行的 crawl/build/index/embedding 阶段没有独立 heartbeat 或进度百分比；CreatorOS 在实现轮询前不能仅凭长时间无变化判定任务卡死。

## 本轮验证（PersonClone 异步作者入库）

- PersonClone `pytest -q`：`246 passed`；作者任务和路由画像专项测试 `18 passed`。
- CreatorOS 真实只读验证：`live_domain_routing=passed`（5 条热榜、7 位作者、83 个领域原型），`live_content_planning=passed`（7 位作者、5 条热点）；未触发新的爬取、生成或发布副作用。
- `AuthorJobStatus`、`PersonCloneClient.get_author_job(job_id)` 和 `AgentState.tasks` 已完成；`get_author_job` Tool 查询后的状态会回写同一个 `TaskRecord`，并发出 `task_updated` 观察事件。
- `agent_task_tracking_smoke=passed`：Agent Loop 执行 `add_author` 后能把远端 `running/clustering/label` 登记为本地 `TaskRecord`，并验证后续状态刷新沿用同一任务句柄。
- `personclone_tools_smoke=passed`：`get_author_job` 能读取 typed `AuthorJobStatus`、返回阶段信息；任务句柄只给模型/内部状态使用，不进入终端工具状态行。
- 真实 `live_personclone_job=passed`：通过本地 `.env` Cookie 只读查询已有任务，返回 `status=ready`、`stage=ready`；未触发任何副作用。
- 暂缓：自动轮询、heartbeat/卡住判断、任务持久化和“热点 → 选作者 → ask_author”的单一编排入口；前者需先补远端 heartbeat 语义，后者作为下一条业务切片实现。
