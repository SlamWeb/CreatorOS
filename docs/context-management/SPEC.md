# Context Management：设计、实现与面试复习

状态：2026-09-11 设计确认；C1 已完成，C2–C4 尚未实现。

## 1. 为什么做

例子：工具返回三万字资料，决定选题的证据位于中间。模型只看到首尾，即使全文已存盘，也可能选错。我们需要让模型按当前会话的 result_ref 回读证据，而不是只宣布“历史没有丢”。

目标是让长对话仍能正确运营：保留目标、最新决定、禁止动作及可回查证据。不是追求最短摘要，也不是越多上下文越好。固定模型下，输入信息影响决策；宿主工具权限与数据库校验仍决定动作能否执行。

## 2. 概念与代码地图

| 对象 | 负责什么 | 代码 |
|---|---|---|
| Session | 完整 user/assistant/tool 消息账本 | creatoros/session/snapshot.py |
| RuntimeContext | 宿主执行环境，不是模型提示词 | creatoros/context.py |
| ModelContext | 一次请求的只读输入快照 | creatoros/ai/context.py |
| ContextBudget | 输入估算、输出预留、真实 usage | creatoros/ai/context.py |
| CompactionPlan | 按完整用户轮次分旧历史与最近历史 | creatoros/agent/compaction.py |
| SummaryRequest | 旧历史与上一份摘要生成新 Markdown 摘要 | creatoros/agent/compaction_summary.py |
| CompactionCheckpoint | 保存摘要、切分位置、源校验及保留尾部 | creatoros/session/checkpoint.py |
| build_model_context | 串起投影、工具结果裁剪和 Skill 目录注入 | creatoros/agent/loop.py |

```text
完整 Session → checkpoint 投影 → 大工具结果首尾裁剪
             → 可用 Skill 目录 → ModelContext → 预算检查 → Provider
```

system/developer 稳定前缀在前，摘要以 user 历史资料注入；tools 是请求的独立字段。稳定前缀有利于缓存，但没有显式缓存控制或命中保证。Skill 目录按需加载完整文件；Web 没开放 read_file，因此不注入不可执行的 Skill 目录。

## 3. 当前机制：实现事实，不是目标

- DeepSeekProvider 配置窗口 1,000,000、输出预留 32,768；这只是仓库配置，不是自动发现模型规格。输入预算 967,232，估算使用到约 90% 时尝试自动压缩。
- 请求前用 ASCII/非 ASCII 字符规则估算；请求后读取实际 usage，但未持续校准下一次估算。不是精确 tokenizer，也不保证估算一定偏大。
- 最近历史预算为 input_limit / 8，限制在 8k–128k；当前配置对应 120,904 tokens。按完整 user turn 回溯，最后一轮至少保留，即使超预算。
- 首次压缩旧轮次；后续用“上一份摘要＋新进入压缩区的轮次”更新一份摘要。发送“稳定前缀＋最新摘要＋保留轮次＋追加消息”，不无限叠加摘要。
- 主模型工具正文首尾合计保留 16,000 字符；摘要输入每条工具正文保留 4,000 字符，另加 marker。这里不是 Token 或 KB，也不是整个请求的硬上限。
- 摘要检查非空、禁止工具调用及固定 Markdown 标题；没有语义完整性保证、专用摘要长度预算或压缩收益检查。
- 原消息保留，checkpoint 原子写文件；源校验失配回退原消息。不是永久审计存储：reset 会清空，system 更新和中断修复也会改变快照。
- 当前压缩后仍超限只警告，仍可能发送请求；摘要请求整体大小没有独立保护。CLI 主循环未接 /compact 命令，已有内部 compact_session 函数。

## 4. C1：当前会话的证据回读

### 痛点与最小方案

旧 read_tool_result 无条件 load_messages()，读取 CLI latest.json；Web 隔离会话也未开放该工具。

1. RuntimeContext 增加可选 session_file，仅宿主提供；模型参数仍只有 result_ref/offset/limit。
2. run_agent 以自身实际读写的 session_file 绑定工具上下文，防止传入的 RuntimeContext 指向另一会话；不修改调用者对象。
3. read_tool_result 从绑定文件读取；仅直接无上下文调用兼容原 CLI 默认文件。不跨会话搜索，不提供任意路径读取。
4. Web 白名单开放 read_tool_result，并告知模型按省略标记分页回查；不开放 read_file，不额外注入 Skill。
5. 保持 1-based 字符 offset、原分页上限及错误类型；缺失 ID 返回错误，不退回其他会话。

### 验收

- 两个临时会话具有相同 call ID，但正文不同：必须各自读对；另一个会话独有的 ID 不能读到。
- 主模型投影不包含中间证据；分页回读包含证据，原文未变。
- Loop 显式 session_file 优先于上下文中错误绑定；默认 CLI 兼容。
- 真实 DeepSeek 经隔离 Web HTTP 会话看到省略标记后调用回读工具，回答随机测试标记；不把标记答案写进用户问题。合成资料明确不是真实调研。
- 不改正式业务库、不生图/发布、不做 UI 改版。故障和数据变换用本地确定性断言，真实模型验证不以 Fake 替代。

