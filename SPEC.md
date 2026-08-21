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

## 本轮目标

本轮只迈一个 `small step`：

- 用 `while True` 重复“读取用户输入 → 追加 user 消息 → 调用模型 → 追加 assistant 消息 → 打印回复”。
- 用 `/exit` 提供最小的显式退出条件。
- 不加入 Tool、Provider、Guard、Session 或异常处理抽象。

## 当前假设

- 第一段 Runtime 代码只验证一次 OpenAI-compatible LLM 调用，不包含循环、工具或抽象层；本轮已验证。
- 初始模型使用 `deepseek-v4-flash`，通过 `https://api.deepseek.com` 调用；后续仍可在理解 Provider 后调整。
- `.env` 由 `python-dotenv` 在程序启动时加载，且由 `.gitignore` 排除。
- 当前 `messages` 是一次 CLI 运行内的工作状态：每轮按 user → assistant 顺序追加消息。
- `/exit` 只控制本地循环，不会发起模型请求。
- 项目早期所有模块共享根级 `SPEC.md`；出现稳定模块边界后，再在最近模块建立独立 `SPEC.md`。

## 对外影响

- 本轮让本地工作状态跨越任意多次模型请求；运行 `python main.py` 会持续等待输入，直到收到 `/exit`。
- `.env` 只存在于本地工作区，不会被提交或推送；代码依赖 `openai` 与 `python-dotenv`。

暂未确认：

- Runtime 包名、目录结构和公共接口；这些都不在本轮决定。
- 未来 Provider 抽象如何承载 DeepSeek 与其他模型；这留到后续步骤。
- 多轮消息是否属于 Agent State、Session 或 Context；先不提前命名，等循环和持久化需求出现后再区分。
- 模型请求失败时如何恢复、限制轮数和检测重复调用；这些属于后续 Guard/错误处理步骤。

## 验收与验证草案

- `main.py` 能从本地 `.env` 读取 `DEEPSEEK_API_KEY`，在每次非 `/exit` 输入后完成一次 DeepSeek 调用，并按 user/assistant 顺序追加消息。
- `requirements.txt` 包含运行所需的两个依赖；`.env` 被 `.gitignore` 忽略。
- 仓库没有提交 API Key、完整 `.env`、Python 缓存或其他秘密信息。
- 本轮只出现最小 `while` Agent Loop，不出现 Tool、Session 或其他未学习的抽象。

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
- 命令：`python -m py_compile main.py`、管道输入两轮问题后 `/exit`
- 结果：通过；循环处理两次输入并得到两次 Agent 回复，收到 `/exit` 后退出，`agent_loop_smoke_check=passed`。
- 问题：本轮仍依赖本地 `.env`，不将其提交；错误恢复和退出之外的 Guard 暂不实现。
