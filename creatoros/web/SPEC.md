# CreatorOS Web API SPEC

## Native-v1 只读产物投影（2026-10-02）

- 根据冻结输入中的 `production_protocol` 选择 legacy 或 native-v1 产物校验；缺省历史数据仍走 legacy。最终卡片把 native checkpoint 的 `content` 投影到既有 `page_spec`，并提供相同页的 `image_prompt`，不要求双 Skill composition。
- 活跃 Revision/Attempt 的 native 部分预览从 `native_checkpoint.json` 读取；通过 native loader 校验本次输入与 Skill 文件，并重新验证页面图片。仍只返回既有部分卡片字段，不泄露文件路径；legacy storyboard/visual checkpoint 检查保持原样。
- 下载校验也按冻结协议验证完整产物摘要，避免 native checkpoint/Skill 证据被误当成 legacy 证据。
- 隔离 `smoke_native_artifact_web` 通过：真实 HTTP + 临时 SQLite/文件系统覆盖单 Skill 原生页内容与 Prompt 投影、部分图片/错误 checksum、已批准 ZIP 下载、Skill 篡改后拒绝投影，以及查询不改 Run version；没有生产调用或正式数据写入。

### 最终卡片证据字段独立显示（2026-10-02）

- 复现：当某页只有内容稿或只有生图 Prompt 时，Run Inspector 原来要求两者同时非空，导致已投影的单字段证据被整块隐藏。
- 最终卡片的内容稿与 Prompt 现按字段独立展示；两者都空或空白时隐藏证据区。双字段投影与现有只读/审批边界不变。制作中 partial 卡片仍只显示图片，不扩展 DTO。
- 受控 Playwright 回归覆盖“仅内容稿”“仅 Prompt”“两者为空”三个状态；图片由 HTTP fixture 提供，不调用真实生产。截图保存到忽略目录 `tmp/testresults/`。
- 完整关联回归：production-progress + studio-workflow 10/10 通过（34.8 秒），含真实点击/刷新/返工/审批受控流程。另用隔离 8876 对真实英语图走过展开证据/放大/返回/刷新，保持同一 Run 待批准；未点击真实试产的批准或发布。

## 逐页制作预览接口（2026-10-01）

- RunDetail 通过 `partial_cards` 投影活跃 Revision/Attempt 中经过 `visual_checkpoint.json` 校验的已完成页面；DTO 仅含 `order`、checksum URL 和 `warnings`，不提供本机路径、审批或验收标记。
- 只接受 schema v1、匹配当前生产输入摘要且与 storyboard/visual plan 一致的 checkpoint。图片必须位于 attempt 的 `partial-images` 内、路径无符号链接逃逸、SHA-256 一致并可解码为受支持栅格图；否则不投影。旧 Run/缺失 checkpoint 返回空列表，不阻塞详情查询。
- 独立 `GET /api/runs/{run_id}/partial-cards/{order}?checksum=<sha256>` 每次重新校验 checkpoint 与图片摘要；不复用最终卡片/批准路径，缺失/无效 checkpoint 返回 404，checksum 冲突返回 409，响应 `no-store`/`nosniff`。
- 隔离 `smoke_studio_partial_cards` 通过：真实 HTTP/临时 SQLite 覆盖投影无路径泄露、受校验图片读取、错误 checksum、路径逃逸拒绝、Run version 不变及旧接口兼容；没有启动正式服务或生图。

## 生产阶段进度投影（2026-10-01）

- RunDetail 增加可选 production_progress：阶段、阶段状态、开始/最近活动时间、安全事件/活动分类、已完成工具调用数及已知内容页数。由活跃 Revision 最新 Attempt 的 sidecar 提供；不是 workflow 真相，不改变 version/lease，不消费模型原文。
- 只从服务 output_root 中该 Run/Revision/Attempt 的明确目录读有限大小 JSON；缺失、损坏、越界、符号链接或旧版没有字段时返回 null，不因此阻断 Run 访问。新 Revision 未执行时不串用旧版进度。
- 沿用前端活跃 Run 的两秒查询刷新和状态 SSE；不新增 event enum 或数据库迁移。SSE 连接状态只表示观察连接，不代表生产活跃。
- 隔离 `smoke_studio_production_progress` 通过 HTTP/SQLite，覆盖旧数据、正常投影、秘密不泄漏、坏 JSON/路径拒绝、换版不串用及读取无写入；原 studio_api/run_api/executor/artifacts 回归通过。

## 本地 Skill 目录接入（2026-10-01）

