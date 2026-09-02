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

- 前台同步执行；暂不实现后台 Worker、自动调度、Side Chat、MCP Server 或平台发布。
- 只做确定性验收：Manifest/Pydantic、图片存在性、安全路径、顺序、可读尺寸和基础字段。
- 不使用 LLM-as-Judge；内容质量评审与发布策略留到真实产物稳定后。
- 预留 `origin_session_id`、`context_snapshot_ref`、`producer_thread_id` 与 lease 字段，但 v1 不建立 ObserverSession。

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
