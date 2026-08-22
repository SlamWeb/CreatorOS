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
- 架构校准：OpenAI Agents SDK 提供 `ModelProvider` / `FunctionTool`，AutoGen 提供 `ChatCompletionClient` / `CreateResult`，LangChain 为不同厂商提供统一 Chat Model 接口；Pi 的 `Provider` 负责认证、模型目录和流式请求，`Models` 负责 Provider 集合。参考：[OpenAI Agents](https://openai.github.io/openai-agents-python/models/)、[AutoGen](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/components/model-clients.html)、[LangChain](https://docs.langchain.com/oss/python/concepts/providers-and-models)、[Pi](https://github.com/earendil-works/pi/blob/main/packages/agent/docs/models.md)。CreatorOS 当前只翻译最小的同步 `complete` 边界。

## 本轮目标

本轮只迈一个 `small step`：

- 在 `run_agent` 的消息历史开头加入固定的 `SYSTEM_PROMPT`。
- 验证 system → user → assistant/tool 的消息顺序和工具闭环。
- 验证 DeepSeek Provider 的消息转换不会丢掉 `role="system"`。
- 不在本轮加入可配置 Prompt、Responses API 的 `instructions` 或权限系统。

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
- `run_agent` 仍负责消息历史、工具执行和循环控制；Provider 仍负责厂商 SDK 胶水代码。
- `SYSTEM_PROMPT` 当前是源码中的固定常量，作为第一条 `role="system"` 消息发送；后续再决定是否由配置或 Runtime Context 提供。
- 项目早期所有模块共享根级 `SPEC.md`；出现稳定模块边界后，再在最近模块建立独立 `SPEC.md`。

## 对外影响

- 本轮让 Agent Loop 依赖 `ModelProvider` 协议和内部 `ModelResponse`；DeepSeek 适配器承担厂商响应和 tool-call 格式转换，工具执行行为保持不变。
- 本轮让 Provider 成为 `run_agent` 的输入依赖，CLI 启动只发生在直接运行 `main.py` 时；导入模块可进行离线测试。
- 本轮为 Runtime 增加 `llm(...)` 命名边界；未来可以在这个边界逐步加入统一错误、统计或事件，但当前行为不变。
- 本轮让内部消息历史显式包含系统指令；Chat Completions Provider 原样保留该角色，未来 Responses Provider 可将其映射为 `instructions`。
- `.env` 只存在于本地工作区，不会被提交或推送；代码依赖 `openai`、`python-dotenv` 与 Pydantic 2.x。

暂未确认：

- Runtime 包名、目录结构和公共接口；这些都不在本轮决定。
- 未来 Provider 抽象如何承载 DeepSeek 与其他模型；这留到后续步骤。
- 多轮消息是否属于 Agent State、Session 或 Context；先不提前命名，等循环和持久化需求出现后再区分。
- 模型请求失败时如何恢复、限制轮数和检测重复调用；这些属于后续 Guard/错误处理步骤。
- 无参数 Tool 是否也使用 Pydantic，以及是否为所有 Tool 统一 args model；当前空参数 schema 仍足够简单。
- 如何支持第二个模型、模型能力差异、认证和模型目录；先用一个真实 Provider 验证接口，再扩展。
- Provider 请求失败如何转换为统一错误结果，以及 Streaming 如何表达增量事件；留到后续错误处理和 Streaming 步骤。
- `SYSTEM_PROMPT` 是否应该成为 `run_agent` 参数、Provider 配置还是 Context 字段；当前先固定在源码中观察实际需求。
- Runtime 层 `llm(...)` 未来是否需要接收 Context、模型选项或取消信号；当前只接收 Provider、messages 和 tools。
- Pydantic 验证错误的用户展示格式、错误分类和重试策略；本轮只把验证错误作为工具结果文本返回。
- 文件内容过大、二进制文件、并发读取和工具超时；这些属于后续 Guard/资源限制步骤。
- Pi 风格的字节级截断、图片内容块、AbortSignal 和 UI 渲染；这些暂不翻译到 Python 版本。
- 工具异常是否需要重试、分类、日志和用户可见诊断；本轮只返回一段错误文本。
- 有副作用 Tool 是否需要每次调用前的人类确认、显式覆盖参数和更细粒度的写入范围；这些留到 Guards / Human-in-the-loop。

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

- 日期：2026-08-22
- 命令：`python -m py_compile main.py`、FakeProvider system 消息顺序 smoke、FakeProvider Tool Calling 闭环 smoke、Provider system 角色转换 smoke、导入安全检查
- 结果：通过；`run_agent` 保留 system 消息并完成工具后续回答，`system_message_check=passed`。
- 问题：本轮仍依赖本地 `.env`，不将其提交；系统提示暂时固定在源码中，模型目录、第二个 Provider、Streaming、模型错误分类和重试暂不处理。
