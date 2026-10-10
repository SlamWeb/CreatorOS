# CreatorOS Operations SPEC

## 入队回执收敛（2026-10-11，完成）

- 复用 WriteReceipt/PendingOperation，不增状态表或迁移。回放同时核验操作类型、栏目和完整规范化计划，返回第一次真正落库的 Topic ID。
- HTTP 手动入队临时分配的 Topic ID 不是用户意图，使用显式 `generated_topic_ids=True` 比较；研究候选/内部调用默认 False，确定性候选身份必须一致。回执记录该模式，混用同 key 拒绝。
- 回执保存冻结 creator_id；账号 Guard 同时核验当前栏目归属与首次写入归属。栏目转移、其他栏目碰撞和身份不足的旧账号回执保守拒绝。旧无模式回执可读，随机 ID 写重放不猜测；内部原完整计划重放兼容。
- 新只读 queue_receipt 从原成功计划读取真实 ID，不执行写入。未知结果与事务失败的自动重提由 Agent 宿主阻止；不会以没有回执断言未写入。
- 隔离真实 SQLite/HTTP：重复/异文/跨栏目/跨账号、响应丢失、第二条触发事务回滚、研究候选身份变化、旧回执、转移、重启验证通过。故障注入是确定性回归，不算真实模型任务成绩；细节见 tools SPEC 与契约报告。

## 已移除选题的旧计划门禁 · 2026-10-09（完成）

- Preview 与确认事务检查 TopicRemoval，旧候选计划不能在移除后重新入队；Repository add 同样拒绝已移除 ID。运营目录、Preview 与执行回执只列可见 Topic，历史/调研去重读取仍保留原行。
- 隔离真实 HTTP 旧 Preview 竞争回归及原 operation_plan/parser/pending_operation_service 通过；移除后目录调序只要求可见完整集合，隐藏位置不变。未增加模型自动确认或自动重试。

## 长期待选候选的当前 Preview · 2026-10-09

- 旧调研候选按当前栏目准备 `expected_series`；确认时原配置/版本/revision/confirmation token 守卫保留。配置冲突提示重新预览后再确认，不再要求付费重新调研。长期有效边界与隔离 HTTP 验收见 `docs/agent-studio/topic-research/SPEC.md`。

## 调研候选计划（2026-09-09）

- AddTopicsOperation 可携带 expected_series，在 Preview 与事务内确认时校验定位/受众/Skill；旧计划省略该字段时保持原 confirmation token 算法兼容。
- 候选修改先回候选列表/选择 Tool，不允许通用 Parser 丢弃来源和版本约束。人工选题原有编辑不变；复用原事务与确认服务。
- 新旧 smoke 均通过；完整证据见 docs/agent-studio/topic-research/SPEC.md。

## 本轮目标

- 用严格 Pydantic `OperationPlan` 表示“向栏目批量加入选题”和“整列调整选题顺序”。
- 先生成只读 `OperationPreview`，再由宿主显式提交；LLM 生成计划不等于获得执行授权。
- 执行前重新校验数据库状态，并在一个事务中应用整份计划，避免半成功。

## 当前边界

- “今日运营”是宿主控制的审批入口，不注册为普通 Agent Tool，也不让模型获得确认权限。
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

## PendingOperation 工作流验证（2026-09-02）

- `PendingOperationService` 支持 propose、persist_edit、confirm、cancel 和重启后列出 actionable plans。
- confirm 将业务写入、PendingOperation succeeded 和成功事件放在同一数据库事务；重复确认是幂等读取，不重复新增 Topic。
- Preview 后外部状态变化会把请求标记为 stale；数据库执行错误会回滚业务写入，并单独保存 confirmed/failed 审计事件。
- `pending_operation_service_smoke=passed restart=confirm edit=passed rollback=passed`。

## PendingOperation CLI 验证（2026-09-02）

- 主菜单“今日运营”已接入 `PendingOperationCLI`：没有活动计划时接受自然语言；存在计划时只接受确认、取消、返回或自由形式修改要求。
- 重启进入后自动恢复并重新展示最新待处理 Preview；确认后才执行，普通 Agent 对话仍不能越过宿主审批。
- `pending_operation_cli_smoke=passed resume=confirm`，菜单入口与全包编译回归通过。
- 真实 DeepSeek 端到端验证通过：提议新增两个选题时数据库保持只读，关闭并重开数据库后继续修改为指定顺序，确认后一次性落库；`revision=2`。
## S6 完成（2026-09-04）

- 复用 Parser/Service/Executor；持久化 scope，解析前检查栏目/父账号 active，模型越界降为澄清，非法目标或漏项调序拒绝。edit 读取当前完整 plan，修改同一 ID。
- 独立 ORM version 与草稿 revision；CLI/Web 的 edit/confirm/cancel 均提交所见版本。CONFIRMED 记录原确认请求凭证，只允许同一成功请求重放；并发冲突不能覆盖新状态。
- Preview 保存名称和 Topic 标题/brief；proposed/edited 逐次保存 usage。它不是完整 Agent Trajectory。
- 原关联 smoke 与新 HTTP 故障/竞争测试通过；真实 4 次 DeepSeek 的新增调序、同计划编辑、歧义及越界请求通过。详细矩阵见 docs/studio/s6/SPEC.md。
