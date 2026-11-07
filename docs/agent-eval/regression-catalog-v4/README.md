# 常规与完整回归目录 v4

`regular-v4` 覆盖 108 条常规请求定义，`full-v4` 覆盖 120 条定义。具体已准入数量和运行结果以看板及计划命令为准；未准入题不会被全量运行静默跳过。

本版保留原题的受限执行能力，并将自然表达变体 `natural:B06`、`natural:B07` 纳入受控准入。准入依据是隔离环境中的调用和副作用验证，不代表真实模型已经跑通或 Judge 已完成人工校准。自然题与原题各自有独立身份、题源和记录，不能仅凭题号或 parent 关系混为一题。

每个执行批次只能选同一个来源命名空间，例如一次选择 `base:*`，或一次选择 `natural:*`。自然题当前只开放 B06/B07。v4 的 base 和 natural 授权分别绑定代码中独立发布的固定题源摘要与允许列表，改写目录或题源摘要不能自行放宽授权；新题或变体须审查并明确发布新的执行授权。

```powershell
python -m creatoros.evaluation.regression_catalog --summary
python -m creatoros.evaluation.regression_runner plan --suite regular-v4 --instances natural:B06,natural:B07 --change-scope agent_shared
```

计划是只读检查；正式部分运行需要声明不完整范围及允许模型费用。受限守卫不是操作系统或网络沙箱，真实生产和联网题需单独核实副作用及恢复方式。题源扩展和冻结参见 [快照说明](../source-snapshots-v1/README.md)；新增题默认不具备执行准入。

v1/v2/v3 的历史目录和各自运行记录保留在看板，执行条件不同的版本不会被默认为可直接比较；原批次证据和分数不回填。
