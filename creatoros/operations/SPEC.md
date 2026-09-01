# CreatorOS Operations SPEC

## 本轮目标

- 用严格 Pydantic `OperationPlan` 表示“向栏目批量加入选题”和“整列调整选题顺序”。
- 先生成只读 `OperationPreview`，再由宿主显式提交；LLM 生成计划不等于获得执行授权。
- 执行前重新校验数据库状态，并在一个事务中应用整份计划，避免半成功。

## 当前边界

- 不接 LLM 自然语言解析、不注册 Agent Tool、不修改 CLI。
- 不实现创建账号/栏目、删除、调度、生产、审批或发布。
- 当前针对本地单写者 SQLite；多进程并发控制留到 PostgreSQL 集成验证。

## 验收

- Pydantic 拒绝空计划、额外字段、重复 Topic ID 和非法操作结构。
- Preview 不修改数据库，并能展示每一步预计变化。
- 只有携带与当前数据库状态一致的 confirmation token 才能执行。
- 多操作在同一事务中提交；任何一步失败都不留下部分结果。

## 最近验证（2026-09-02）

- `operation_plan_smoke=passed preview=readonly stale=blocked rollback=passed`。
- Preview 后数据库队列发生变化时，旧 confirmation token 被拒绝，必须重新展示给用户确认。
- 使用真实 SQLite trigger 在第二个 Topic 写入时制造数据库错误，验证第一个 Topic 同时回滚。
- confirmation token 是状态一致性指纹，不是身份认证凭证；本轮尚未暴露 Agent 执行 Tool。
