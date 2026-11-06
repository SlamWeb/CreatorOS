# Agent Benchmark 当前入口

现行 Agent 评测包括 [受控回归 B01–B50](benchmark-v2/cases.json)、[自然表达题集 v2](natural-v2/README.md) 和 [50 个扩展开发请求](expansion-50-v1/README.md)。自然题集有20道合成改写，其余30条仅作宿主兼容占位，不得执行；50+20+50 是请求实例定义，不代表独立能力或同版本全量成绩。各题集和运行独立冻结，不混算。运行与判分契约见 [benchmark-v2/SPEC.md](benchmark-v2/SPEC.md)。

- 程序分与语义分独立记录，暂不设置综合权重。
- 各题按自身需求定义预期结果与评分项：简单 CRUD 检查最终数据，复杂多轮和生产题检查必要的因果链路，不凑统一数量的规则。
- 缺证据保留未评分。旧冻结批次沿用其原题目、原判分和原产物，不自动重判或重跑。
- Eval 页面只读展示正式评测登记及原 Benchmark 历史批次，不执行任务，不写入人工复核文件；新登记和比较契约见 [评测登记 SPEC](../../creatoros/evaluation/SPEC.md)。[工具调用专项](tool-calling-v1/README.md) 复用已有题，当前仅有限题目支持隔离正式运行。

旧 E01–E12 与工作台 Eval 的 runner、grader、题目定义、UI 契约已退役。早期日期报告保留在 [archive](archive/README.md)，仅用于追溯，不作为当前要求；`data/agent-eval*` 下历史数据未移动、删除或重判。
