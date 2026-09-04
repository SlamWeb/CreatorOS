# ContentRun SPEC

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
