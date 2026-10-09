# CreatorOS ↔ Codex 最小通信协议

## 2026-10-09：权威结果与可选记录的边界

- 讨论完成/失败/中断以宿主持久化的 discussion record 为准，答复存于 record.reply；reply.txt、error.txt 和用量文件只是可选副本。终态先保存，副本 I/O 失败不能改判原结果。
- 调研 SDK completed 后仍须校验 ResearchReceipt、数量与实际搜索证据；有效候选和状态由宿主保存到批次。response.txt、codex_trace.jsonl 和错误详情只是可选副本，不替代业务验收。
- thread ID、任务/批次/worker 回执仍是权威记录，写失败严格报错；调研公开观察器会保存批次事实，不能整体当可选 Trace 忽略。讨论禁止工具守卫同样不可忽略。
- 不重发 SDK turn、不自动重试失败任务，不改变查询/授权/生产/审批语义；没有全盘不可写时的第二套状态系统。隔离 HTTP 和故障注入证据见 `docs/reliability/SPEC.md`，不是新增真实模型产出验证。

## 2026-10-08：围绕产物续聊与统一职责

本节覆盖下文旧版“不支持 Side Chat / 账号任务列表”的边界，不重写生产执行器。

- CreatorOS 是业务事实与授权入口：账号、栏目、选题、Skill 绑定、Run/Revision/审批；账号 Agent 获取目录元数据，按需查任务，不自动继承 Codex 全部历史。
- Codex 是任务执行者：调研取得栏目与已有选题快照；生产取得冻结输入/Skill；讨论取得指定版本的已验收图片、内容/Prompt 与冻结 Skill。明确记录实际输入，不假定上下文共享。
- 本次先提供完成产物的只读讨论：从生产 thread fork 独立讨论 thread，后续 resume 该讨论 thread；旧任务无 thread 时全新讨论并明确标注。原生产 thread/文件/审批状态不变。实际附上 LocalImageInput，而不是只写图片路径。工具与写权限限制按 SDK 能力核验，不凭 Prompt 声称隔离。
- POST /api/runs/{run_id}/discussion，正文 request_id、revision_id、artifact_digest、message；返回一条持久化消息任务。GET 同路径返回 {items: [...]}，按时间顺序。记录 id/request_id、run_id/revision_id、message、status(queued/running/completed/failed/interrupted)、reply/error、thread_id、source_thread_id、created_at/updated_at、events[{id,kind,text,at}]、context（输入范围摘要）。同 Run 重复 request_id 同正文复用、异文冲突；第一版整个讨论执行器容量为 1，忙时拒绝，不偷偷排队。GET 不调用模型。重启运行态改 interrupted，不自动重发。
- 讨论任务不是生产 Run：讨论失败不能把作品改失败；讨论完成不是返工或批准。用户明确要求修改时复用既有 /revisions 接口与 expected_version，新版 queued，仍需显式执行。账号工具与 UI 共用这些接口，不能接受任意 thread_id/文件路径。
- GET /api/creators/{creator_id}/tasks?series_id=... 汇总现有生产、调研、讨论记录；含 items/summary/as_of，由原记录投影，不再持久化第二套业务状态。账号作用域检查冻结与当前归属，转移后拒绝错账号访问。
- UI：产出页「与 Codex 讨论」，显示针对第几版、只讨论不改图、逐条消息/活动/失败、可展开“本次传入什么”；刷新找回历史，不重新提交。正文明确要求返工时走原返工入口；账号/栏目页显示紧凑任务列表，不增加装饰 KPI。
- 第一版不接运行中 steer、不自动跨任务记忆、不自动修改 Skill、不自动重画、不自动发布。生产进行中不能开启讨论，界面说明等待完成；不伪称已排队。用户认可的通用反馈通过已有 Skill 编辑功能保存。

验收：讨论有真实图片输入且只读；多轮复用讨论 thread；重复/异文请求、越权、越界文件、旧 digest、失败/重启与原作品状态不变；Web 与 Agent 同任务；历史版本切换不串消息；桌面/手机截图与操作断言。真实 SDK 看图讨论只做低成本隔离验证，不生图/发布/修改正式运营数据。

