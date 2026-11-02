# Benchmark 实现

唯一当前契约见 [评测SPEC](../../../docs/agent-eval/benchmark-v2/SPEC.md)，逐题标准见同目录CASE_STANDARDS.md。

- scoring.py执行逐题程序/语义二分类规则，分数分开、关键门禁独立、缺测留null；report.py按题分别汇总，不设权重。
- grader.py保存业务结果、关键链与通用诊断；server.py在补齐真实生产/续接/交付证据后计分，Judge收到原始对话、工具回执、SQL与可用媒体证据。
- store.py和web/eval_routes.py仅只读当前50题与已保存批次，不读取旧12/20题规则、不重算历史分数。
- run.py显式冻结与付费执行；io.py独立写入脱敏证据。无旧Eval运行时依赖。
- 旧真实批次结果不可当成v3成绩。本轮验证记录与限制见docs中的VERIFICATION-20261102.md。
