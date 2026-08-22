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
- 存储校准：Pi 默认按工作目录把会话保存为 JSONL 文件；OpenAI Agents SDK 提供文件型 SQLite、SQLAlchemy、Redis 等 Session；LangGraph 使用 Checkpointer，可选内存、SQLite、Postgres、Redis 等后端。参考：[Pi Sessions](https://pi.dev/docs/latest/sessions)、[OpenAI Agents Sessions](https://github.com/openai/openai-agents-python/blob/main/docs/sessions/index.md)、[LangGraph Checkpointers](https://docs.langchain.com/oss/python/integrations/checkpointers/index)。CreatorOS 当前选择最小的本地 JSON 快照，不提前引入数据库或完整 Session 抽象。
- 架构校准：OpenAI Agents SDK 提供 `ModelProvider` / `FunctionTool`，AutoGen 提供 `ChatCompletionClient` / `CreateResult`，LangChain 为不同厂商提供统一 Chat Model 接口；Pi 的 `Provider` 负责认证、模型目录和流式请求，`Models` 负责 Provider 集合。参考：[OpenAI Agents](https://openai.github.io/openai-agents-python/models/)、[AutoGen](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/components/model-clients.html)、[LangChain](https://docs.langchain.com/oss/python/concepts/providers-and-models)、[Pi](https://github.com/earendil-works/pi/blob/main/packages/agent/docs/models.md)。CreatorOS 当前只翻译最小的同步 `complete` 边界。

## 本轮目标

本轮只做一次保持行为的目录结构重构：

- `creatoros/ai` 承载内部模型类型、Provider 协议和 DeepSeek 适配器，对应 Pi 的 `packages/ai`。
- `creatoros/agent` 承载 `llm`、Streaming 消费和 Agent Loop，对应 Pi 的 `packages/agent`。
- `creatoros/tools` 承载 Pydantic 参数、工具定义、Registry 和执行逻辑，对应 Pi coding-agent 的工具域。
- `creatoros/session` 只承载当前 JSON 快照函数；不提前引入完整 Session/State 抽象。
- `creatoros/cli.py` 负责 `.env` 后的 Provider 构造和 CLI 启动；根 `main.py` 只做入口和兼容导出。
- 除 import、模块连接和兼容入口外，不改变函数逻辑、消息格式、工具行为、Streaming 行为或会话路径。

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
- `execute_tool_call` 捕获单次工具调用的普通 `Exception` 并返回错误文本；当前不区分模型输入错误和工具内部程序错误。
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
- 本轮模块拆分不代表已经完成 State、Context、并发执行或事件总线；这些仍保持在后续范围。
- `SYSTEM_PROMPT` 当前是源码中的固定常量，作为第一条 `role="system"` 消息发送；后续再决定是否由配置或 Runtime Context 提供。
- 当前 Tool trace 直接写到终端 stdout，不改变发给模型的 `tool` 消息；结果可能较长，截断和结构化展示留到后续 Observability/UI 步骤。
- 当前会话存储是单个 JSON 快照；每次保存先写临时文件再替换目标文件，避免直接覆盖时留下半个 JSON 文件。
- `sessions/latest.json` 只用于本地恢复，可能包含用户输入、工具参数和文件内容，因此必须被 `.gitignore` 忽略。
- 项目早期所有模块共享根级 `SPEC.md`；出现稳定模块边界后，再在最近模块建立独立 `SPEC.md`。

## 对外影响

- 本轮让 Agent Loop 依赖 `ModelProvider` 协议和内部 `ModelResponse`；DeepSeek 适配器承担厂商响应和 tool-call 格式转换，工具执行行为保持不变。
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
- 多轮消息是否属于 Agent State、Session 或 Context；先不提前命名，等循环和持久化需求出现后再区分。
- 模型请求失败时如何恢复、限制轮数和检测重复调用；这些属于后续 Guard/错误处理步骤。
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
- Pydantic 验证错误的用户展示格式、错误分类和重试策略；本轮只把验证错误作为工具结果文本返回。
- 文件内容过大、二进制文件、并发读取和工具超时；这些属于后续 Guard/资源限制步骤。
- Pi 风格的字节级截断、图片内容块、AbortSignal 和 UI 渲染；这些暂不翻译到 Python 版本。
- 工具异常是否需要重试、分类、日志和用户可见诊断；本轮只返回一段错误文本。
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
- 状态：目录拆分完成，逻辑保持不变，兼容入口与 Fake Streaming/会话/工具 smoke 已通过。
- 已验证：`conda run --no-capture-output -n deepcode python -m compileall -q main.py creatoros`；`main` 与 `creatoros.cli` 导入成功且 `.env` 已加载；FakeProvider 完成文本流、工具调用、工具结果、第二次模型回复和会话快照闭环；临时项目目录上的 Pydantic `read_file` / `write_file` Tool smoke 通过；`main.Tool`、Pydantic Tool 参数模型和旧的 `from main import ...` 兼容导出仍可用；`git diff --check` 通过。
- 已验证：`cmd /c "echo /exit| conda run --no-capture-output -n deepcode python main.py"` 能进入根入口并正常退出；本轮不重复调用真实 API，避免把结构重构与网络行为混在同一验证中。
