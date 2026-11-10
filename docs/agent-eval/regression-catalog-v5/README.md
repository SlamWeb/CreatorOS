# 常规与完整回归目录 v5

`regular-v5` 覆盖 108 条常规请求定义，`full-v5` 覆盖 120 条定义。已准入数量和实际运行结果以看板与计划命令为准；默认全量仍会因未准入项停止，不会静默跳过。

本版把扩充题 `expansion:E06`、`E07`、`Y04`、`C02`、`E05` 列为受控准入候选，并为每题在独立世界中验证本地 HTTP、受控 Agent loop、跨账号拒绝、生产/调研误调用拦截及评分正反例。C02 和 E05 的误删除/误更新还验证了业务状态和禁用动作两道评分门禁。受控测试不代表真实模型或 Judge 已通过，具体题目仍以目录为准。

每个批次只选 `base`、`natural`、`expansion` 中一种来源。各来源均绑定代码中独立发布的固定题源摘要和允许列表；仅更改目录摘要或复制快照不能自行授权新题。守卫不是操作系统或网络沙箱，真实生产和联网题需单独核实副作用及恢复方式。

```powershell
python -m creatoros.evaluation.regression_catalog --summary
python -m creatoros.evaluation.regression_runner plan --suite regular-v5 --instances expansion:E06,expansion:E07 --change-scope agent_shared
```

计划是只读检查，正式部分运行需要声明不完整范围并允许模型费用。新题默认未准入；扩展题源、封存和版本维护参见 [快照说明](../source-snapshots-v1/README.md)。v1 至 v4 的历史目录与运行记录独立保留，不同执行条件不会默认作为可直接比较的成绩。
