# CreatorOS Storage SPEC

## 本轮目标

- 用 SQLAlchemy 2.x 定义 `Creator`、`Series`、`Topic` 三个最小业务表。
- 本地默认使用 SQLite；数据库地址统一由 `DATABASE_URL` 提供，为后续 PostgreSQL 保留同一数据访问边界。
- 用 Alembic 保存第一份可重复执行的 schema migration，不让应用启动时偷偷 `create_all`。
- 用 Repository 验证创建账号、创建栏目、加入选题、整列调序和关闭连接后重新读取。

## 当前模型

- `Creator` 对应一个真实平台账号，保存展示名、平台、时区、可选每日总上限和启用状态。
- `Series` 对应 Creator 下的独立栏目，固定 Skill，并保存选题确认、发布审批和自动补货策略。
- `Topic` 对应 Series 的有序选题，区分 research/manual 来源并保存最小生命周期状态。

## 当前边界

- `OperationPlan` 已由独立 operations 模块消费 Repository；storage 保存 PendingOperation 当前状态和 append-only OperationEvent，但不负责自然语言解析或 UI。
- ContentRun 已由独立 `runs` 模块消费本模块的数据库边界；storage 不负责生产状态机或审批规则。
- 不实现 UI 或 Tool；本轮先稳定可被这些上层复用的业务状态。
- 不宣称 PostgreSQL 已支持；只有实际运行同一 migration 和 Repository 集成测试后才升级该结论。

## 验收

- Alembic 能把空 SQLite 文件升级到当前 revision，并生成三张业务表和版本表。
- Repository 能在同一事务中安全调序，拒绝漏项、重复项和跨 Series Topic。
- 关闭第一个 Database 实例后，用新实例仍能读取相同 Creator、Series 和 Topic 顺序。

## 最近验证（2026-09-02）

- `content_storage_smoke=passed creators=1 series=2 topics=4 restart=passed`。
- 第一版 Alembic revision 为 `20260902_0001`；第二版 `20260902_0002` 增加 PendingOperation 与 OperationEvent。
- 调序使用同一事务内的两阶段正整数位置更新，既满足 `position > 0`，也避免 `(series_id, position)` 唯一键碰撞。
- 本轮只验证 SQLite；`DATABASE_URL` 是未来 PostgreSQL 接线边界，不代表已经完成跨数据库集成验证。
- `pending_operation_storage_smoke=passed restart=passed events=1`：计划 JSON、Preview、token、usage 和审计事件均可跨重启读取。
- 第三版 Alembic revision `20260902_0003` 增加 ContentRun、Revision、Attempt 与 append-only Event；`content_run_storage_smoke=passed revision=20260902_0003 restart=passed`。
