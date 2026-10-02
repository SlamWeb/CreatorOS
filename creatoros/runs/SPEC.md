# ContentRun SPEC

## Native 单 thread 协议接线（2026-10-02）

- 新 `ContentRunService` 默认将 `production_protocol=native-v1` 固化进 Run/Revision 输入；调用方可显式传 `legacy`。未含协议字段的历史 JSON 仍解析为 `legacy`，返工沿用原输入快照。
- 原生双 Skill 组合标记为 `native-carousel-v1`，要求 mind/production 角色和有效 Skill 文件，但不再按 Skill 名称、GitHub 来源或角色图资源白名单筛选；旧 `pagespec-xiaobai-carousel-v1` 门禁保持不变。本地 Skill 的 `github_url`、`commit` 可为空。
- 服务仅对 native-v1 producer 传协议标记和当前 Revision 明确冻结的 `previous_pages`，不给它注入 Run 上记录的旧 thread 或隐式历史内容；同 Revision Attempt 的交付 checkpoint 恢复由原生产模块负责。旧协议 producer 参数保持原状。
- Native 新 Revision 仅从同一 Run 前一 Revision 已验收的 native checkpoint 读取 delivery 文案和每页 content/image_prompt，不复用图片路径；同 Revision 重试读取首个 Attempt 冻结的 `previous_pages`。前一版本摘要变化或缺少有效交付时拒绝空上下文返工。
- 服务在产物验收与批准时都从 Revision 的不可变输入读取协议。`native_delivery_failed` 作为生产阶段可显式技术恢复的错误类型记录。
- 验收方式：隔离 SQLite、本地任意名称且无 GitHub/character.png 的双 Skill、受控 Producer；不调用真实生产 API、不写正式数据库。
- 最近验证：`smoke_native_run_wiring` 通过，覆盖任意本地双 Skill 冻结、legacy 白名单拒绝、native 前稿提取及 retry 输入冻结、历史输入默认值、native 参数隔离与 `native_delivery_failed` 可恢复；`smoke_native_artifact_web` 通过。Legacy 回归 `smoke_content_run_service`、`smoke_pair_production`、`smoke_producer_skills`、`smoke_series_guards`、`smoke_receipt_recovery`、`smoke_studio_executor`、`smoke_studio_partial_cards`、`smoke_studio_production_progress`、`smoke_studio_run_api`、`smoke_visual_checkpoint` 均通过；默认 native 创建回归 `smoke_studio_api`、`smoke_topic_crud` 通过。以上均使用隔离目录和本地受控依赖，没有真实生产 API 或正式数据库写入；`py_compile` 与 `git diff --check` 通过。

## 单视觉逐页保存与显式恢复（2026-10-01）

- 双 Skill 单视觉会话按页交付，技术失败后通过既有 execute 新建同 Revision Attempt；仅复用输入及冻结 Skill digest、原内容、视觉计划、图像 SHA256 全部一致的前 Attempt checkpoint。人工返工新 Revision 不复用旧图。本节取代下方历史的“技术重试整套重新生产”描述。
- 有效 checkpoint 后的交付失败为 visual_delivery_failed，可由用户点恢复；中断沿用原状态机。无 checkpoint 的 schema 错误仍不盲重试，额度错误不自动恢复。
- 失败也保存已观察的内容/视觉 usage，避免成本只在成功时可见；恢复的新 Attempt usage 与历史 Attempt 分开保存，不把历史调用记为免费。
- 内容、计划、逐页 Prompt/参考资产、checkpoint 与最终证据一致才验收。没有增加另一套状态机或数据库迁移。
- 隔离 SQLite/transport 注入晚页失败后，仅缺页执行通过；真实 SDK 同会话多 turn 文本探针通过。生产整组图片质量验收尚待下一次用户生产。

## 已完成图片的显式回执恢复（2026-10-01）