### 谁能看什么、谁可以改什么（现行约定）

| 角色/任务 | 默认收到的上下文 | 持久化与修改权 |
| --- | --- | --- |
| CreatorOS 账号 Agent | 账号→栏目→Skill name/description；本聊天历史/滚动摘要；按需查询工具结果 | 经受作用域限制的业务 API 修改；不知道 Codex 全部历史或图片像素，不可凭描述假装看过图 |
| Codex 调研 | 当前账号身份、目标栏目、内容 Skill 正文、该栏目正式选题及未入队候选、用户补充要求 | 独立 SDK thread、只读调研；返回候选回执，宿主保存，不直接入队/生产 |
| Codex 生产 | 本次 Topic/brief/受众、冻结 Skill 与 assets、返工要求；双 Skill 在同 thread 内检查配合 | 工作目录产物可写，宿主检验后才能成为待批准版本；不改账号数据库、不自行批准 |
| Codex 产物讨论 | 指定 Revision 实际图片、已保存内容/Prompt/发布文案、可取得的冻结 Skill 正文；生产 fork 或独立快照起点 | 独立讨论 thread，继续讨论只 resume 此 thread；宿主保存答复，不改变生产状态或产物 |
| 用户 | UI 同源读取上述业务记录；可展开输入范围、消息活动、历史版本 | 决定质量是否满意、是否返工、是否保存共享 Skill 改动、是否人工发布 |

生产与讨论的历史不是跨选题长期记忆。用户希望以后沿用的规则，需明确保存到已绑定 Skill（已有编辑工具）；本轮不自动抽取、不暗中改 Skill，也未新增栏目规则编辑器。

### 状态与存储

- Creator/Series 的启用状态是配置，不能因为其中一个任务失败就标账号失败；列表摘要从任务投影。
- 生产 Run/Revision/Attempt 在 SQLite；调研批次仍在已有 topic-research/batches JSON；讨论消息在数据库相邻的 `*-agent-sessions-discussions/records/*.json`，工作目录保存请求、图片副本、worker 回执、答复和错误。没有为统一 UI 强行迁移旧数据。
- 账号聊天运行状态属于聊天；Codex turn 的状态只是执行证据；生产 Run 的验收/批准状态属于业务。讨论 completed 只表示答复就绪，不是返工、生产或批准。
- TASK 定义输入与交付，EVENT 保存公开活动，REPORT 决定是否得到该任务类型的有效结果；同一个 task 与所操作资源不是同一状态机。
- 讨论原始请求保留实际输入，公开 context 明确包含与排除。不记录/显示私有 reasoning 正文。UI 按活跃状态查询增量增长的回复；当前是 HTTP 轮询，不声称 SSE 字符直推。
- SDK 用量明确区分 `last_request` 与 `thread_cumulative`；fork 会继承生产累计用量，后者不能作为本次讨论用量。SDK 未给出可确认的值时标 unavailable，不伪造零用量或 turn 总消耗。

### SDK 能力限制（不能把提示词当硬隔离）

本地 openai-codex 0.157.1 支持 fork/resume 与 LocalImageInput。read_only 只限制文件写，不禁所有远程工具。讨论进程关闭 shell、web_search、默认 Apps、agents；只允许观察到 reasoning/agentMessage，遇非预期 item 请求中断并报告失败。这是降面+检测，不是事前工具白名单，不能撤销远程工具已接受的副作用。正式产物从不进入讨论可写目录；不会接收讨论回执为生产交付。

生产 fork 会继承该源线程在分支时可见的历史，SDK 当前包装没有按指定旧 turn 分支参数。因此讨论不是 snapshot-only，也不是完全重置上下文；前端“本次传入什么”明确展示此项。新主题仍不续用此讨论 thread，用户可见图片仍绑定指定 Revision/digest。

官方依据：https://learn.chatgpt.com/docs/codex-sdk 、https://learn.chatgpt.com/docs/app-server 、https://learn.chatgpt.com/docs/config-file/config-reference 。

### 2026-10-08 验证记录

