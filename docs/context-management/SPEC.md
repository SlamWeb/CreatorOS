# Context Management：设计、实现与面试复习

## 长 Turn 的 Step 回退（完成，2026-09-13）

- 普通历史仍优先完整 user Turn；只有最新 Turn 自身超过近期保留预算，才允许摘要其中较早的完整 Step。
- Step 为一次 assistant 输出及其全部 tool results；不切开并行 tool-call 批次，不越过缺失或不匹配的结果。至少保留最后一个 Step，单个不可分割 Step 超预算仍走现有外置/硬预算保护。
- 中途切分时单独保留该 Turn 的用户请求原文，并计入保留预算；checkpoint 保存其原账本索引，后续压缩只处理新增历史和旧摘要，不重新读取已摘要的大段历史。
- 验收：普通短 Turn 不变、长循环切分、多工具配对、未完成 Step、原请求只注入一次、连续压缩/新请求/重启索引正确；小额真实 DeepSeek 摘要及继续回答。仅临时 Session，不修改正式数据。
- `CompactionPlan` 仍按 Token Budget 选完整 Turn，最新 Turn 超预算才扫描 Step 边界。`retained_messages` 保持连续原账本后缀，`pinned_user_index` 额外标识被切开 Turn 的用户原话；`estimated_retained_tokens` 包含原话与后缀。只有一个 Step 时不为移走用户原话而压缩。
- Checkpoint 的 `first_retained_index`、`source_message_count` 仍对应原始 Session；pin 不是新增账本消息。恢复投影为稳定前缀＋累计摘要＋用户原话（如有）＋近期后缀＋新追加消息。旧 checkpoint 缺少 pin 字段仍可加载。
- 第二次压缩只把 pin 临时补回规划输入，切分结果再映射回原账本索引；摘要输入为旧摘要＋本次待压缩 Step（带原请求作为语境）。下一条用户 Turn 到来后，旧残余 Step 被摘要时旧 pin 一并释放。摘要指令明确处于未完结 Turn 内，不把截断点误当任务完成；不能保证模型绝不丢语义。
- `python -m tests.check_step_compaction` 通过：完整/超长 Turn、单个不可分割 Step、多结果乱序、未完批次、原文不变、连续 checkpoint/重载/新 Turn/旧格式兼容；真实 Loop 使用确定性 Provider 和工具夹具，单条 query 内 8 次工具调用、9 次主调用，触发自动压缩并完成，Trace 共用一个 turn_id，账本保留8份完整结果。
- `python -m tests.check_step_compaction --live` 真实 DeepSeek 通过两次滚动摘要及重载后的继续回答，找回早期 `C7-ANCHOR-924` 并回复“仅草稿”。三次请求 input/output 分别为 663/521、1219/925、1822/12 tokens；总 input=3704、output=1458。测试使用合成历史、临时目录且无业务工具执行；这是单案例接线验证，不是约束保留率或压缩收益 Benchmark。一次早先运行的进程回执丢失，不计入上述已观测结果。
- 7项隔离回归通过：smoke_compaction_plan、smoke_compaction_checkpoint、smoke_compact_session、smoke_compacted_model_context、smoke_auto_compaction、smoke_context_protection、check_context_trace。没有新增依赖、前端或正式业务库变更。
- 面试口径：Turn 是一次用户请求，Step 是一次模型输出及对应工具结果；预算检查仍在每次主模型请求前。优先 Turn 保持完整语境，长循环才退到协议安全的 Step；原请求＋累计进度摘要承接语义，原始证据仍可回读。暂不做语义边界分类器、历史 embedding 或固定保留多条用户输入。下一步仍为 C4 Context Eval。

## C3 Context Trace（2026-09-12）

