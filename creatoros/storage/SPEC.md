# CreatorOS Storage SPEC

## 栏目改名回执（2026-10-24，已验证）

- `20261024_0019`只扩展write_receipts.operation允许rename_series；改名与冻结回执同一事务，重放不重新改名，也不返回后来被改过的新状态。同键异请求/目标/账号归属拒绝。
- 0018升级、降级再升级的原行/外键/完整性及ORM零漂移验证通过；存在改名回执时禁止降级，避免丢记录。后端5/5真实隔离HTTP/SQLite验证包含生产中只改名和旧Topic/Run/Revision/Attempt/图片保护。
- 用户要求启动正式服务后先备份，0018/0019正常载入；未为验证改动正式栏目名称、Skill或产物。启动前遗留claimed执行记录确认PID/直属子进程均已退出后原样归档，旧运行按正常恢复标记中断、不重画。

## 素材文件夹（2026-10-24，隔离迁移通过）

- 迁移 `20261024_0018` 仅新增 `media_collections`：账号归属、可选自动收图 Topic、显式额外 Run、选片排除与手动顺序、CAS 版本和创建幂等请求。图片仍是原受管文件，不存 SQLite BLOB、不复制到新目录、不改旧 Run 输入或执行状态。
- 隔离 0017→0018→0017→0018 验证旧表逐值不变、外键/完整性正常，升级后 ORM 零漂移；集合操作与 100 图 ZIP/排序另验证原业务行和文件不变。完整测试与边界见 `docs/media-collections/SPEC.md`。
- 正式库未迁移；有生产任务时不通过热建表方式绕开维护流程。

## 选题编辑成功回执（2026-10-22，隔离验证）

- `20261022_0017` 只扩展 WriteReceipt.operation CHECK 允许 `edit_topic`，不新增 Topic 版本字段、不回写原业务行。编辑摘要从当前选题身份/正文/来源/状态/位置/更新时间生成，生产输入仍以旧 Run/Revision 冻结快照为准。
- 回执保存冻结账号、严格请求内容及写后正文，更新与回执同事务完成。精确重放不重写当前 Topic；请求键冲突、旧摘要与跨账号回放拒绝。仅承诺本机 Studio 共用写锁/短事务，不宣称跨进程或分布式 exactly-once。
- `tests.test_topic_edit_migration` 在含旧账号、栏目、选题、Run/Revision 及回执的 0016 临时库验证升级/降级/再升级全部原行逐值相同、ORM 零漂移、外键/完整性正常。有新编辑回执时拒绝降级，避免静默丢弃编辑证据。没有迁移正式数据库。

## 调研提交队列（2026-10-20，隔离验证）

- `20261020_0015` 仅新增 `topic_research_queue_items`，批次 ID 唯一、栏目外键、状态与提交/开始/结束时间；原批次 JSON、旧业务行不回填或改写。按 status/submitted_at/id FIFO 串行领取。
- queued 表示用户已授权且从未开始；researching 在调用模型前短事务落盘。重启保留 queued 并继续消费，将无结果的 researching 标 interrupted，不自动重复付费。JSON 已有终态时只补队列终态。
- 原账号、栏目、Topic、Run、Revision、Attempt 和 Skill 不因队列表迁移改变；迁移及调度验证只运行隔离库。
- 新库升级后 compare_metadata 零漂移；重启从已有SQLite读取授权，JSON完成但队列未收尾时保留JSON字节并补终态。无分布式领取承诺，沿用应用数据库绑定执行锁保证本机单宿主。

## Skill 校准（2026-10-20，隔离验证）

- `20261020_0014` 仅新增 `skill_calibrations`；保存请求幂等、栏目/账号/目标 Skill、基线摘要、草稿任务关联、用户标注证据和显式应用凭证。图片和冻结 Skill 仍为本地受管文件，不存入 SQLite BLOB 或账号固定上下文。
- 不回填、迁移或删除正式产物/共享 Skill；仅隔离库验证迁移和恢复，正式升级沿原启动路径，运行中的服务不重启。
- 新库迁移与 ORM compare_metadata 零漂移通过；有校准历史的空栏目沿既有 archive 保留语义，不再物理删除触发新增外键失败。发布意图与显式应用凭证和文件备份配合恢复，数据库不存图片或长模型事务。

