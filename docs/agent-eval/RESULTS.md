# CreatorOS 运营 Agent Eval 结果

## 开发集第二轮：真实任务与一次规则修复

本轮运行了六个开发任务双组各一次、D05 澄清版双组各一次，以及修复后的六题双组回归，共 26 次任务试验，实际 input+output 合计 **316,001 tokens**（包含改口/恢复任务的前置模型调用）。均为真实 DeepSeek、隔离 HTTP/SQLite；使用固定候选，不执行联网调研、生产或发布。

| 任务 | 首轮 split / unified | 规则修复后 split / unified |
|---|---|---|
| D01 选择第二条 | 通过 / 通过 | 通过 / 通过 |
| D02 同名栏目归属 | 通过 / 通过 | 通过 / 通过 |
| D03 排除第三条、改第一条标题 | 通过 / 通过 | 通过 / 通过 |
| D04 多轮改口、保留已有预览 | 通过 / 通过 | 通过 / 通过 |
| D05 只读筛选 | v2 判分存在歧义；v3 通过 / 失败 | v3 通过 / 通过 |
| D06 重载会话后找回已有链接 | 通过 / 通过 | 通过 / 通过 |

最终回归在每组六题单次试验中均为 **6/6**。这是一轮用于开发的回归结果，不是保留集成功率，也没有证明 unified 比 split 更好。两组都能完成任务，所走查询路径不强制相同。

### 失败分析与修复依据

1. **评测自身过严**：D05 v2 的用户只说“当前待选”，grader 却禁止回答任何位置出现已入队标题。模型在表格/备注说明 queued 状态也被判错。原始自动结果保留，但这一对试验不作为可靠的模型失败率依据。D05 v3 明确要求“不要列出或重复已入队标题”，两组重跑。
2. **明确指令仍被补充说明破坏**：v3 unified 正文仅列 c2/c3/c4，却在备注中重复 c1 标题，并称“按要求未列出”。该题判失败；没有错误入队或生产，是输出范围约束问题。加入共享 DISPLAY_SCOPE_RULE，要求筛选/禁止重述也适用于补充说明；实际 Web 宿主和实验复用同一规则。修复后 D05 双组通过，但每组仅一次，不能断言此问题已稳定消除。
3. **grader 的假覆盖**：旧 smoke 报告提及错顺序，但只构造一条选题；本轮加入两条交换顺序的负例、伪造链接的负例，以及只读结果标题/来源检查。该问题属于评测基础设施，不冒充 Agent Badcase。

### 修复后成本（真实累计 usage）

| 任务 | split total tokens | unified total tokens |
|---|---:|---:|
| D01 | 10,335 | 11,492 |
| D02 | 10,257 | 11,463 |
| D03 | 10,522 | 10,889 |
| D04 | 20,086 | 20,938 |
| D05 | 6,543 | 7,324 |
| D06 | 14,169 | 14,956 |

本轮三批总用量分别为 153,345、13,682、148,974 tokens；原始报告保留 input/output/cache hit/cache miss，cache hit 是 input 子集。未进行三次重复，也未测发布成功率。该用量不包含上一轮 D01 探针。

### 原始证据与限制

- `tmp/operating-eval-dev-v2/report.json`：首轮六题，D05 v2 待复核。
- `tmp/operating-eval-d05-v3/report.json`：题目澄清、未添加展示规则的双组试验。
- `tmp/operating-eval-dev-scope-fix/report.json`：添加展示规则后六题回归。
- 每题保留独立数据库、Session、请求快照、Context Trace，以及 Operation/Topic 前后快照。原始数据只留本地，不提交确认令牌。
- 上述运行基于 `41b511f` 加本轮未提交改动；JSON 中 git_sha 只记录基线 HEAD，不能当成精确代码快照。后续 runner 已新增 source-hashes.json。请求快照可复查实际 system/tool schema。
- v2 使用 Web 的 STUDIO_TOOLS，v3 另启用与 Web 一致的 archive_only_reads；宿主仍使用实验说明而非完整 WEB_INSTRUCTIONS。因此本结果是共享 Runtime 上的契约实验，不等于完整 Web 用户体验评测。
- D02 使用同名栏目和不同账号，但用户同时给定栏目 ID 和批次，难度有限。D06 是真实持久化重载，不是进程崩溃恢复。D05 的正式项由隔离夹具构造，不测人工确认流程。
- 当前逐题有请求数/时间检查、任务间 usage 软阈值；尚未实现跨多次命令共享的持久预算账本或严格180秒强制取消。本轮总量未接近原计划上限；不宣称严格费用封顶。

### 已验证及下一步

smoke_operating_eval、smoke_context_eval、smoke_topic_research、smoke_web_agent、smoke_agent_studio 均通过，宿主规则修改后重跑 Web smoke 通过。没有前端改动。

下一阶段先实现 H01–H06 的夹具与人工复核判分，再冻结版本运行；当前 CLI 会拒绝 Hxx，不提供假续跑命令。开发任务续跑使用新目录：

```powershell
python -m tests.eval_operating_tasks --live --dev --output tmp/operating-eval-dev-next
python -m tests.eval_operating_tasks --live --case D05 --output tmp/operating-eval-d05-next
```

