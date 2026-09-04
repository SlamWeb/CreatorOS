# CreatorOS Web API SPEC

## 当前理解

- 这是 Studio 的本地 HTTP 接线层，不是新的 Agent Runtime，也不是 PersonClone API。
- 业务真相仍在 storage、operations、runs；浏览器只消费显式 Pydantic DTO，不能接触 ORM Session、密钥、原始异常栈或绝对文件路径。S3 写入仍由本模块调用既有 Repository/Service。

## 已完成阶段（S1–S4）

- 用 FastAPI 暴露健康检查、概览、Creator/Series/Topic 目录、ContentRun 摘要/详情和待确认计划的只读查询。
- 空库返回合法的空结构；有数据时提供真实关联、计数、状态和允许动作，让页面不再猜业务状态。
- S3 新增账号/栏目创建和选题 Preview/confirm/cancel/edit 路由；不调用 LLM 或 PersonClone。
- S4 新增 Run 创建、后台提交、恢复和取消路由；请求不等待 Codex，状态由 `ContentRunService` 和 `ManagedRunExecutor` 负责。

## 当前假设

- `create_app(database=...)` 用于隔离测试；应用不持有外部传入的 Database 所有权。
- `python -m creatoros.web` 是本地启动入口，会先显式执行 Alembic，再绑定 `127.0.0.1:8765`。
- `creatoros.web.app:app` 适合已经迁移过的 ASGI 部署；它不会在导入或请求时偷偷 `create_all`。
- 查询分页上限 100；概览列表是为首页准备的有限摘要，不是历史导出 API。

## 对外影响

- 新增依赖清单 `requirements-web.txt`，不改核心 `requirements.txt`。
- 新增 `creatoros.web.schemas`、`queries`、`writes`、`app`、`__main__`；Storage 只补充 `list_creators/count_creators` 目录读取方法。
- `GET /api/overview`、`/api/health`、目录/运行/运营计划 GET 路由与 S3 最小 POST 写路由可被本地前端消费。
- `ErrorResponse` 统一为 `{ "error": { "code": ..., "message": ... } }`；绝对路径在错误文案中会被截断/替换。

## 设计边界

- `OverviewView` 的 counts 使用数据库全量查询；不能用首页截断后的数组长度冒充总数。
- `TopicView.available_actions` 和 `RunSummary.allowed_actions` 是后端策略提示，前端隐藏按钮不构成授权；真正写操作仍由后续 Service 校验。
- queued 且无关联 Run 或关联 Run 仍为 queued 时显示 start；interrupted 或可重试 failed 显示 resume。后端提供所见 Run version，开始前不偷偷刷新版本重发。
- 运行详情只显示 `has_output`、digest、validation、usage 和 trace 是否存在，不返回 `artifact_directory` 或任意路径。
- `/api/health` 只报告数据库是否能执行 `SELECT 1` 和 Codex 可执行文件是否存在，不发起付费探针、不返回 Key。
- S3 写入只允许最小账号/栏目创建及 Operation Preview/confirm/cancel/edit；S4 的 Run 路由只能通过 ContentRunService 提交，浏览器不直接连接数据库。

## 验收

- 临时 SQLite 从 Alembic 空库升级后，HTTP smoke 覆盖空库、目录关联、运行投影、分页边界、404/422 和查询不写库。
- 真实本机服务启动后 `GET /api/overview` 返回 200 和当前正式空库的零计数；服务绑定 loopback。
- `compileall` 通过；现有 storage、operations、runs smoke 不退化。

## 最近验证（2026-09-03）

- `python -m compileall -q creatoros/web creatoros/storage/repository.py tests/smoke_studio_api.py`：通过。
- `python -m tests.smoke_studio_api`：`studio_api_smoke=passed empty=passed catalog=passed run_projection=passed projection_safe=passed`，并确认查询投影仍不泄漏路径；S3 写入由独立 smoke 覆盖。
- 启动 `python -m creatoros.web` 并请求 `http://127.0.0.1:8765/api/overview`：`studio_live_api=passed status=200 empty_overview=passed`。
- 关联回归：storage、operation plan、pending operation service、content run storage/service 共 5 项通过；全包 `compileall` 通过。
- 不涉及真实 LLM、PersonClone、Codex、生图或发布；本模块验证没有新增 API 费用。
- `python -m tests.smoke_studio_operations`：创建账号/栏目、Preview 零写入、确认写入、版本冲突和重复确认幂等通过；隔离 SQLite 浏览器完整走通账号 → 栏目 → 两选题 → Preview → 确认。

## S4 最近验证（2026-09-04）

- POST /api/runs 仅创建：服务器固定 content:{topic_id} 幂等键，创建 201、取回 200；浏览器不能自定义键绕过幂等。POST /execute（/resume 同义）带 expected_version，认领后返回 202。
- 同时只执行一个 Run；busy/already_running 返回 409 与原 run_id。取消仅允许非运行态。写入限制为 JSON 和本机明确 Origin。
- app lifespan 管理 OS 单实例锁和执行器，生产不占 HTTP 请求；人工关闭后只有显式恢复才增加 Attempt。
- `smoke_studio_run_api`、`smoke_studio_executor`、`smoke_studio_process` 通过；目录/选题 API、CLI/Run Service、Producer 解析回归通过。
- 隔离浏览器验证：开始→producing；第二任务 busy 链接当前运行；切换栏目并 Preview/确认新选题；服务有序退出→重启→interrupted；显式恢复同一 Run 的 Attempt 2。强制终止也验证了未确认记录阻止恢复。
- 真实 Codex 协议探针通过；浏览器长耗时测试为受控故障注入，未写正式数据库。

## 下一步

- S5：接图片 Manifest 投影、Revision/Attempt Inspector、返工/批准和事件 SSE；不在 S4 提前提供任意文件读取或平台发布。