- 真实 SDK 看图与续聊 2 轮通过：用正式 say/tell/speak/talk 图片的隔离副本，第一轮识别四格与中英文，第二轮同一讨论 thread 继续给建议；Run version/status/digest 不变，未生图或发布。模型建议质量不是本次验收结论。
- 本地报告 `tmp/live-discussion-g3018cfc/report.json`，不提交正式产物或线程记录。初测发现 SDK total 含源生产累计计数，已分开标注 last_request/thread_cumulative 并添加回归；初测旧报告用量不能解读为本次消耗。
- 隔离 HTTP/SQLite 回归：讨论重复请求、异文冲突、旧 digest、忙时拒绝、失败、同 thread 续聊、重启不重发、原生产状态不变；非预期工具 item 拒绝与用量标签回归通过。
- 账号工具通过实际 loopback HTTP 走讨论、历史、显式返工；等待沿用同条讨论任务，公开进度进入当前聊天。状态/故障注入采用受控 Reviewer，不声称是真模型质量评测。
- Web 前端 Playwright 全量 58/58 通过，最终补丁定向 2/2 通过，typecheck/build 通过。`studio-workflow.spec.ts` 的讨论路径覆盖生产中禁用、版本隔离/深链、成功提交与回复、422 后显式新请求、未知网络结果和成功响应损坏时先 GET 核对再恢复；断言 POST 的 `artifact_digest` 使用 `review_digest`。`agent-discussion.spec.ts` 覆盖讨论进度/答复/事件安全展示及版本链接。讨论 API 响应由受控路由提供，未触发付费模型或正式数据写入；真实 loopback API 和真实 SDK 测试另列，不能混称一条端到端真模型浏览器测试。
- 后端 13 个 smoke 通过：讨论服务/工具/loopback HTTP/任务投影、调研/SDK、账号作用域/上下文、Web 聊天/压缩/调研观察、生产交付/Run API。新工具增加固定 schema 后，旧压缩夹具历史未越过 8k 保留下限；只调整测试历史与窗口，不修改生产压缩策略。
- 生产 queued 表示等待手动执行，不计作正在运行的 worker，避免空闲任务列表不停轮询。讨论 queued 将立即由后台开始，仍属于需观察任务。
- 本机正式服务重启被宿主执行策略拒绝，没有绕过。当前旧进程仍需用户在原终端 Ctrl+C 后重新运行 `python -m creatoros.web` 才加载新增后端路由。
- 桌面与窄屏截图由上述受控 UI 测试生成并检查布局：`web/test-results/studio-workflow-workspace--7e842-d-approval-survives-refresh/discussion-desktop.png`、`discussion-mobile.png`。截图验证只证明视觉布局；操作与状态断言以 Playwright 结果为准。

## 2026-10-06：交付与恢复基线（以下为历史记录）

状态：2026-10-06 已实现并通过真实历史恢复验证。复用 Python SDK、现有调研批次、Run/Revision/Attempt，不新增调度框架。

## 本次真实问题

两次 native 生产在 Codex 完成后被宿主拒收：第一次内容稿在生成图片后补充，第二次图片做了音标修正。旧 ingest 把生产途中的预览当成不可变最终交付，报“已验收页的图片、Prompt、引用或内容不能隐式覆盖”。这不是图片缺失或 SDK 登录失败。

## 三种消息与唯一状态来源

1. TASK：宿主保存 `worker_task.json`，包含协议版本、kind（research/production）、业务 scope、输入文件引用、交付要求。Prompt 分工具编写，使用同一任务的独立目录；生产只加载已选 Skill 的冻结本地路径。
2. EVENT：消费一次 SDK stream。保存 thread/turn ID、turn 开始及 SDK 终态；公开进度由已有事件接口展示。`worker_receipt.json` 是 SDK 执行证据，不是另一个业务状态机。消息/工具活动与心跳分开。
3. REPORT：调研按 ResearchReceipt 返回候选；生产写 `work/delivery.json` 及真实图片/文案。宿主验证结果后，原调研批次变 ready，原 Run 变 awaiting_approval。页面和 Agent 读这些原业务记录。模型口头说完成、单个工具完成、文件存在都不独立决定成功。