- 每个 Session 增加相邻 `.context-trace.jsonl`，记录请求 started/finished；UUID request_id、turn_id 跨进程不复用，session_id 为会话路径哈希，checkpoint_id 为检查点内容哈希。CLI reset 后保持独立 turn_id。
- 主请求在所有压缩/外置结束后统计最终 ModelContext；摘要请求统计实际带输出上限的上下文。互斥分项为 system、tools、summary、recent_messages、tool_results、skills、summary_source 和 serialization_overhead，合计等于既有预算估算。不是厂商 tokenizer 或精确 HTTP 字节统计。
- 实际 prompt/output/cache hit/cache miss 独立保存，缺失为 null；失败摘要即使被格式/收益校验拒绝也保留已知 usage。记录耗时、模型名、预算、压缩结果、外置数量、源消息位置；不存正文、工具参数、路径或异常原文。
- 复用同一 Loop/Compactor，Web 提供当前 Session 的分页只读 Trace API；本切片先完成数据与查询，不修改页面布局、不把 ContentRun 当成 Agent Session。
- 验收：隔离本地故障注入检查预算阻止、摘要格式/无收益失败、usage 缺失、中断、分项可加性、重启追加；真实 DeepSeek + 隔离 Web 验证主/摘要 usage 和请求关联。C4 质量对照另做。

## 最新决定：近期完整保留、旧结果外置（2026-09-11）

- 本节覆盖旧的“所有主请求工具结果固定首尾截取”策略；近期默认完整保留。只在压缩后的主请求仍超预算时，按体积从大到小外置工具正文，保留用户原文。
- 工具原文保留在 Session，并归档至同目录 `<session文件名>.tool-results/`（避免同目录多会话冲突）。文件名由 call ID 与正文摘要生成，不使用模型给出的路径。index.json 保留调用名、ID、简短描述、相对路径；索引不整份注入。
- 摘要区的工具结果替换为确定性说明及文件引用。摘要旁追加机器维护的索引入口，不依赖 LLM 记住所有路径；原消息不改。
- read_file 支持当前会话归档文件；Web 只允许此目录，CLI 普通文件规则不变。文件大时按字符分页，不取消普通文件安全限制；读回片段按近期结果保留。
- 不新增完整 Skill 加载能力：Web 虽允许 read_file，但只读归档，所以不得自动注入 Skill 目录。
- 验收：近期正文完整、旧摘要输入无巨大正文、原文/索引可读、跨会话/路径逃逸拒绝、滚动摘要索引仍在、真实 DeepSeek 摘要后按 read_file 找回随机证据。不实现 L3/长期 Memory/外部观测平台。

状态：2026-09-12；C1–C3 已完成，C4 尚未实现。第 3 节保留 C2 前基线，当前变更以顶部决定和实施记录为准。

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
| build_model_context | 组装 checkpoint、近期完整消息和 Skill 目录注入；预算超限时由 Loop 触发外置 | creatoros/agent/loop.py |

```text
完整 Session → checkpoint 投影 → 近期工具正文保持完整
             → 必要时旧/最大工具正文外置到归档 → ModelContext → 预算检查 → Provider
```

system/developer 稳定前缀在前，摘要以 user 历史资料注入；tools 是请求的独立字段。稳定前缀有利于缓存，但没有显式缓存控制或命中保证。Skill 目录按需加载完整文件；Web 虽开放归档限定的 read_file，但不因此注入 Skill 目录。

## 3. C2 前基线：历史实现事实（用于解释为什么改）

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
4. Web 白名单开放 read_tool_result，并告知模型按省略标记分页回查；后续归档方案再开放仅限当前会话目录的 read_file，不额外注入 Skill。
5. 保持 1-based 字符 offset、原分页上限及错误类型；缺失 ID 返回错误，不退回其他会话。

### 验收

- 两个临时会话具有相同 call ID，但正文不同：必须各自读对；另一个会话独有的 ID 不能读到。
- 主模型投影不包含中间证据；分页回读包含证据，原文未变。
- Loop 显式 session_file 优先于上下文中错误绑定；默认 CLI 兼容。
- 真实 DeepSeek 经隔离 Web HTTP 会话看到省略标记后调用回读工具，回答随机测试标记；不把标记答案写进用户问题。合成资料明确不是真实调研。
- 不改正式业务库、不生图/发布、不做 UI 改版。故障和数据变换用本地确定性断言，真实模型验证不以 Fake 替代。

## 5. 后续切片与退出条件

### C2 执行约定（2026-09-11）

