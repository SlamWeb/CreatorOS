# CreatorOS 运营 Agent Eval 结果

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
