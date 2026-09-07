# Web 宿主复用 Agent Loop

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
