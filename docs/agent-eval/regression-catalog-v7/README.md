# 常规与完整回归目录 v7

本版以独立来源 `e06-scope:E06` 替代旧 `expansion:E06`，题目总数保持 120。新版判分要求绑定同轮同对象的权限拒绝回执；无法证明回执归属或答案存在明确无依据断言时保留待复核。原始 Judge 与历史结果不回填。旧题仍禁止新运行，历史目录与批次保留。

本版 108 条常规题按隔离资格逐步准入；实际资格和执行结果以目录和看板为准。正式运行须选择同一来源和同一守卫、显式声明范围与模型调用费用。新版 E06 使用 `local-dispatch-v6`，其余已准入题使用各自冻结的旧策略；守卫不是操作系统或网络沙箱。

```powershell
python -m creatoros.evaluation.regression_catalog --summary
python -m creatoros.evaluation.regression_runner plan --suite regular-v7 --instances e06-scope:E06 --change-scope agent_shared
```

题源按摘要独立冻结；新增或修改题目必须重新审查，不能自动继承已有准入。扩展流程见 [题源快照说明](../source-snapshots-v1/README.md)。评分仍依赖模型且未完成人工校准，有限文本规则不能覆盖所有表述。