## 任务参考选题（2026-10-18）

- 迁移 `20261018_0013` 仅为 topics 新增 nullable reference_id；旧Topic/Run/产物不回写。原图与完整要求保存在数据库同级独立任务参考组，Run/Revision冻结输入。跨栏目参考拒绝，原有Preview token和入队幂等兼容。
- 隔离新库升级/旧生产队列ORM回归通过，正式库未由本轮测试修改。完整设计及真实/受控验证分别见 `docs/topic-references/SPEC.md`。

## 显式生产队列（2026-10-17）

- 迁移 `20261017_0012` 仅新增 `production_queue_items`：Run/Revision 外键、唯一组合、expected_version、调度状态、提交/开始/结束时间及错误。索引按 status/submitted_at/id 领取 FIFO；不回填旧 Run，不改旧数据、图片、审批或发布记录。
- 表保存“明确授权的执行请求”而非主题库存或业务生命周期；pending 尚未创建 Attempt，不占 lease。running 在调用原 claim 前落盘，异常重启不能因为 Run 仍 queued 而误重投。
- 当前仅本机单消费者，由数据库绑定 OS guard 保证唯一执行者；数据库不承担模型调用、不锁住长事务。幂等在短事务内基于 Run/Revision 唯一约束和同实例串行写锁；未开始授权会随 SQLite 保存，消费不是纯内存队列。
- 隔离新库 Alembic 升级与 ORM compare_metadata 零漂移；故障注入见 `tests.test_production_queue`。迁移无自动生产副作用，正式库仍由既有启动路径升级；本次测试不写正式运营数据。

## 自动反馈采集（2026-10-16）

- 迁移 `20261016_0011` 新增 `publication_feedback_settings`（按账号保存自动更新开关、检查时间和安全错误摘要）与 `publication_feedback_states`（每发布单的尝试时间、下次尝试、连续失败次数及安全摘要）。默认未开启，不改已有发布单或指标。
- 指标继续追加 `publication_metrics`，未知值保持 NULL。网络调用不占数据库事务；采集前保存 30 分钟重试窗口，成功后以实际快照时间推进，失败指数退避最多 6 小时。进程中断后读取已持久化窗口，不补跑漏过的周期。
- 看板发布时间及 7 天筛选优先取最后一条 verified 证据的远端发布时间，缺失/格式不合法才回退旧发布凭证时间；只调整读取投影，不改写历史凭证。已验证时区转换、旧派发/新发布时间、远端过期/未来及坏字段回退。
- 采集范围与所有权取已核验发布单的冻结账号，栏目转移后不向原账号披露新归属的栏目名称。动态数据不进入 Agent 固定上下文。
- 验证记录由隔离迁移、时间推进和受控平台测试补充；正式 MCP 只读验收由主代理执行。
- `python -m unittest tests.test_publication_feedback` 16 项通过：ORM/迁移零漂移、默认关闭、30 分钟到期/重启、手动刷新幂等、最近 7 天窗口、失败退避与保留快照、身份核验、关闭时在途读取、发布优先/浏览器串行、120 秒读取超时取消、手动恢复清除退避、账号隔离、手动刷新遇忙的提示与重试清理，以及真实发布时间投影/时区/窗口/回退。`smoke_publication_requests` 既有确认/幂等/未知结果不重发回归通过。时间和异常使用隔离平台，不计为真实小红书验收。

## 发布确认与幂等（2026-10-16）

- 迁移 `20261016_0010` 增加 `publication_accounts`（本地 Creator 对稳定平台用户 ID）和 `publication_requests`（Run 唯一、账号+内容摘要唯一、冻结 payload、状态、确认时间、核对证据事件）。不改写已有人工发布、指标、账号或生产数据。
- 发布副作用之前使用 SQLite `BEGIN IMMEDIATE` 将 draft 原子改为 sending；外部调用发生在提交之后。unknown 不因超时/重启变回可重试。现有 `manual_publications` / `publication_metrics` 仍存最终凭证和追加指标。
- 业务契约及验证见 `creatoros/publication/SPEC.md`。迁移/故障注入测试仅隔离库，正式库由应用启动的显式升级路径处理。

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