- Skill 列表提供当前文件元数据与 `local_path`。组合创建/改绑及单 Skill 绑定接受已登记路径，规范化为原目录 ID，保留 Web/Agent 与旧数据库兼容。
- 本地路径只在 Skill 配置元数据明确提供；Run/产物/会话投影及错误路径屏蔽仍照旧。本轮不改前端，不开放任意文件读取。
- 隔离 HTTP 验证单/双 Skill 路径绑定、CAS、旧快照与产物接口通过；详见 integrations/runs SPEC。

## 人工发布与反馈 API（2026-09-30）

- 复用 Run Inspector：POST `/api/runs/{id}/publication` 登记批准后的人工笔记链接，POST `/api/runs/{id}/publication/metrics` 追加手填指标，GET `/api/runs/{id}/download` 下载经摘要重验的图片/文案 ZIP；RunDetail 加 publication 投影。平台发布与指标查询仍完全由用户手工完成。
- 隔离 HTTP smoke、现有产物回归通过；正式服务 GET 显示真实消息队列 Run 待批准且 publication 为空。完整契约与下一步见 `creatoros/publication/SPEC.md`。

## Agent 展示范围规则（2026-09-14）

- D05 开发评测发现：用户禁止重复已入队标题时，Agent 在正文正确筛选，却在补充说明重述排除项。
- 新增 DISPLAY_SCOPE_RULE 并接入 WEB_INSTRUCTIONS，明确筛选与禁止重述约束同样适用于补充说明。运营 Eval 复用该规则，验证记录见 docs/agent-eval/RESULTS.md。
- 只调整宿主说明；不改页面、API、队列或发布行为。

## 统一选题库 API（2026-09-14）

- 新增 GET /api/series/{id}/topic-library：只读合并持久化候选与正式 Topic，按确定性 ID 去重，正式编辑优先；支持 all/pending/queued 和分页。旧 /topics 不变，Web 视觉尚未接入。
- 隔离库验证确认前后同 ID、保留来源、过期不可选、待选不能创建 Run，真实 DeepSeek 查询/预览通过；详见 docs/agent-studio/topic-research/SPEC.md。

## Context Trace 查询（2026-09-12）

- `GET /api/agent/sessions/{session_id}/context-trace` 复用会话所有者路径，after为事件行游标、limit 1–100，返回items/next_cursor/has_more。只提供结构化计数与ID，无原文/文件路径；未知会话404，空记录合法。
- Trace属于Agent Session，不是ContentRun；原Web usage/event行为保留，页面展示本轮未修改。真实DeepSeek、隔离HTTP分页/会话边界与原Web smoke通过，细节见上下文专项SPEC C3。

## 上下文预算停止（2026-09-11）

- context_blocked 持久化进 Web entries，本轮 failed/error 明确提示预算不足且历史保留；宿主故障测试与 Web 回归通过。见 docs/context-management/SPEC.md C2。

## 会话工具结果回读（2026-09-11）

- Web Agent 白名单加入 read_tool_result 和受限 read_file：前者按当前会话绑定回查未截断正文，后者只能读取当前会话的 `.tool-results` 归档文件，不能读取项目任意文件或其他会话；归档大文件按字符分页。
- Web 不因开放归档 read_file 而注入 Skill 目录；归档内容被视为历史数据，不是当前状态或指令。
- 真实 HTTP/DeepSeek 隔离验收、跨会话拒绝和本地分页验收见 docs/context-management/SPEC.md。

## 栏目选题研究（2026-09-09）

- 新增 research_routes：POST/GET series/{id}/topic-research，GET topic-research/{id}，POST topic-research/{id}/preview。后台研究由 lifespan 管理；选择复用 PendingOperation，模型没有确认权。
- DTO 标记 research_selection，使共用抽屉提示回候选修改；错误不透传 Codex 原始栈。真实验证见 docs/agent-studio/topic-research/SPEC.md。

## Web Agent 宿主（2026-09-08）

- 新增 chat/chat_routes：后台单指令宿主调用既有 run_agent；HTTP 提交 + SSE 快照观察，断订阅不取消执行。目录/生产复用五个 Studio Tool，自调用地址来自真实本机监听地址，不读取 Host 或修改全局环境变量。
- 会话 JSON 与数据库相邻，Web/CLI 分开；显式 session_file 贯穿原始消息/压缩检查点。request_id 去重、expected_version 校验；重启标 interrupted、未知 tool result 修复但不重放。
- 宿主说明在每次指令前同步为当前配置，历史用户/模型/tool 消息保留；旧 checkpoint 若摘要校验不匹配则回退原始历史。
- 验证与边界见 `docs/agent-studio/web-chat/SPEC.md`；不是共享 CLI/Web Session、自动恢复推理或发布系统。

## 当前理解

