# 常规与完整回归目录 v2

本目录承接 [v1 目录](../regression-catalog-v1/README.md)，沿用相同的源题、成员与资源分类；11 个已准入的原题从 `local-dispatch-v1` 切换为 `local-dispatch-v2`。`regular-v2` 包含 108 个常规请求，其中 11 个已准入、97 个待准入；`full-v2` 包含 120 个请求实例，其中 11 个已准入、109 个待准入。准入不是模型已执行，也不是人工评分校准。其余内容及限制见 v1 说明。

v2 是新的执行条件，不与 v1 成绩直接比较。目录与套件具有各自版本和内容摘要，正式运行绑定冻结快照；旧目录和旧记录不改写。看板默认展示 v2；历史轮次可由既有轮次入口读取，历史套件记录仍可通过 `GET /api/eval/v1/suites/regular-v1/runs` 和 `GET /api/eval/v1/suites/full-v1/runs` 读取。比较器以 `execution_mode` 区分执行条件。

v1/v2 的题集和工具专项授权题源已独立存入 [内容寻址快照](../source-snapshots-v1/README.md)。读取时按原目录中的摘要校验；共享题源更新不改变历史目录和运行记录。若快照缺失或损坏，历史目录提示不可用，不能静默使用不匹配的内容。

只读查看：

```powershell
python -m creatoros.evaluation.regression_catalog --summary
python -m creatoros.evaluation.regression_catalog --manifest docs/agent-eval/regression-catalog-v1/catalog.json --summary
python -m creatoros.evaluation.regression_runner plan --suite regular-v2 --instances base:B05,base:B06 --change-scope tool
```

计划不运行模型。真实运行需显式授权、预算与部分覆盖确认，参考 [v1 运行说明](../regression-catalog-v1/README.md)，并将套件 ID 替换为 `regular-v2`。守卫不是操作系统或网络沙箱；模型费用、外部副作用及适配器准入范围仍须分别核实。