- 预估达到输入硬预算才停止主请求；90% 仍只是压缩触发线，不能把接近窗口等同超限。
- 摘要请求独立计算输入（含旧摘要、序列化标记和摘要指令），输出上限为 min(4096, Provider 输出预留)。超限先拒绝，不分块、不丢用户正文、不循环重试。
- 摘要输出使用请求级 max_tokens 限制，并在保存前检查长度与实际压缩收益；失败或无收益不覆盖已有 checkpoint。
- 自动摘要失败时，若原请求仍在预算内则警告后继续；若原请求超限则停止本轮。用户输入仍保存，Web 显式显示失败原因；原始 Session 不删。
- 本阶段基于估算，不保证服务端永不报窗口错误。真实小额摘要验证加本地故障注入，不用百万 Token 制造超限。

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
- C2 完成记录见下；C3–C4 未完成，不以接线验收声称压缩后决策不退化。

### C2 完成与验证

- 摘要独立检查整个输入：包含指令、旧摘要及序列化历史；超过估算预算不调用模型。ModelContext 增加可选 max_output_tokens，DeepSeek complete 映射 max_tokens；摘要上限为 min(4096, Provider 输出预留)，普通请求未设置时保持原行为。
- 摘要输出估算超限、格式错误或投影未缩减时拒绝替换旧 checkpoint。主循环摘要失败后保留原上下文，未超硬预算则继续，超限则 context_blocked 并停止本轮。90% 预警线不等于禁止请求线。
- Web 保存停止事件并显示 failed/error，CLI 回到输入循环；用户输入仍落盘。不把上游异常正文透传用户，不自动重试、分块摘要或删除历史。
- `tests.smoke_context_protection` 通过：等于/超过硬边界、超长单轮零主调用、摘要预检零调用、软预算失败后继续/硬预算停止、错误脱敏、无收益时旧 checkpoint 字节不变、请求输出限制、Web failed 状态。
- `tests.live_compact_session` 真实 DeepSeek 通过：input 786 / output 275 tokens，切分位置 5。小额协议与 checkpoint 保存验收，不是语义质量评估。
- 回归通过：smoke_auto_compaction、smoke_compact_session、smoke_web_agent、smoke_model_context、smoke_compaction_summary、check_session_result_read。旧成功夹具依赖超限摘要/膨胀摘要，改为可摘要的大工具结果或足够旧历史；失败路径另有显式断言，不放宽预算规则。
- 限制：估算非精确 tokenizer；不保证服务端永不报超限；摘要失败的 usage 尚待 C3 统一记录。没有正式库修改、内容生产/发布或前端改版。
- 面试回答更新：累计摘要不会多份叠加，C2 还限制输出并拒绝无缩减替换，但保住语义需要 C4 业务对照，长度检查不能证明质量。
- 下一步 C3：补投影与压缩轨迹；C4 再做业务质量与成本对照。

### 近期完整保留与旧结果外置记录（2026-09-11）

- `creatoros/session/artifacts.py` 为每个 Session 建立 `<messages文件名>.tool-results/`，以 `sha256(tool_call_id + 正文)` 命名原文文件，并维护 `index.json`；写入使用临时文件替换且拒绝符号链接。重复结果不会产生第二份正文。
- Loop 不再对每个主模型请求自动做 16,000 字符首尾投影。工具结果写入 Session 后立即归档一份原文；近期消息仍把完整正文送入模型。压缩摘要输入则使用短描述、结果引用和索引入口；硬预算仍超限时，才从最大的工具正文开始外置，用户消息不被外置。
- `read_file` 增加 `unit=chars`，Web 通过 `archive_only_reads` 只允许读取当前 Session 归档；请求范围、符号链接、跨会话路径和无绑定会话均拒绝。每页返回范围与 `next_offset`，提示按顺序分页，不能用抽样证明全文不存在。
- 本地验证 `python -m tests.check_archived_context`、`tests.check_session_result_read`、`tests.smoke_read_tool_result` 通过：近期正文完整、索引含原调用 ID、原文精确可读、两个会话互相隔离、路径逃逸拒绝、超长单行要求字符分页。
- 真实 DeepSeek 隔离 Web 试验曾发现一个重要 badcase：模型读取开头/中部/末尾三个跳跃区间后漏掉位于约 18,000 字符处的随机标记，并错误声称资料没有该字段。该次结果保留为失败证据；因此当前实现只改善“可回读性”，不声称模型一定会穷尽搜索。另一次真实摘要返回缺少规定标题，被现有格式校验拒绝，旧 checkpoint 未被覆盖。
- 更新后的真实 DeepSeek 隔离验收通过：模型按 `next_offset` 从 1 连续读取到标记所在页，6 次 `read_file` 后返回准确随机值；摘要 input/output 为 336/500 tokens，后续主请求合计 usage 为 34,414/1,328 tokens（含各请求的 cache hit 字段）。证据保存在本地临时 `tmp/archived-context-20260911-180558/report.json`，合成证据和临时 SQLite 均未进入正式库。
- 这条链路仍不是 L3/长期 Memory，也没有语义检索、自动 grep/search 或完整上下文 Trace；下一步应在 C3/C4 记录归档读取轨迹并评估“连续分页能否找回证据”，而不是继续增加投影魔法。