- 恢复不是重新调用模型或恢复旧 thread 推理：人工核对原 ledger 最终 JSON、原 storyboard、冻结 Skill 和实际图片后，通过既有 guard / request_revision / claim / execute_claimed 导入新 Revision。旧失败 Attempt 保持原样，仍需人工批准。
- 专用维护入口 `python -m creatoros.integrations.receipt_recovery --run-id ... --ledger ...` 默认 dryrun；`--apply` 必须提供所见 version 和接受的 receipt SHA256。只支持 invalid_production_receipt 的失败双 Skill Run，拒绝改动教学/屏幕文字、缺页/坏图/越界路径/错误资源。
- 正式数据库主题 Run `5d51415b-ce24-4a98-83a1-09e61bee7ab4` 已恢复 Revision 2 / Attempt 1，11 张原图通过验收，状态 awaiting_approval（version 9）。Revision 1 保持 failed，无模型/生图调用、批准或发布；导入 Attempt usage=0 仅表示导入成本，不是原生成成本。
- 正式恢复前已备份 `tmp/creatoros-before-receipt-recovery-20261001.db`，原始回执与来源保存在新 Attempt 的 receipt_recovery.json；原冻结 Skill / storyboard 和生成目录不覆盖。
- 隔离 `smoke_receipt_recovery` 通过；正式 HTTP 11 个图片 URL 均 200。其他 Run 未变化，导入图片逐张与生成源 SHA256 一致，最终证据保留原 11 页内容原文。
- 收尾复核补严格 resolve 后目录边界及 ledger 符号链接拒绝；恢复 smoke 重跑通过，原正式产物不再改写。
- 新的 Claude Code Run `76fbc0f9-2952-4a84-aa10-219fcf23320d` 已恢复：7 页、回执 SHA256 `1d2c5bbfd4798769d048e02b3d9616ad0ad116e1d71d3d53eed54728634891d7`，预检通过后用户授权停止服务，导入 Revision 2 / Attempt 1，状态 awaiting_approval。此前正式库备份在 tmp/pre-receipt-recovery-20261002/，原失败记录保留。新版无 PageSpec 的视觉回执只接受精确的空白标题/发布字段、匹配页序与图片路径、无 warnings；资源前缀仅按已存在的冻结 Skill 文件校验后归一化。产物展示文案从原 Storyboard 生成草稿，原回复与修复记录保留，不能声称为原模型文案。真实网页显示 7 张图片，翻页与内容/Prompt 展开已操作验证，未调用生图、批准或发布。

## SDK 生产全新 session（2026-10-01，完成）

- SDK 每个 Attempt 新建会话，双 Skill 是 Mind / Visual 两个独立会话；旧 CLI resume 行为保持兼容。下方旧 SDK 同 thread 恢复约定由本节替代。
- 当前 Run 的 `producer_thread_id` 保存最新 Attempt 的视觉会话；每个 Attempt 保留自己的 ID，同 Attempt 不允许意外改绑。内容会话 ID 在该 Attempt 的阶段 Trace 中记录。无数据库迁移。
- 返工仅显式带入本 Run 最新已保存的内容 pages；不带旧生图 Prompt、旧会话或其他任务记忆。技术重试重新生产，不把旧图片自动复用。
- `storyboard.json` 与 `storyboard.md` 先于视觉阶段落盘；视觉失败时保留内容，成功时检查逐页内容一致并将两个文件纳入审批摘要。缺少 storyboard 的历史产物仍兼容，不补写原件。
- SDK 阶段隔离、技术重试/新 Revision 新 ID、同 Attempt 冲突拒绝、内容文件篡改/缺失拒绝，与既有 Run/Executor/API/Artifacts 回归通过。真实无生图探针结果见 integrations SPEC；本轮不重画消息队列图片、不批准或发布。

## 本地 Skill 执行快照（2026-10-01，完成）

- 排队时记录本地绑定；首次执行冻结当前 Skill。恢复/返工继续使用该 Run 的文件快照，本地后续修改影响新的 Run。
- 双 Skill 与已安装单 Skill 都复用同一 Run/Attempt 机制。历史记录按原 digest 复用旧冻结文件或导入原件，不静默切换到已编辑的工作副本。
- 无数据库迁移；新增 JSON 可选字段兼容旧输入。隔离测试覆盖 queued 后编辑、生产中编辑、恢复及新 Run 读取新内容。
- `smoke_producer_skills`、`smoke_pair_production`、`smoke_series_guards` 与旧 Run/Executor/Artifact/API 回归通过。双 Skill 历史恢复使用原 Attempt 文件；原图片、原 `production_evidence.json` 与导入原件未修改。验证具体清单见 `creatoros/integrations/SPEC.md`。

