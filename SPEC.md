# CreatorOS Runtime SPEC

Progressive SPEC, not a form.

## 当前理解

- CreatorOS 是面向创作者 / MCN 场景的长期项目；当前阶段只从零学习并手写极简 Python Agent Runtime。
- 用户具备 Python 基础和简单 Agent Loop 经验，希望逐层理解 Agent Harness / Runtime，并能在求职和面试中讲清工程取舍。
- Pi Agent 只作为架构参考；实现应从 Python 的最小可运行版本逐步演进。
- 第一个可运行切片已经完成：Python 通过 OpenAI-compatible SDK 调用 DeepSeek，并打印一条回复。
- Pi 当前给我们的一个具体启发是：模型适配和 Agent 状态属于不同问题；本轮只把消息历史显式化，不复制 Pi 的 TypeScript 包结构。

## 本轮目标

本轮只迈一个 `small step`：

- 把内联的 `messages` 提取成 Runtime 明确持有的列表。
- 在一次模型回复后，把 assistant 消息追加回列表，为后续多轮调用准备数据形状。
- 仍然只调用模型一次，不加入 `while` 循环、Tool 或 Provider 抽象。

## 当前假设

- 第一段 Runtime 代码只验证一次 OpenAI-compatible LLM 调用，不包含循环、工具或抽象层；本轮已验证。
- 初始模型使用 `deepseek-v4-flash`，通过 `https://api.deepseek.com` 调用；后续仍可在理解 Provider 后调整。
- `.env` 由 `python-dotenv` 在程序启动时加载，且由 `.gitignore` 排除。
- 当前 `messages` 是一轮对话的工作状态：开始时有一条 user 消息，调用后追加一条 assistant 消息。
- 项目早期所有模块共享根级 `SPEC.md`；出现稳定模块边界后，再在最近模块建立独立 `SPEC.md`。

## 对外影响

- 本轮把消息历史从 API 参数提升为本地工作状态；运行 `python main.py` 仍只发起一次真实模型请求并打印文本。
- `.env` 只存在于本地工作区，不会被提交或推送；代码依赖 `openai` 与 `python-dotenv`。

暂未确认：

- Runtime 包名、目录结构和公共接口；这些都不在本轮决定。
- 未来 Provider 抽象如何承载 DeepSeek 与其他模型；这留到后续步骤。
- 多轮消息是否属于 Agent State、Session 或 Context；先不提前命名，等第二次调用出现真实需求后再区分。

## 验收与验证草案

- `main.py` 能从本地 `.env` 读取 `DEEPSEEK_API_KEY`，完成一次非思考模式的 DeepSeek 调用，并将 user/assistant 两条消息保存在同一个列表中。
- `requirements.txt` 包含运行所需的两个依赖；`.env` 被 `.gitignore` 忽略。
- 仓库没有提交 API Key、完整 `.env`、Python 缓存或其他秘密信息。
- 本轮不出现 Agent Loop、Tool、Session 或其他未学习的抽象。

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
- 命令：`python -m py_compile main.py`、`python -c "import runpy; ..."`
- 结果：通过；真实 DeepSeek 请求成功，`message_state_check=passed`，消息列表包含 user 与 assistant 两条记录。
- 问题：无新增问题；本轮仍依赖本地 `.env`，不将其提交。