- 这是 Studio 的本地 HTTP 接线层，不是新的 Agent Runtime，也不是 PersonClone API。
- 业务真相仍在 storage、operations、runs；浏览器只消费显式 Pydantic DTO，不能接触 ORM Session、密钥、原始异常栈或绝对文件路径。S3 写入仍由本模块调用既有 Repository/Service。

## 已完成阶段（S1–S5）

- 用 FastAPI 暴露健康检查、概览、Creator/Series/Topic 目录、ContentRun 摘要/详情和待确认计划的只读查询。
- 空库返回合法的空结构；有数据时提供真实关联、计数、状态和允许动作，让页面不再猜业务状态。
- S3 新增账号/栏目创建和选题 Preview/confirm/cancel/edit 路由；不调用 LLM 或 PersonClone。
- S4 新增 Run 创建、后台提交、恢复和取消路由；请求不等待 Codex，状态由 `ContentRunService` 和 `ManagedRunExecutor` 负责。
- S5 增加受控图片、历史版本详情、批准/返工与可重连事件流。Web 批准仍是验收，不是发布。

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
- 运行详情展示安全的 Manifest 投影、图片 URL、digest、文件检查、usage 和 trace 是否存在；不返回 `artifact_directory` 或任意文件路径。
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

## S5 实现与验证（2026-09-04）

- 复用 ContentRunService 的批准/返工协议，不增加第二套状态机；批准绑定所见 Run version、Revision ID 与产物 digest，409 后重新检查，不自动确认。
- 从 Run/Revision/Attempt 解引用约定产物目录；图片只允许 Manifest 中的有序 raster 文件，拒绝跨 Run、路径逃逸、符号链接逃逸和坏图。图片 URL 带版本 digest 与图片校验和，响应不缓存；不暴露文件系统路径。
- RunDetail 增加发布文案、来源、图片与文件检查信息；旧版可读但不可批准，返工只创建 Revision，显式开始才调用 Codex。
- SSE 读取持久化业务事件，snapshot 不跳过待补事件；after_id/Last-Event-ID 恢复，连接/断开无生产副作用；前端按 ID 去重并保留轮询兜底。
- 验证：临时数据库+本地产物测试 1/5/6 张、缺图/坏图/逃逸、旧版本批准冲突；真实本地 HTTP/SSE 验证订阅与断线；已有真实图片用于浏览器只读 QA，不重复调用生图。
- `smoke_studio_artifacts` 通过：图像校验和绑定已保存版本，调用者换 checksum 不能绕过；图片和 Manifest 符号链接逃逸、跨 Run/Revision 引用被拒绝；旧 validation 无 per-image hash 时重算总摘要，兼容旧记录但不写回。
- `smoke_studio_events` 通过真实 localhost TCP + HTTP/SSE：snapshot、分页游标、Last-Event-ID 优先、顺序重放、断观察不中断生产、重新订阅不增加 Attempt。事件只暴露允许字段，不透传包含路径的原始 payload。
- 本地 `StudioServer` 关闭时先通知 SSE 观察者结束，再让 lifespan 收尾生产执行器，避免长连接卡住关闭；普通 ASGI 部署需自行设置有界 graceful timeout。
- 浏览器用隔离 SQLite 和复制的已有真实 5/6 张图片走通切换、放大、历史只读、返工不生产、复制文案、批准及刷新持久化；正式库和原图未更改。
- 最终关联回归 12 项 smoke 通过：content_storage、content_run_storage/service/cli、codex_producer、studio_api/operations/run_api/executor/process/artifacts/events；Python compileall、前端 typecheck/build 通过。

## 下一步

- S6：自然语言运营命令的 Web Preview/确认入口；S7 再完成正式演示与完整真实生图联调。本阶段不新增发布、记忆或 Agent Benchmark。
## S6 完成（2026-09-04）

- POST /api/operations/propose 接真实 DeepSeek，支持可选 series_id；仅 propose/edit 按需构造 Parser，60 秒超时、零隐式重试，不改变 Agent Chat 默认参数。
- create_app 支持 parser/factory 注入。模型未配置/不可用 503，坏输出 502，非法 scope 404/409，版本竞争 409；未配置模型时表单 Preview/确认仍可用。
- DTO 输出真实 version、scope、白名单 Preview/usage；列表无嵌套 token。旧 Preview 只读补齐名称，不重签 token；非 succeeded 确认不返回成功。
- 15 项关联 smoke 通过；真实 4 次 HTTP 模型验收 + 浏览器 3 次真实模型请求，无生图、发布或正式数据修改。
## 生产 Skill 入口（2026-09-08）

- 增加技能目录、安装提交/查询、栏目显式条件绑定 API，宿主生命周期回收安装进程；新旧生产复用 ContentRun。Web Agent 新增三个安装/查询工具，不开放模型直接绑定。验收见 docs/agent-studio/producer-skills/SPEC.md。
