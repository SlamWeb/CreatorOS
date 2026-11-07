# 常规与完整回归目录 v6

`regular-v6` 包含 108 个常规请求定义，`full-v6` 包含 120 个请求定义。沿用 v5 的本地隔离执行策略；旧题 `expansion:E06` 的评分口径与账号范围权限契约不一致，已转为待复核。其余题目的执行资格与 v5 一致。准入数量和实际结果以看板与计划命令为准。

所有版本均禁止新建旧源 E06 的运行；v1 至 v5 的历史目录、运行记录与冻结证据保留，读取不改写原分数，恢复仍需满足原批次的完整性与源码校验。修正版 E06 的评分证据尚未完成正式 runner 接入及验证，不能作为已准入题运行。

```powershell
python -m creatoros.evaluation.regression_catalog --summary
python -m creatoros.evaluation.regression_runner plan --suite regular-v6 --instances expansion:E07 --change-scope agent_shared
```

计划是只读操作；部分运行必须显式声明范围不完整和模型费用。每批只能使用一种来源；守卫不是操作系统或网络沙箱。扩展与冻结规则见 [题源快照说明](../source-snapshots-v1/README.md)，旧版本使用条件见 [v5 说明](../regression-catalog-v5/README.md)。