## P4 接线（2026-09-26）

- 可选 composition 输入快照承载双 Skill，旧快照仍可读取；复用已有 Run/Revision/Attempt/显式恢复，不另建执行器。
- pair 产物证据和 Skill 资源纳入验收/审批摘要；详细边界与验证见 `docs/studio/composition/SPEC.md` P4。
- 隔离 `smoke_pair_production` 通过：HTTP 幂等创建、配置冻结、中断后续接同 thread、新 Attempt、页/Prompt/图对应、缺文件/篡改/越界路径拒绝审批、坏证据 API 不产生 500。故障注入使用受控 Producer，不冒充真实生图。
- 旧链路 `smoke_content_run_service`、`smoke_series_guards`、`smoke_studio_artifacts`、`smoke_studio_executor` 回归通过。没有新建恢复状态机或新增数据库迁移；不宣称逐个教学/生图阶段都有独立 checkpoint。

## 本轮目标

- 用 `ContentRun` 表示一篇内容从排队、生产、确定性验收到人工批准的完整生命周期。
- 用不可变 `ContentRevision` 保存每次人工返工版本，用 `ContentAttempt` 区分同一版本的技术重试。
- 用 append-only `ContentRunEvent` 保存状态迁移，为恢复、审计和后续 Agent Eval 提供完整 Trajectory。
- 将数据库作为工作流状态真相，将 `SocialContentPack` 目录作为产物真相；两者通过路径和内容摘要关联。

## 状态与恢复规则

- 主路径：`queued -> producing -> validating -> awaiting_approval -> approved`。
- 旁路终态/暂停态：`interrupted`、`failed`、`cancelled`。
- 进程在 `producing` 时退出，重启后将该运行标记为 `interrupted`；只有用户显式恢复才继续产生费用。
- 人工返工创建新 Revision；网络超时等技术问题创建同一 Revision 下的新 Attempt。
- 批准必须同时提交 Revision ID 与当前产物 digest，防止用户看过后文件被替换。

## v1 边界

- CLI 仍可前台执行；Studio 使用一个受管理的本地单写执行器，把长生产移出 HTTP 请求。暂不实现多 Worker、自动调度、Side Chat、MCP Server 或平台发布。
- 只做确定性验收：Manifest/Pydantic、图片存在性、安全路径、顺序、可读尺寸和基础字段。
- 不使用 LLM-as-Judge；内容质量评审与发布策略留到真实产物稳定后。
- `origin_session_id`、`context_snapshot_ref`、`producer_thread_id` 与 lease 字段都保留；lease 只表达执行所有权和新鲜度，不伪造图片进度。

## 验收

- Alembic 从空库升级后四张 ContentRun 表与 ORM metadata 完全一致。
- 幂等键阻止同一 Topic 重复启动，状态迁移拒绝非法跳转。
- 中断后可以显式恢复；Revision 与 Attempt 编号分别递增且旧记录保留。
- 修改 Manifest 或任一有序图片字节后，旧 digest 不能再批准。

## 最近验证（2026-09-02）