## 5. 后续切片与退出条件

| 阶段 | 改什么 | 完成条件 |
|---|---|---|
| C1 | 当前 Session 回读 | 上述隔离、分页、真实模型验证通过 |
| C2 | 预算保护 | 摘要与主请求独立预算；超限不盲发；不可切单轮明确失败；摘要失败保留原数据 |
| C3 | 最小上下文 Trace | 关联 session/turn/checkpoint；投影前后大小、摘要 usage、版本与耗时可审计 |
| C4 | Context 任务评估 | 完整历史/只裁工具/裁剪加摘要三组对照，评最终业务行为与总成本 |

C2 不直接更换 tokenizer 或窗口配置。先定义估算误差及保护策略；不能声称估算阈值提供绝对 Token 上限保障。C3 不记录密钥，不把未经脱敏上下文上传外部观测平台；重放记录不等于重新执行有副作用工具。

## 6. 小型 Context Eval

六类：早期“仅预览”约束、用户改口以后者为准、同名对象准确 ID、工具中间证据回读、连续两次压缩不复活取消任务、重启后继续。

每题固定环境/多轮输入/允许结果/禁止变化。长度可控的相同历史分别用完整上下文、仅工具投影、投影加真实摘要运行；通过测试参数强制切分，不必消耗百万 Token 才测压缩。另用确定性边界测试验证自动触发。

- 主指标：最终预览/Run/队列与约束是否正确；必要的澄清也可以成功。
- 诊断：关键事实、决定、ID 是否遗失/变更，原证据能否回读；不以摘要好看或固定调用顺序评分。
- 成本：摘要请求＋后续模型请求＋回读造成的输入，使用真实 usage；缓存命中是 input 子集。对共同成功案例比较成本，不奖励直接拒绝任务的低消耗。
- 先六题开发集，所有失败保留；后续再增加不同情境保留集与重复试验，不宣称六题就是泛化 Benchmark。
- 不评 PersonClone 生成质量，不重建长期 Memory 系统，不安装观测平台，不复制 Pi 源码。

## 7. 面试问答

**为什么原始 Session 与 ModelContext 分离？** 原记录用于恢复/取证，模型输入需要预算和相关性控制；压缩是读取视图的变化，不应销毁证据。

**API 有 usage 为什么还估算？** usage 是已完成请求的计费事实；发送下一轮前需要预测新增消息与工具 schema 的体积。当前粗估仍有误差，待 C2 改进保护，不能假装精确。

**为什么不用固定最近五轮？** 一轮可能只有一句话，也可能有巨大工具正文。预算控制体积，完整轮次控制协议边界；但超长单轮需要单独处理。

**为什么摘要还有裁剪？** 主模型投影和摘要输入服务不同请求，各自占预算；单条裁剪不等于总请求受控。中间证据会丢，所以必须可回读并做业务对照验证。

**摘要会不会越积越长？** 每次替换为一份累计摘要，不叠多份；但累计摘要自身仍可能增长，摘要输出预算与收益检查属于 C2，不是已解决。

**这是长期记忆吗？** 不是。它是当前会话连续性管理；账号定位、反馈等业务持久化与跨会话记忆召回是不同责任。

**行业参考怎么用？** 延续 Pi 的 Python 化架构学习原则：宿主状态、模型请求视图和工具边界分离，不复制完整框架。评测参考 OpenAI 的任务级评测/trace grading 与 Anthropic 的环境终态评分；本轮不声称复现 Pi 当前具体压缩算法。

参考（前轮已查阅）：
- https://developers.openai.com/api/docs/guides/evaluation-best-practices
- https://developers.openai.com/api/docs/guides/trace-grading
- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

## 8. 实施记录

- C1：RuntimeContext 增加 session_file，Loop 绑定真实账本并覆盖错误的宿主上下文值；read_tool_result 使用该绑定；Web 开放只读回查，未开放 read_file 或跨会话路径。
- `python -m tests.check_session_result_read --live` 通过：随机标记藏在 36,039 字符正文中间，初始投影确实不包含答案；真实 DeepSeek 经 localhost HTTP 调用两次 read_tool_result（offset 1、16001），正确读回标记。合成资料与隔离 SQLite，不修改正式库或启动生产。
- 本次 3 次模型请求累计 input 25,263 / output 257 / cache hit 13,440 tokens；缓存包含于输入。证据：`tmp/context-read-20260911-120743/report.json`。这是单次链路验收，不是泛化质量或优化收益。
- 本地确定性验证：相同 ID 不串会话、其他会话独有 ID 不可读、缺失 ID、默认 CLI 兼容、Loop 覆盖错误绑定且不修改调用者对象、投影和原文保留。
- 回归通过：smoke_web_agent、smoke_runtime_context、smoke_auto_compaction、smoke_tool_result_projection。受控模型只用于确定性接线/故障检查，真实回读使用 DeepSeek。无页面改动，未做视觉验收。
- C2–C4：计划，未完成。下一步 C2；先解决超限不盲发与摘要请求预算，不以 C1 声称压缩后决策不退化。