生产中 delivery 是可修改的工作稿。每次读取完整核验路径、图片字节、顺序、引用和内容；只有有效版本替换预览，旧图片按内容摘要保留。Codex 成功结束且 complete=true、全套文件有效后冻结 checkpoint；最终验收/审批继续校验摘要，之后不得静默改稿。已取消任务不能因迟到结果复活。

调研只要求标题、切入点、理由、来源；选中候选通过现有入队逻辑成为生产输入。生产不强制 PageSpec，中间形式由 Skill 决定。结果与审核状态分开：awaiting_approval 不是发布。

## 恢复

- SDK thread 创建后立即保存 ID，turn 开始立即保存 ID；启动失败允许没有 thread。
- thread_resume 恢复上下文后创建新 turn，不恢复某条工具指令。技术恢复沿用同任务同输入/Skill；新主题/新版本新 thread。归属通过现有批次/Run输入→账号/栏目/选题，历史 thread 通过 Attempt 保留。
- 保存原 work 的最新 delivery，不用旧预览覆盖它。路径只在已知旧 Attempt 根下重定位；原始记录保留。
- SDK turn 已成功、只是宿主验收失败时，可离线重验文件并交付，不调用模型/生图；必须有本任务持久化的成功 turn 证据。旧任务需只读核对原 Codex ledger，不能根据 final 文案猜成功。
- SDK turn 失败/中断不自动当成功；保留现有显式恢复操作。交付格式修复最多一次，不自动重画。工作文件/证据变化仍拒绝不可信恢复。

## 这版的边界

保持本地单任务执行所有权。邮箱是每任务目录中的请求、事件、回执文件；不要求 Codex 轮询文件领任务，不新增 RabbitMQ/Redis。SDK 原生 steer/ExternalMessage 留作后续用户中途改要求的接口，本轮不实现。账号级统一任务列表与多 worker 调度留待下一步，现有 Web/Agent 仍通过各业务接口查询。

## 验收

- 复现共享 content.md 补稿、同页修图、更换 Prompt；生产中可更新且已冻结交付不可改。
- 无效新版本不得破坏上一份有效预览，越界/坏图/重复图仍拒绝。
- SDK失败与交付失败分别留证；完成后离线恢复不调用模型，不改旧Attempt；取消终态不复活。
- 复用用户真实失败图片在隔离目录重验，正式恢复经既有执行所有权/API完成；不重新生图或自动批准发布。
- Skill 编辑另由其最近 SPEC 记录，历史 Run 冻结文件不随 Skill 库编辑变化。

参考：OpenAI Docs https://learn.chatgpt.com/docs/codex-sdk 、https://learn.chatgpt.com/docs/app-server；用户提供的《Codex Python SDK试用》最后两轮（任务契约、结构化回执、thread恢复语义）。

## 本次验证记录

- 隔离真实历史重放 2/2：只读 SDK thread history 确认最后 completed turn、cwd 和已保存 final response；使用既有真实图片完成新 Attempt 的产物验收。测试在模型入口设置失败护栏，确认没有任何新模型或图片请求。
- 正式恢复：Run `115e51bf-c9ab-4fe2-b17b-21976d715fe8`，通过既有 `/execute` API，版本 4 → 8，状态 failed → awaiting_approval；同 Revision 新 Attempt 2 成功，耗时 516ms，用量为 0，最终 1 张真实图片。原 Attempt 1 保留失败日志。未自动批准或发布。
- 同类历史 Run `13e42ec1-f004-44a5-8249-32adf4b0e074` 已被用户取消，本轮保持 cancelled。另一条未开始的 queued 任务没有执行。
- 恢复前 SQLite 一致性备份：`tmp/creatoros-before-delivery-recovery-20261006-221628.db`。正式产物、thread history、备份及回放报告留在忽略目录，不提交 Git。
- 自动回归：smoke_worker_delivery、smoke_native_production、smoke_native_run_wiring、smoke_native_artifact_web、smoke_production_progress、smoke_studio_production_progress、smoke_topic_research_sdk、smoke_content_run_service、smoke_studio_executor、smoke_studio_run_api 通过。故障与取消验证使用隔离受控执行器；真实 SDK 验证只读，不冒充新的付费生产评测。
