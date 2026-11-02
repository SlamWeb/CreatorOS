# Agent Benchmark 当前入口

唯一现行 Agent 题集为 [B01–B50](benchmark-v2/cases.json)，运行与判分契约见 [benchmark-v2/SPEC.md](benchmark-v2/SPEC.md)，显式付费运行命令见 [README](benchmark-v2/README.md)。

- 程序分与语义分独立记录，暂不设置综合权重。
- 各题按自身需求定义预期结果与评分项：简单 CRUD 检查最终数据，复杂多轮和生产题检查必要的因果链路，不凑统一数量的规则。
- 缺证据保留未评分。旧冻结批次沿用其原题目、原判分和原产物，不自动重判或重跑。
- Eval 页面只读展示当前 50 题标准与独立批次结果，不执行任务，不写入人工复核文件。

旧 E01–E12 与工作台 Eval 的 runner、grader、题目定义、UI 契约已退役。早期日期报告保留在 [archive](archive/README.md)，仅用于追溯，不作为当前要求；`data/agent-eval*` 下历史数据未移动、删除或重判。