### C3 完成与验证（2026-09-12）

- 新增 `session/context_trace.py`；主请求在预算恢复之后记录，摘要 span 覆盖预检、调用、格式验证、收益验证和保存 checkpoint，失败可以从 stage 定位；exception 仅存类型。原 ContextBudget 估算和工具执行规则不改变。
- started/finished 共用 request_id；同一用户指令的多次主调用与自动摘要共用 turn_id。独立调用 compact_session 则拥有独立 turn_id；通过 output_checkpoint_id 关联后续使用这份摘要的主调用。`source_message_count` 定位原 Session 请求前边界，返回工具只记录名称与调用 ID，正文仍在原 Session。
- Trace是诊断元数据，未另存每份请求正文或旧checkpoint文件；原Session reset/宿主规则变更后不承诺逐字重建过去的完整请求。保留记录不等于实现完整可重放审计系统。
- 使用 `estimated_parts` 互斥归类，Tool Results 不重复算入 Recent Messages；summary_source 表示摘要专用的待总结历史；加载 Skill 全文后的 read_file 结果属于 tool_results，skills 仅表示注入的目录元数据。system 包含固定 Runtime Rules，framing/舍入余量归 serialization_overhead。主请求 `tokens_before` 是此次恢复前的有效投影估算，不是整个原始账本体积。
- `usage=null` 表示没有收到用量；cache 字段缺失时为 null，不推算为零。SDK 内部重试并非独立 span，本版统计 Provider 调用与最终可见 usage，不能保证覆盖服务端未知失败费用。
- `sent=false + blocked` 表示本地预检拒绝发送；started 且没有 finished 表示运行中或硬中断后的未知结局，不能当成功。JSONL 不完整末行等待下次读取，重启追加时隔开残片；普通关闭刷新文件，不承诺断电持久性或多进程并发写同一 Session。
- 新增 `GET /api/agent/sessions/{session_id}/context-trace?after=0&limit=50`，按事件行分页，上限100；未知会话404、非法范围422、无记录返回空。返回计数/ID/类型，无正文或文件路径。页面尚无图表组件，ContentRun 不挂接为同一对象。
- `tests.check_context_trace` 故障注入与纯变换通过：分项可加、压缩关联、失败摘要保留usage、硬预算/摘要预检不调用、外置后统计、流异常/中断、缺失usage、分页和重启残片。关联9项 Runtime/Web/归档回归通过，默认 Session 文件替换为临时目录以避免污染。
- `tests.check_context_trace --live` 真实 DeepSeek + 隔离 Web 通过，`tmp/context-trace-b62735cedc/report.json`：摘要 input/output=9,176/215，主请求两次=3,215/63、3,314/135；主请求cache hit=2,688、3,072。实测与Web原usage逐项核对；最后请求估算3,393而实测3,314，明确显示估算误差。仅合成历史与查询空测试库，无生产/发布/正式库修改。
- 下一步 C4：用约束保留、改口、ID、证据回读、滚动摘要和重启六类开发案例，对完整历史/摘要/摘要加证据回读做任务结果与总费用比较。此处的trace smoke不是质量Benchmark。