- `content_run_storage_smoke=passed revision=20260902_0003 restart=passed`：四张表可迁移、无 metadata drift，Run/Revision/Attempt/Event 可跨重启读取。
- `content_run_service_smoke=passed interrupt=resume revision=2 digest_guard=passed`：首次中断后沿用已保存 Codex thread 创建 Attempt 2，人工返工创建 Revision 2，旧版本仍保留。
- 批准前重新计算 canonical Manifest 与有序图片字节的 SHA-256；图片被修改后旧 digest 被拒绝。
- 已对现存 6 张真实 Codex 产物执行确定性验收：Manifest、图片解码和尺寸读取通过，总图片约 17.1 MB；本轮未再次调用昂贵的真实生图。
- `ContentRun.version` 使用 SQLAlchemy version counter；审批、返工和取消同时要求调用者提交所见版本，防止旧页面覆盖新状态。
- 主菜单“运行记录”已接入前台 ContentRun 控制台：可从 Topic 队列创建、执行/恢复、返工、查看目录与 digest，并显式批准；生产时只在终端底部重绘单行状态。
- `content_run_cli_smoke=passed approval=versioned`：CLI 使用调用者所见 version 与 Revision digest 批准，完成后可返回主菜单。
- `live_codex_resume_protocol=passed`：真实 Codex CLI 逐行触发 `thread.started` callback，并用同一 thread ID 完成一次结构化 `exec resume`；探针未调用生图工具。

## S4 托管执行（2026-09-04）

- `ManagedRunExecutor` 容量为 1，第二任务返回 `producer_busy` 和当前 run_id，不自动排队。同步认领并新建 Attempt 后才提交线程；调度失败落为 interrupted。
- Web lifespan 和 CLI 共用按数据库路径绑定的 OS 文件锁。`recover_inflight` 只有在持有锁且执行记录清洁时才进行，不会把另一进程正在生产的任务恢复掉。
- 认领保存 owner、Revision/Attempt、30 秒 lease；约 5 秒续租。heartbeat 用条件 UPDATE，不递增审批 version，也不生成进度事件。
- 数据库旁的 `*.execution.json` 在启动子进程前原子落盘，记录 owner、Run/Attempt、宿主 PID、生产进程 PID/创建时间与阶段；未确认停止的记录阻止恢复。系统锁不靠“文件是否存在”判定。
- 关闭时先使 owner 失效，再通知 Producer 停止，等待线程退出后释放锁；晚到结果不能改变中断状态。超时则保留锁/记录，明确报告待核实。Windows 子进程树通过 Job Object 回收。
- 新输入快照含 topic brief、栏目描述和受众；旧快照有默认值。复用 v3 的 lease 字段，没有新增 migration。
- `smoke_studio_executor` 通过：并发幂等创建、双击、第二任务 busy、旧版本/owner 拒绝、heartbeat 版本不变、初始化/调度失败、有序停止、显式同 thread 新 Attempt 恢复、validating 重启只验收。
- `smoke_studio_process` 通过：真实本地子/孙进程终止、超时、取消信号、非零退出、Windows 宿主硬退出与未确认记录阻止恢复。故障注入使用无费用本地进程。
- `smoke_studio_run_api` 通过：创建 201/幂等 200、显式 execute 202、忙碌 409、版本校验、运行期间读取和新增栏目、轮询到 awaiting_approval。

### 非正常退出后的人工核实

若启动提示恢复受阻，先检查对应数据库旁的 execution.json 中宿主与子进程身份。确认该次进程树都已退出后，人工归档这份执行记录再重启；不要删除 lock 文件冒充取得锁，也不要按 codex/python 进程名称批量结束进程。只有用户显式开始/恢复才再次调用模型。

## S5 文件验收加固（2026-09-04）

- Web 复用既有 approve/request_revision；确定性验收补 Manifest/图片 resolve 后的目录边界检查，并从同一份图片字节解码/计算摘要。
- ValidatedImage 增加可选 sha256（兼容旧 JSON），供受控图片 URL 验证用户看到的图片字节；总 artifact_digest 算法不变。
- 文件不可读或产物变化时批准失败且保留状态，不冒充内容质量评审；所有新增读取/订阅不调用 Producer。
- `smoke_studio_artifacts` 与 `smoke_content_run_service` 通过；旧 digest 算法不变，旧 JSON 兼容。图片经受控读取校验后才能用于批准，Web 不另建状态机。
## 按快照使用生产 Skill（2026-09-08）

- _produce_claimed 将既有 input.skill_name 传给 Producer，并使用数据库相邻的 Skill 注册目录。外部 Skill ID 指向不可变版本，改绑不重写已有 Run/Revision；内置默认兼容。验证见 docs/agent-studio/producer-skills/SPEC.md。
