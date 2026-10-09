# CreatorOS Storage SPEC

## 选题移除记录（2026-10-09，完成）

- 只增加 TopicRemoval/`topic_removals`，保存 topic_id（可指向尚未入队的确定性候选）、series_id、当时账号归属、request_id、候选来源与状态（deleted/archived/dismissed）、时间。topic_id 不外键到 Topic，支持物理删除；series 外键保留归属。
- 有生产历史的选题不修改生命周期状态，不删除 Run/Revision/Attempt/图片；工作区通过移除标记过滤，历史与调研原件保持。迁移只在隔离库验证，正式库由日常启动的既有升级机制处理。
- `20261009_0009` 仅建表与索引；0008→0009、降级/再升级的隔离验证通过，原 Topic/Run 保留、foreign_key_check 无错误、ORM metadata drift=0。降级会丢弃移除记录，仅在一次性测试库运行，不作为用户恢复步骤。
- Repository 历史读取默认保留全部 Topic，运营计划显式 `include_removed=False`；可见调序复用原可见位置，隐藏行的位置不变，临时槽从全部行最大位置之后分配。栏目删除将移除记录计入历史并归档，保留墓碑/回执。

## 栏目移除回执（2026-10-05）

- `20261005_0008` 只扩展 `write_receipts` 的 operation CHECK，允许 `delete_series`；复用 `Series.is_active` 归档，无新增表/字段。只验证隔离 SQLite，正式库未改，日常启动入口按既有机制升级。
- 无选题/Run/调研历史的空栏目物理删除；有历史的栏目归档保留原行，目录默认不再显示。删除通过 revision 与 request_id，回执保留内部账号归属以支持账号作用域下的响应丢失重试。
- 隔离 `smoke_series_remove`、`smoke_operation_migration`、`smoke_series_composition_service`、`smoke_account_scope`、`smoke_topic_research`、`smoke_studio_api` 通过；禁止活跃生产/调研时删除，未取消任务或清理原产物。

## 人工发布迁移（2026-09-30）

- `20260930_0007` 增加 `manual_publications` 和 `publication_metrics`，分别保存已批准产物对应的人工发布凭证与追加式数据快照。隔离 Alembic/ORM 对比零 drift，正式库在备份后由本机启动入口增量升级；现有 Creator/Series/Topic/Run 行未由迁移重写。业务与验收见 `creatoros/publication/SPEC.md`。

## 本轮目标

- 用 SQLAlchemy 2.x 定义 `Creator`、`Series`、`Topic` 三个最小业务表。
- 本地默认使用 SQLite；数据库地址统一由 `DATABASE_URL` 提供，为后续 PostgreSQL 保留同一数据访问边界。
- 用 Alembic 保存第一份可重复执行的 schema migration，不让应用启动时偷偷 `create_all`。
- 用 Repository 验证创建账号、创建栏目、加入选题、整列调序和关闭连接后重新读取。

## 当前模型

- `Creator` 对应一个真实平台账号，保存展示名、平台、时区、可选每日总上限和启用状态。
- `Series` 对应独立栏目；可暂不归属账号（`creator_id` 可空，未分配同名由部分唯一索引去重）。Skill 绑定二选一：旧单 `skill_name`，或完整 `mind_skill_id + production_skill_id` 组合（`skill_binding_shape` 禁止半套）。`revision` 为配置乐观并发版本。选题确认、发布审批和自动补货策略保持不变。
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
- 2026-09-04 S4 复用既有 lease/heartbeat/trace 字段，输入 JSON 新增可缺省的栏目描述与受众；没有更改 schema。执行锁和未确认子进程记录由 runs 管理，数据库 Session 仍按请求/线程独立创建。
## S6 完成（2026-09-04）

- 新 Alembic 20260904_0004 增加 PendingOperation.scope_series_id 和 ORM version；旧记录 version=1、scope=None，revision/事件保留。
- smoke_operation_migration 从含旧计划和事件的 0003 升级，验证行保留、foreign_key_check、metadata 无 drift；content_storage/content_run_storage 通过。
- 本轮只迁移隔离测试库，没有升级正式运营库；日常启动仍由既有入口显式升级。

## P1 组合契约完成（2026-09-23）

- 新 Alembic `20260923_0005`：`creator_id`、`skill_name` 可空；新增 `mind_skill_id`、`production_skill_id`、`revision`（version_id_col）；`skill_binding_shape` 禁止半套绑定；`uq_series_unassigned_name` 部分唯一索引兜底未分配同名。
- `series_composition_migration_smoke=passed`：0004 旧库升级/降级/再升级数据逐字段不变，`compare_metadata` 零漂移；约束与索引行为逐项验证。详见 docs/studio/composition/SPEC.md 实施记录。
- 本轮只迁移隔离库与正式库副本彩排，原 `data/creatoros.db` 保持 0004；日常启动仍由既有入口显式升级。
