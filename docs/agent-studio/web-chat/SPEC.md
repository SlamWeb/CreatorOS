# Web 宿主复用 Agent Loop

## P1 账号会话与作用域 · 2026-10-04（完成）

### 问题与最小方案

- 本机下载自用，不引入登录用户、租户、常驻账号进程或新的 Agent 框架。总览与账号入口复用同一个 Loop。
- 业务数据仍在 SQLite（默认 `data/creatoros.db`）；Web Session 在相邻 `creatoros-agent-sessions/<uuid>/` 保存 `view.json`、完整 `messages.json`、checkpoint 和 Trace；CLI 仍是 `sessions/latest.json`。不迁移历史消息、不保存 Codex 凭证。
- Session 创建时绑定 `scope_kind=overview|creator` 和 `creator_id`，无改绑接口。缺字段旧记录视为 overview；换账号必须换会话。模型只能读宿主注入的范围，不能从用户文本改变范围。
- 创建和发送时检查账号存在且启用。账号作用域只开放账号业务工具及会话归档回读；全局 Skill 安装/提炼、跨账号分配留在总览。普通知识问答不限制话题。
- RuntimeContext 携带固定 creator_id/session_id。StudioClient 将宿主 session_id 放在请求头；服务端从本地会话文件解析绑定，而不是相信模型参数。资源 ID 必须查真实归属：Series、Topic、调研批次、Run。未知的账号工具 API 默认拒绝。
- 此范围是本机使用的防误操作边界，不是多租户安全：普通本地 UI 和 overview 仍可操作全部数据。不防拥有本机文件/API 权限的人，不引入新认证协议。
- Run 读取/执行同时要求生产时账号快照和当前栏目归属匹配；移动栏目后的历史任务先由总览处理。不新增跨账号转移工具。全局单条聊天执行规则保留，不做并发调度。
- 同一 Studio 进程内，账号范围检查与路由执行和 HTTP 写入互斥；不会被同时发生的栏目转移插入检查/执行之间。不提供跨进程认证或防止本地直改数据库的保证。

### UI 与接口

- 工作台当前账号提供“账号 Agent”入口 `/agent?creator=<id>`；新会话仅在第一次发送时创建。历史按 overview/creator 分组；刷新、切换历史和账号不调用模型。
- POST `/api/agent/sessions` 保留无正文/空对象兼容，新增可选 `creator_id`；GET 列表支持 scope_kind 和 creator_id 过滤，过滤先于最近 30 条截取。
- 显示当前绑定账号，保留总览入口。已有 chat 与 URL creator 不一致时阻止发送，不将旧消息重标账号；单独 chat 深链从会话元数据恢复账号。

### 验收与暂缓

- 隔离 SQLite + 独立会话目录：A/B/overview 的创建、列表、连续追问、重启旧数据兼容；假冒 B 的 Series/Topic/Run/batch 拒绝且零写入；失效账号拒绝，未知 API 拒绝。
- 原 Web Agent request_id/版本/SSE/恢复/全局工具 smoke 回归；真实低成本 DeepSeek 只查询隔离账号，不调研、不生产、不发布；记录消息/工具轨迹。
- 浏览器实操账号入口、切换/新对话/刷新、深链和错误阻止；桌面与 390px 截图检查、typecheck/build。
- 暂不做账号摘要、运营目标/规则长期记忆、自主计划、定时行动、向量记忆、CLI 账号模式、会话迁移或新数据库表。下一阶段再做账号状态 brief。

### 本轮证据

