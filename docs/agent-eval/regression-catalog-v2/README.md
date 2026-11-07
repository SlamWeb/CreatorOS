# 常规与完整回归目录 v2

本目录承接 [v1 目录](../regression-catalog-v1/README.md)，沿用相同的源题、成员与资源分类；11 个已准入的原题从 `local-dispatch-v1` 切换为 `local-dispatch-v2`。`regular-v2` 包含 108 个常规请求，其中 11 个已准入、97 个待准入；`full-v2` 包含 120 个请求实例，其中 11 个已准入、109 个待准入。准入不是模型已执行，也不是人工评分校准。其余内容及限制见 v1 说明。

v2 是新的执行条件，不与 v1 成绩直接比较。目录与套件具有各自版本和内容摘要，正式运行绑定冻结快照；旧目录和旧记录不改写。看板默认展示 v2；历史轮次可由既有轮次入口读取，历史套件记录仍可通过 `GET /api/eval/v1/suites/regular-v1/runs` 和 `GET /api/eval/v1/suites/full-v1/runs` 读取。比较器以 `execution_mode` 区分执行条件。

看板也列出可校验的 v1 历史目录。v1 目录目前引用仓库共享题源，而非独立冻结的题源副本；若共享题源变化导致摘要不符，历史目录会暂不可用并在页面提示，但不阻断 v2 目录。历史轮次、运行记录与其已保存的冻结证据仍通过原入口读取，不因目录加载失败而重写。

只读查看：

```powershell
python -m creatoros.evaluation.regression_catalog --summary
python -m creatoros.evaluation.regression_catalog --manifest docs/agent-eval/regression-catalog-v1/catalog.json --summary
python -m creatoros.evaluation.regression_runner plan --suite regular-v2 --instances base:B05,base:B06 --change-scope tool
```

计划不运行模型。真实运行需显式授权、预算与部分覆盖确认，参考 [v1 运行说明](../regression-catalog-v1/README.md)，并将套件 ID 替换为 `regular-v2`。守卫不是操作系统或网络沙箱；模型费用、外部副作用及适配器准入范围仍须分别核实。
