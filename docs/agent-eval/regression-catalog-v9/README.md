# 常规与完整回归目录 v9

本版在 v8 上增加 `base:B29`、`base:B36`、`base:B45` 的隔离图片读取。`local-dispatch-v8` 在隔离世界绑定并核验账号、会话、数据库、素材和产物路径；图片读取会在当前隔离会话创建附件副本。准入表示受控隔离与证据链已验证，视觉理解仍依赖真实模型与 Judge，不等于已经通过。

目录包含 108 条常规和 120 条完整请求；不同来源或守卫须分别运行。联网与生产场景继续待审，守卫不是操作系统或网络沙箱。历史目录与冻结证据保留，新增题目须冻结来源并逐题验证。

```powershell
python -m creatoros.evaluation.regression_catalog --summary
python -m creatoros.evaluation.regression_runner plan --suite regular-v9 --change-scope agent_shared
```