以下保留第一轮 D01 报告，属于历史记录；其“尚未运行开发集”等描述已由上方记录更新。

更新时间：2026-09-14
数据集：`creator-operating-v1`，`case_version=1`
代码版本：`f529b63dda025d125c87673881e98b9294d84006`

## 本轮结论

第一批只运行了 D01 的两组单次真实探针，不能据此声称统一选题库整体提升。两组都完成了同一个任务：读取 `series-eval-a` 的 4 条待选候选，选列表第二条 `c2`，创建一条 `awaiting_approval` Preview；正式 Topic、已有 Operation、调研、生产和安装副作用均保持不变。

这证明了评测接线、隔离夹具、终态判分和两组工具契约可以跑通；还没有回答“哪套契约在更多任务上更好”。完整结果需要继续运行 D02–D06 与 H01–H06，并按 SPEC 的预算和复核规则记录未运行项。

## 对照配置

| 项目 | split_catalog（旧契约） | unified_catalog（当前契约） |
|---|---|---|
| 待选查询 | `get_topic_research(batch_id)` | `list_series_topics(series_id, state=pending)` |
| 选择/预览 | `prepare_topic_selection` | `prepare_topic_selection` |
| 模型 | `deepseek-v4-flash` | `deepseek-v4-flash` |
| 主模型请求 | 3 | 3 |
| 数据库/服务 | 独立临时 SQLite + 随机本机端口 | 独立临时 SQLite + 随机本机端口 |
| 正式数据 | 未写入 | 未写入 |

旧组在评测作用域同时替换模型可见的 `list_series_topics` schema 和实际 registry；没有把旧 schema 接到新 endpoint。两组使用相同结构的 4 候选合成夹具，来源是有效 HTTP(S) 测试 URL，不代表真实调研结论。

## D01 终态结果

| Arm | Task Success | 终态检查 | 工具轨迹 | 副作用尝试 |
|---|---:|---|---|---|
| split_catalog | 1/1 | 8/8 通过 | `get_topic_research` → `prepare_topic_selection` | 0 |
| unified_catalog | 1/1 | 8/8 通过 | `list_series_topics(pending)` → `prepare_topic_selection` | 0 |

两组均满足：

- 只新增 1 条、且 scope 为 `series-eval-a` 的 Preview；
- Preview 状态为 `awaiting_approval`；
- 选题 ID、标题、切入点、推荐理由、来源和顺序与夹具完全一致；
- 原有 Operation 与正式队列 before/after 相同；
- 没有调用 `start_content_run`、`research_series_topics` 或 `install_producer_skill`。

## Usage 记录

Usage 来自每次真实 DeepSeek 响应的 `session.context-trace.jsonl`，不是估算值。缓存命中和未命中分开记录，不能相加解释为额外 input。

| Arm | Input tokens | Output tokens | Total tokens | Cache hit | Cache miss |
|---|---:|---:|---:|---:|---:|
| split_catalog | 14,919 | 519 | 15,438 | 13,824 | 1,095 |
| unified_catalog | 16,181 | 508 | 16,689 | 14,464 | 1,717 |

本次 unified 组的 schema/说明上下文更长，单样本 total tokens 高于 split 组；样本量为每组 1，不能推断稳定成本差异，也不能把它当作质量劣化。

原始隔离产物（未提交）保存在：

```text
tmp/operating-eval-d01/split_catalog/
tmp/operating-eval-d01/unified_catalog/
```

其中包含独立 `eval.db`、Session、工具结果索引、请求快照和 Context Trace；没有读取或修改正式运营数据库。

## Grader 与负例 Smoke

无模型 smoke 已通过：

```text
operating_eval_smoke=passed cases=12 positive_and_negative_controls=passed
```

覆盖了空 Preview、错误 ID、错误标题、错误 brief、错误顺序、错误栏目、额外 Operation、禁止工具尝试、正式队列突变、只读查询和真实歧义澄清等控制。grader 以环境终态和工具轨迹为准，不接受最终文本单独宣称“已完成”。

## 未完成与续跑

尚未运行：D02–D06、H01–H06、重复运行、压缩/恢复任务的真实模型探针，以及人工复核项 H02/H05。当前 runner 只实现了低成本第一片 D01，故不会伪造开发集或保留集总分。

本地夹具与单元级检查：

```powershell
D:\Anaconda4.7g\envs\deepcode\python.exe -m tests.smoke_operating_eval
D:\Anaconda4.7g\envs\deepcode\python.exe -m tests.eval_operating_tasks
```

D01 双组真实续跑（需要本地 `DEEPSEEK_API_KEY`，会产生真实模型调用）：

```powershell
D:\Anaconda4.7g\envs\deepcode\python.exe -m tests.eval_operating_tasks `
  --live --case D01 --output tmp/operating-eval-d01-rerun
```

扩展开发/保留集前，应先为 runner 增加对应 case 的隔离初始状态、follow-up 输入和终态期望，再按 SPEC §6 的 1,000,000 visible-token 软上限逐题执行；额度不足时保留已完成结果，不把未运行项计入分母。