- `tests.smoke_account_sessions`：真实隔离 HTTP/SQLite/JSON 检查新建 A/B/总览、禁止改绑字段、旧文件兼容、筛选先于最近 30 条截取、删除/停用账号禁发及重启恢复绑定。无模型调用用于纯状态验证。
- `tests.smoke_account_scope`：真实 loopback + 实际 Studio 工具适配器验证会话头、只列 A、B 的栏目/选题/批次/Run 拒绝，拒绝的写操作零业务行数变化；移动栏目旧快照保守拒绝。故障注入暂停在检查后，另一个 HTTP 转移等待至入队完成，证明同进程互斥窗口。
- `tests.smoke_account_sessions --live`：真实 DeepSeek 两条连续只读指令，5 次模型请求，依次调用 list_creator_series → list_series_topics → list_creator_series。输入 24,888 / 输出 665 / 缓存命中 19,456 tokens；另一账号会话空白、没有调研/生产/发布。这是单条联调样本，不是 Benchmark 分数。临时完整会话随夹具清理，重跑命令可复现。
- 已通过关联 smoke：web_agent、agent_studio、runtime_context、studio_composition_tools、studio_operations、studio_api；既有 SSE、版本/幂等、失联处理和重启协议保留。Python compileall 通过。
- 前端 build/typecheck 与定向 Playwright 3/3 通过：真实账号/会话 API，首次发送才创建、刷新零提交、历史分组、深链恢复、错配/无效账号禁发、从 A 工作台切到 B 栏目。仅聊天 turns 注入 503，验证失败保留输入、不重试；没有在浏览器调用生图。
- 原 studio-workflow 完整回归通过（1/1，32.4s），包含创建、Preview、受控产物生产、返工、批准和刷新。首次运行的故障拦截仍匹配无参数旧列表 URL，未命中新增的范围 query；改为兼容查询参数的拦截规则后原路径通过，不放宽业务逻辑。
- 已查看 1440×900 / 390×844 截图，输入框/绑定说明/回链可见，无横向溢出。截图位于忽略的 `web/test-results/account-chat-*/`。曾发现测试误用未重建 dist，已重建并重跑；同时修复账号 404 延迟反馈及 creator 查询参数阻止切换其他栏目的回归。
- 正式运营库、生产输出、Codex 登录态与全局 Skill 未修改。主框架仍只有同一 Loop；跨会话账号记忆与目标型规划尚未实现。

## 已实现 · 2026-09-08

- 用户授权实现上一轮的下一步：Web 对话接现有 Agent，不重写推理/工具循环。
- 原 CLI 默认行为保留；Loop 可注入 session_file 与 RuntimeContext。Web Session 与 CLI latest.json 隔离，压缩检查点跟随各自文件。
- Web 首版只开放上一轮的 5 个 Studio 工具，复用同一 Tool 定义与执行函数；执行端也校验宿主工具范围，不开放文件写入、旧直接生产或自动批准。暂不广告无法执行的 Skill。
- 新增独立 Agent 页面；现有运营指令 Preview/确认抽屉保留并可从对话页进入。对话可以查目录、提交已有选题、追问同一 Run；不承诺新增选题/审批已通过 Agent 接通。
- POST 提交一条消息立即返回，单个后台 Agent 线程调用现有 Loop。SSE 推送会话快照（覆盖，不累加），断线只停止观察，刷新/重连不触发推理。生产仍归原 ContentRun 执行器。
- Session 用与数据库相邻的隔离目录存 JSON 消息、compaction、UI 记录；不增加数据库表。服务端只生成 UUID，会话索引/最近对话可找回，UI 不公开原始 system/tools/绝对路径。
- 本机单用户、同一服务同时只允许一条 Agent 指令。request_id 去重 + expected_version 拒绝旧页面提交；不做 Side Chat、多进程聊天 Worker、CLI/Web 同一个 Session。
- 服务退出停止后续模型/工具步骤；未完成请求重启标 interrupted，不自动续跑。已经保存但缺结果的 tool call 补“结果未知，请查询”以修复协议，不重放动作。没有工具级强制取消承诺。

## 验收计划

