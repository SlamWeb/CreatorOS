# CreatorOS Operations SPEC

## 本轮目标

- 用严格 Pydantic `OperationPlan` 表示“向栏目批量加入选题”和“整列调整选题顺序”。
- 先生成只读 `OperationPreview`，再由宿主显式提交；LLM 生成计划不等于获得执行授权。
- 执行前重新校验数据库状态，并在一个事务中应用整份计划，避免半成功。

## 下一切片：自然语言解析

- 用 Provider 的 Structured Output 把用户自然语言翻译为现有 `OperationPlan`，并注入当前 Series/Topic 目录帮助模型解析指代。
- 解析结果仍必须经过 Pydantic 和现有 Preview；模型不直接执行写操作。
- 使用 DeepSeek Responses API 的 `json_schema` 做一次真实低成本验证。

## 当前边界

- 本轮接入独立自然语言解析器，但不注册 Agent Tool、不修改 CLI，也不让模型获得执行权限。
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

## 自然语言解析验证（2026-09-02）

- 新增独立 `StructuredModelProvider`，DeepSeek 实现只在结构化解析时使用 Responses API `json_schema`；原 Agent Loop 仍使用既有 Chat Completions。
- Parser 把当前 Series、Skill 和有序 Topic 目录作为只读状态传入模型，再用 Pydantic 验证模型响应。
- 解析决策分为 `ready`、`needs_clarification` 和 `unsupported`，避免 Schema 在超范围请求下强迫模型编造可执行计划。
- `operation_parser_smoke=passed catalog=passed validation=passed`。
- 真实 DeepSeek 验证：新增两个选题并调序得到 2 个操作，Preview 与执行通过；删除栏目请求返回 `unsupported`，未产生计划。
- 主成功请求 usage 为 input 1,040 / output 94 tokens；测试只使用临时 SQLite，未修改正式栏目。

## 本轮目标（PendingOperation）

- 把解析决策、OperationPlan、Preview、confirmation token 和当前审批状态保存到数据库，重启后可以继续确认。
- 用 append-only `OperationEvent` 记录 proposed、edited、confirmed、succeeded、failed、stale 和 cancelled，不只保留最终状态。
- 支持 `propose / edit / confirm / cancel`；确认时让业务写入和 succeeded 状态在同一数据库事务提交。
- 将主菜单“今日运营”接到宿主控制的确认界面；普通 Agent 不能自行调用 confirm。

## 本轮边界

- PendingOperation 只承载短时运营指令审批，不提前塞入 ContentRun 的生产步骤、Codex thread 或产物字段。
- 暂不新增创建栏目、归档或永久删除 Operation；现有 add/reorder 完成审批闭环后再扩展同一 union。

## PendingOperation 存储验证（2026-09-02）

- Alembic `20260902_0002` 新增 `pending_operations` 与 `operation_events`，没有修改 Creator/Series/Topic 语义。
- 当前状态与 append-only 事件分开存储：前者服务快速恢复，后者服务审计、Trace 和后续 Eval。
- `pending_operation_storage_smoke=passed restart=passed events=1`，并通过 Alembic metadata drift 检查。
