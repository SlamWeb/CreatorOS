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

## 本轮目标

本轮只迈一个 `small step`：

- 创建一个最小 `execute_tool_call` 边界函数。
- 在其中处理未知工具、坏 JSON、非 object 参数和 Tool 执行异常。
- 将错误转换成 `role="tool"` 可返回给模型的文本，保持内层 Agent Loop 继续运行。
- 不加入重试、日志、错误分类、权限系统或新的 Tool。

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
- 项目早期所有模块共享根级 `SPEC.md`；出现稳定模块边界后，再在最近模块建立独立 `SPEC.md`。

## 对外影响

- 本轮让 Runtime 在单次 Tool 调用失败时仍能追加工具结果并回到模型；正常情况下仍可选择时间、日期或分段读取项目内文本文件。
- `.env` 只存在于本地工作区，不会被提交或推送；代码依赖 `openai` 与 `python-dotenv`。

暂未确认：

- Runtime 包名、目录结构和公共接口；这些都不在本轮决定。
- 未来 Provider 抽象如何承载 DeepSeek 与其他模型；这留到后续步骤。
- 多轮消息是否属于 Agent State、Session 或 Context；先不提前命名，等循环和持久化需求出现后再区分。
- 模型请求失败时如何恢复、限制轮数和检测重复调用；这些属于后续 Guard/错误处理步骤。
- 参数 schema 与 Python 函数签名不一致时如何验证和报错；本轮仍依赖模型按 schema 发送 JSON object，不提前引入通用校验器。
- 文件内容过大、二进制文件、并发读取和工具超时；这些属于后续 Guard/资源限制步骤。
- Pi 风格的字节级截断、图片内容块、AbortSignal 和 UI 渲染；这些暂不翻译到 Python 版本。
- 工具异常是否需要重试、分类、日志和用户可见诊断；本轮只返回一段错误文本。

## 验收与验证草案

- `main.py` 能从本地 `.env` 读取 `DEEPSEEK_API_KEY`，从 `Tool` 对象生成 schema，通过 `execute_tool_call` 解析 JSON arguments 并执行带可选参数的 `read_file(path, offset, limit)`，再追加正确的 `tool_call_id`。
- `read_file` 对项目内 UTF-8 文件返回指定行段，对项目外路径、目录、缺失文件、非 UTF-8 文件和越界行号返回可供模型理解的错误文本。
- 坏 JSON、非 object 参数、未知工具和 Tool 内部异常不会让 Agent Loop 直接退出，而会变成工具结果文本。
- `requirements.txt` 包含运行所需的两个依赖；`.env` 被 `.gitignore` 忽略。
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
- 命令：`python -m py_compile main.py`、构造坏 JSON/非 object/未知工具/坏参数的本地 smoke、正常 `read_file` 模型调用后 `/exit`
- 结果：通过；所有失败输入都返回工具错误文本，正常 Tool 调用仍能完成，`tool_error_boundary_check=passed`、`tool_error_boundary_live_smoke=passed`。
- 问题：本轮仍依赖本地 `.env`，不将其提交；错误分类、重试、日志和副作用权限暂不处理。
