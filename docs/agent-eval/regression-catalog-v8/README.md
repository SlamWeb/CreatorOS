# 常规与完整回归目录 v8

本版在 v7 基础上准入 `expansion:P01`、`expansion:P02`、`expansion:P03` 的素材只读场景。`local-dispatch-v7` 仅放行已核验的素材查询动作，导入和分析启动仍受阻断。准入表示隔离执行与副作用经受控验证；真实模型表现和评分可信度以各轮记录为准。

目录含 108 条常规及 120 条完整请求；题源按摘要冻结。每次正式运行只选同一来源和守卫，覆盖不完整须明确记录。守卫不是操作系统或网络沙箱；联网与生产题仍需单独验证。

```powershell
python -m creatoros.evaluation.regression_catalog --summary
python -m creatoros.evaluation.regression_runner plan --suite regular-v8 --change-scope agent_shared
```

历史目录、题源及成绩保留。新增或修改测试题时，先冻结来源并核验真实工具和评分证据，再发布新版本；不能自动继承原有准入。
