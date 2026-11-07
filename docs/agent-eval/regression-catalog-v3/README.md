# 常规与完整回归目录 v3

`regular-v3` 定义 108 个常规请求；`full-v3` 定义 120 个请求，包括 12 个需要真实生产或联网调研的重资源请求。当前可执行题数以目录和看板显示为准；未准入题不会被全量运行静默跳过。已准入表示通过隔离与副作用的受控验证，不等于真实模型已测过、评审已人工校准或保证任务通过。

本版使用 `local-dispatch-v3`，守卫在隔离测试进程内限制工具分派和后台服务，保留真实工具 schema；它不是操作系统或网络沙箱。扩大的允许题目按 v3 固定授权范围验证，v1/v2 守卫、目录及历史运行保持原执行条件；不同执行条件不可直接当作同一套成绩对比。v1/v2 的历史版本仍可在回归集页面查看，各自运行记录独立保存。

只读查看实际成员与准入情况：

```powershell
python -m creatoros.evaluation.regression_catalog --summary
python -m creatoros.evaluation.regression_runner plan --suite regular-v3 --change-scope tool
```

模型调用会产生费用，部分运行需要明确选题并标注范围不完整；执行参数和保护措施见 [v1 运行说明](../regression-catalog-v1/README.md)，将其中套件 ID 替换为 `regular-v3`。需要真实生产或联网的题目必须逐题核实隔离、授权、副作用及恢复方式；不要把资源分类视作执行准入。

本目录使用 `sources_frozen: true` 并通过 `adapter_source_sha256` 绑定基础题源。[内容寻址快照说明](../source-snapshots-v1/README.md) 包含新版本扩展和首次冻结命令；缺失或损坏时拒绝读取，不以共享题源替代历史。扩展题目应建立新目录版本，冻结题源并审查权限后再按题开放执行，历史批次和分数不回填。