- 隔离 SQLite/会话目录：两会话不串线、重复请求、并发/旧版本、断线和恢复、异常不泄漏、缺 Key、宿主工具拒绝、压缩路径和 CLI 回归。
- 真实 DeepSeek + HTTP + 浏览器测试目录查询、连续追问、流式显示和刷新恢复；生产接线用已有 Run 幂等返回或受控 Producer 做故障验证，不重复完整生图，不发布、不写正式库。
- 桌面和 390px 截图自检；typecheck/build、关联 smoke；更新说明与本 SPEC 后 commit/push。

## 实施与验收结果

- 复用既有 Loop/stream_llm/工具执行/自动压缩；只增加宿主注入，不另写一个模型循环。Web 首版 5 个工具，CLI 默认不变；Skill 生产仍由 ContentRun/Codex 执行，不在 Web 广告未开放工具支持的 Skill。
- 新增 `/api/agent/sessions` 列表/创建、`/{id}` 查询、`/{id}/turns` 提交、`/{id}/events` SSE。会话 30 条近期索引、UI 最近 200 条记录投影，完整记录保留本地文件；不承诺长期会话全文搜索。
- `tests.smoke_web_agent` 通过真实 loopback HTTP + 临时 SQLite：流式观察/断线、重复请求/不同正文冲突、并发/旧版本拒绝、两会话隔离、执行端拒绝未开放工具、错误脱敏、缺 Key、重启修复未知工具结果、真实 Loop 的独立 compaction 路径与宿主说明更新。受控模型仅用于可重复故障/边界注入。
- 关联 11 项 smoke 通过：agent_navigation、agent_events、agent_skill_selection、agent_task_tracking、runtime_context、compacted_model_context、auto_compaction、agent_studio、studio_api、studio_operations、studio_run_api。
- 前端 typecheck/build 通过；原 Studio Chrome E2E（创建→Preview→生产→返工→批准→刷新）通过，受控生产只用于该产品回归。
- 真实 DeepSeek：3 条浏览器指令（查账号栏目、提交已有选题、追问 Run）+ 2 条 HTTP badcase 复核，共 11 次模型请求；输入 24,547、输出 1,572 tokens，缓存命中 18,176。只是一个链路样本，不是 Benchmark 分数。
- 真实调用轨迹包含 list_creators → list_creator_series → list_series_topics → start_content_run → get_content_run；三轮均观察到生成期间的部分文字。刷新/关闭浏览器/服务重启后找回同一会话，页面链接打开同一 Run。隔离 Run 预设 cancelled，始终没有新增 Attempt 或触发生图/发布；正式数据库不变。
- 浏览器桌面 1440×1000 / 手机 390×844 已截图自检；修复 Markdown 原样显示与手机会话选择器折行；无横向溢出、pageerror 为空。Markdown 不启用 HTML、不加载模型输出图片，使用默认安全 URL 转换。
- 本地证据（忽略、不提交）：`tmp/web-agent-live-20260908-001357/`，含 browser-report.json、review.db、review-agent-sessions 与截图。真实 Web Session：`1f68949d-6b0d-498e-91be-ae9771e6691e`。

## 真实 badcase 与暂缓

- 已取消 Run 的旧摘要泛泛建议“恢复/返工”，模型据此声称页面可以恢复。没有实际执行副作用，但回答错误。修复 Tool 摘要按终态/allowed_actions 提示。
- 第一次重启复核中模型仍引用旧会话的系统说明，未重新调用工具。修复 Web 宿主每次执行前同步当前系统说明；历史消息不删除，旧 checkpoint 若哈希失配回退完整历史。再次真实查询正确说明 cancelled 不能在页面恢复/返工。旧错误回答仍作为历史证据保留，不改写日志。
- 这不代表模型永不误导：后续 Eval 应测“旧消息错误提示 vs 当前状态”“只查询不生产”“重复提交不重跑”等最终环境约束与回答一致性。
- 不做 CLI/Web 共用同一会话、多 Agent 同时聊天、停止按钮/强制终止同步工具、自动恢复推理、长期记忆、自动批准或发布。服务关闭仅阻止后续模型/工具步骤，已经提交的 ContentRun 仍按原执行器退出/恢复协议处理。
