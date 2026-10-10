# Web 宿主复用 Agent Loop

## 冻结基线后的调研结果契约（2026-10-11，本地验证完成）

- E08 原始 Codex 输出、落盘批次、raw 工具返回与模型投影均实际 8 条，请求为 10；不是投影截断。E09 正确工具 URL 被最终回复改写。原基线失败保持不动。
- 模型视图将请求上限明确为 requested_count，实际候选数为 returned_count；保留全部真实候选，不截断、不补造。业务 HTTP DTO、原始账本及 Trace 不变。
- 两个调研工具说明实际数量、不足如实交付、unknown 不等于失败或远端已停止、链接原样使用 url；不增加自动重提、模型重试或坏例专属答案模板。
- 新增隔离真实 SQLite／loopback HTTP／工具／请求快照验证，覆盖实际 8／10 条、failed／unknown。相关 29 项与两个 smoke 通过；联合本地验证另见 Eval SPEC。这些不是模型成绩。
- 同一冻结 12 题／13 槽将从真实网页用 DeepSeek 全量回归；E07 数据正确但解释矛盾未加专属补丁，单轮改善也不宣称根除。生产、发布和正式运营数据不在本轮修改范围。

## 账号对话契约收敛（2026-10-11，完成）

- 用户授权本轮代写：默认中文、简短业务结论及必要链接；不主动展示 ID、digest、技术字段或复述协议。用户明确问技术细节时仍可展开。
- 权限拒绝不列出无关目录；没有实际验证不得编造 ID 格式错误、记录归属或故障根因。账号范围仍由持久会话和 API Guard 决定。
- 模型工具参数按当前账号收敛，模型可见结果与原始结果分开记录；Trace 展示两者，不把原始技术字段重新塞回普通聊天。
- 验收路径：账号浮动聊天 → 查询/明确入队/拒绝越权 → 真实 DeepSeek 与 SQLite → 业务回复 → 刷新 → Trace。幂等重放故障另标受控，不冒充模型主动重复调用。
- 原 SPA、真实 DeepSeek 连续三轮浏览器验证均保留：前两轮英文开场/越权拒绝重复当前账号 ID 不合格；最后一轮四条请求、七次模型调用完成，入队只写两条 Topic/一份回执，刷新与 Trace 阅读零新增请求。账号任务工具实际使用 `{}`，由宿主绑定账号；七份请求快照与实际 Provider 输入逐项一致。
- 相关 Python 88/88、真实隔离 HTTP/SQLite smoke、Trace 浏览器回归 1/1 与 typecheck/build 通过。模型行为不是永久保证；最后任务回答仍补充一项相关的 ready 状态，不宣称所有文案已最短。完整步骤、失败迭代及复跑见 [契约结果报告](CONTRACT_RESULTS.md)。

## Skill 改稿坏例修复 · 2026-10-09（完成）

- 正式会话 Trace 确认实际模型为 deepseek-v4-flash：两次文本/目录读取成功，第三次读取 JPG 误走混合内容接口导致 studio_invalid_response；未调用 update，文件未保存。英文回复与“已确认根因”均不是 Codex 视觉分析结果。
- 宿主显式规定中文进度/回复，图片路径不等于视觉输入，不将文件内容薄弱猜测成已确认根因。明确修改并保存允许说明共享影响后直接更新；用户只要预览仍不写。
- 栏目/Run 来源的改稿草稿携带业务身份及作品版本，优先走版本绑定讨论获取图片依据；不新增通用看图/任意文件读取工具，不让普通查看启动模型或写文件。
- 编辑读取必须先列目录、再指定目标文件读取全文与工作副本 digest；默认分页正文不返回编辑摘要，禁止猜测或沿用原件摘要。旧会话在下一轮照既有机制更新宿主规则，无需删除历史。
- 真实探针三次记录均保留：152647 实际中文保存成功，但旧 path 断言不适配默认分页读取；152953 暴露英文进度及无摘要时模型猜值，首次 CAS 被拒后重新保存成功；明确编辑读取与中文草稿后 153316 全部验收通过（get/list_files → get/path → update，一次保存，11,373 输入+输出 tokens）。报告均在 tmp/live-producer-skill-edit-20261009-*，只改隔离 Skill；一次成功不是模型永不出错的保证。
- smoke_web_agent / smoke_account_sessions、Skill read/edit/files 三个真实隔离 HTTP smoke 通过；图片负例返回 skill_text_only。新增 UI 手递账号与历史 Revision、草稿刷新/零自动发送通过。仅规范已有讨论工具的选择，没有在本轮重新调用 Codex 做看图或生图质量验收。
- 扩展 smoke_agent_studio 仍有既有陈旧断言（要求 Run 摘要不含 revisions，与 10-08 契约冲突）；未更改该无关测试，也不宣称全仓回归全绿。

## 调研在原会话等待终态 · 2026-10-05（完成）

- 真实坏例：用户提交英语栏目调研后聊天结束，后台因服务 PATH 找不到 Codex 立即失败，原会话没有失败反馈。修复启动链路及任务观察，不自动重跑旧失败批次，不入队、生图或发布。
- Web 调研工具提交一次，由宿主有界等待同一批次；每秒读取已有 GET，公开活动更新当前工具行，终态才将候选/安全错误返回模型。不是模型重复轮询或重提。CLI 未配置观察回调时仍保留后台提交语义。
- 观察/刷新不取消、不重提。读失败或等待超时保留批次 ID，明确任务状态待查询；服务关闭结束等待并修复未知结果，重启不重放付费动作。
- 单会话单请求，最多四个不同会话并行；各自 Provider、账本、Trace、活动与保存节流互不共用。调研执行器仍一次一个，不自动排队；同配置、同栏目、同数量与归一化要求的进行中调研共享任务。
- UI 复用既有工具行，展示真实阶段、最近实际活动、可展开公开消息/工具摘要与同批次链接；失败直接在原对话可见。心跳不充当模型活动，不公开内部 reasoning、凭证或本机路径。候选就绪不代表入队。
- 验收：隔离 HTTP/SQLite 覆盖等待期间 running、终态候选回到模型、失败回到工具/聊天、重复提交、两会话共享同批次/不同会话隔离、关闭/重启/读故障零重提；真实低频 Codex 联网候选与 DeepSeek 原聊天链路另证。浏览器 1440/390 操作与刷新零 POST。
- `python -m tests.smoke_research_chat` 与更新后的 `smoke_web_agent` 通过（含最多 4 会话、第五条拒绝）；账号 sessions/scope/context、reply_trace、studio_api/composition_tools/runtime_context 回归通过。受控 Provider/Researcher 专门覆盖竞态与故障，不代替真实服务证明。
- `python -m tests.live_research_chat --run` 使用真实 DeepSeek `deepseek-v4-flash` + Codex `gpt-6-luna/xhigh`；74.7 秒、单一研究提交、真实搜索事件和 1 个英语候选（borrow vs lend / 牛津词典来源）、终态结果回到同一聊天，隔离 Topic 为零。证据 `tmp/research-chat-live-3v6w09qo/result.json`，聊天 `61be7ea9-eee5-4a56-8135-fbc39adc40c2`，批次 `4c20431eb7734343adcc2e4285b592db`；没有重跑用户旧批次或改正式数据。
- CUA 实际打开这份隔离真实结果，检查公开活动、回复 Trace 两次主请求/步骤 1 的 ready 工具结果，刷新仍是同一批次；没有额外模型请求。可用 `python -m tests.live_research_chat --serve tmp/research-chat-live-3v6w09qo` 在 8896 只读复查；新的真实探针必须显式 `--run`。
- 限制：归一化仅大小写/空白/NFKC，不保证两个模型把同一句话改写成不同工具参数后仍去重；未实现跨进程调研队列、自动恢复模型执行或长时无人值守重试。生产仍在 Run 页面异步观察。


## 回复级 Trace · 2026-10-05（完成）

- 本轮只做聊天诊断，不实施账号工作区的整页重构。完整回复底部提供复制原文和 Trace 两个图标；Trace 打开只读 dialog，不重新调用模型或工具。
- 复用现有 Context Trace，Web 新请求将宿主 request_id 作为 turn_id，关联本条用户请求的多次主调用和自动摘要。每次预算/压缩/外置后的实际 ModelContext 单独保存请求快照，含 system/developer、目录树、摘要、近期消息及 tools schema；不是用当前 messages.json 重建过去。这不是 HTTP 报文、模型内部思考或 Codex 生产子线程 Trace。
- 快照保存在当前 Web Session 的 messages.request-trace/，记录模型回复、工具调用参数与实际返回的模型可见正文、失败/未知状态；主 Trace JSONL 仍只保存诊断元数据。CLI 默认不启用正文快照。已知环境凭证和敏感键脱敏，不记录传输 headers/原始异常；本机上下文内容是用户明确请求的诊断例外，不上传外部平台。
- 新 GET /{session_id}/turn-trace/{turn_id} 返回该轮请求索引；GET /{session_id}/turn-trace/{turn_id}/requests/{request_id} 按需读取一份快照。路径只能从会话和严格 ID 推导，拒绝跨会话/跨轮/符号链接；no-store。主聊天 SSE 不携带完整上下文。
- UI 按实际调用顺序选择 Step（摘要单独标记），展示用量/预算/压缩和上下文、模型回复、工具参数/结果。长文本默认展示短预览，显式展开全文、收起；不执行 HTML 或加载其中的图片。加载/空/失败/重试明确，切换会话关闭 Trace，迟到响应不能串入另一条回复。
- 历史回复没有请求 ID/原文快照时提示未记录，不补造；流式输出未完成不展示成功回复操作。复制以原始 Markdown 为准，权限失败不假成功，Trace 可用键盘打开/Escape 关闭并恢复入口焦点。
- 验收：隔离真实 SQLite/HTTP 验证两轮/多 Step/同名 tool call ID/重启/跨会话拒绝/旧记录/缺损快照/脱敏；确定性 Provider 用于故障与压缩路径，真实低成本 DeepSeek 只读账号查询验证请求快照与 Provider 输入一致；浏览器 1440/390 的复制、Trace 选择、长文展开收起、错误重试/切会话、刷新零 POST。不调研、生图、安装或发布，不改正式运营数据。

### DeepSeek Harness 参考与取舍

- 只读研究本机 `D:\DeepSeek Harness\deepseek-harness`，再核对官方当前 [架构](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)、[Session](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/session.md) 和 [Trajectory UI](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/client/ui-trajectory/src/client/TrajectoryView.tsx)。本机 clone 是旧快照，不能把它当成官方最新版本。
- 借鉴 turn → step → tool call/result 的关联、模型请求可追溯、概览与按需 inspector 分层、阅读与原始消息分开。官方当前 system prompt 来自 `system/message` 历史，`request/header` 只承载调用配置/默认值/工具 Schema，不是完整 HTTP headers。
- CreatorOS 保留现有 Ledger、C3 元数据 JSONL 和逐请求正文 sidecar，不迁移成完整 Event Sourcing、统一 seq、压缩日志、虚拟化 ledger 或外部观测平台。回复弹窗选择主/摘要请求，详情分上下文、模型输出、工具；请求统计折叠，阅读默认展示真实换行，原始消息保留完整字段；参数和结果按 call ID 配对。
- Trace 是诊断证据，不是恢复或重放按钮。快照写入失败不能使已经成功的业务工具被错误地判为失败；缺正文/缺结果如实标未记录或未知。记录保留在本机，占用空间随调用次数增长，本轮不增加清理策略；已知凭证脱敏不等于通用敏感信息识别。

### 验证结果与复跑

- `python -m tests.smoke_reply_trace` 通过：隔离真实 HTTP/SQLite 两轮多 Step、重复 call ID、错误结果、无 StreamEnd 的部分回复、finish_reason=length 的截断、自动摘要请求的独立正文、跨会话/轮次拒绝、缺文件/坏 JSON/错误结构、脱敏、重启、历史记录和 CLI 默认不保存正文。快照写入是 best effort，不把已完成的工具操作变成失败。
- `python -m tests.live_reply_trace` 真实 DeepSeek 通过：两条只读指令、3 次主调用（各 1/2 次），唯一工具为 get_producer_skill；记录的 messages/tools 与实际 Provider 输入逐项相同，账号范围正确，零 ContentRun/零 Producer 调用。使用临时数据库/会话，不是任务质量 Benchmark。
- 后端关联回归通过：smoke_web_agent、check_context_trace、smoke_account_sessions、smoke_agent_events、smoke_account_context、check_step_compaction；compileall 通过。CLI 默认事件序列和原预算/压缩/外置策略保持不变。
- `npm --prefix web run build` 通过（保留既有大 chunk 提示）；设 CREATOROS_PYTHON 为 deepcode 环境后，`npm --prefix web run e2e -- reply-trace.spec.ts account-chat.spec.ts agent-layout.spec.ts` 4/4 通过。受控响应仅验证 UI 确定性/故障路径：原文复制、单份快照按需读取、统计折叠、阅读/原始消息、三页签、call ID 配对/未知结果、重试、切会话、旧记录、键盘和 390px 溢出。
- CUA 在独立 8879 服务实际发送一条 DeepSeek 只读 Skill 查询：2 次模型请求及真实 tool result 可见；复制原始 Markdown、长文展开、切换 Step/页签、Escape 关闭及入口焦点恢复通过。390×844 下 document scrollWidth=390；正式 8765 服务与运营数据未操作。验收临时服务已停止。
- 实际截图保存在忽略目录 `tmp/testresults/reply-trace-real-tools-20261005.jpg` 和 `tmp/testresults/reply-trace-real-mobile-20261005.jpg`；受控桌面/手机截图在 web/test-results/。旧回复不能补录；更新后重启 Web，发送新指令才会产生正文快照。

## P2 账号上下文树 · 2026-10-04（完成）

- 用户确认默认上下文层级为账号 → 栏目 → 绑定 Skill，Skill 只展示当前本地文件的 name/description；单 Skill 和 mind+production 组合均支持，共享 Skill 元数据去重。不注入正文、assets、选题队列、生产历史或其他账号。
- 账号使用已有字段，不新增账号定位/目标/偏好表。栏目定位、受众与 Skill description 有确定性文字上限并标注省略；用户明确暂缓大量栏目筛选/分页，当前展示全部栏目，仍计入既有总预算。
- 使用简洁账号角色指令替代账号模式下的总览安装/提炼说明。用户给目标时可以主动查询并提出建议；查看/建议不写入，不因一句“今天做什么”启动生产。宿主作用域和服务端校验继续执行。
- 树是当前 SQLite + 本地 Skill 的只读投影，仅进入 ModelContext，不追加 messages.json，不改变 checkpoint 的源摘要。每条用户请求构建一次，成功业务写工具后刷新；执行写操作仍由业务服务校验当前状态。失效 Skill 明确标记，不自动修复、不阻断其他查询。
- 账号 Agent 可按需查询共享库元数据来组成新栏目；明确要求查看 Skill 时，受管理 Skill 正文可由专门只读分页工具读取，不放开 Web read_file 任意路径。调研 Codex 可读 Mind；生产 Codex 仍按现有本地路径/快照读取实际组合。
- 全部实际投影计入预算；压缩规划为动态树预留输入空间，不把树交给历史摘要。Trace 记录本次实际树、查询时间、字段省略及独立 token 估算。本机业务树为这一诊断记录的内容例外，不记录 Skill 正文或 assets。不新做 Trace UI。
- 范围只覆盖 Web 账号模式；总览/CLI 保持原投影行为，不增加自动长期记忆、自主调度、发布或新的 Agent 框架。
- 验收：隔离数据库/Skill 库/会话验证树归属、去重、当前本地修改、坏 Skill、文本省略、全部栏目保留；账本/checkpoint 不受刷新影响，动态预算和 Trace 一致；成功写后刷新。真实低成本 DeepSeek 只读查询和显式 Skill 回读，不启动调研、生图或发布，不改正式运营数据；P1/Runtime/工具回归。

### 数据流与实现位置

1. Web 账号会话固定 creator_id，`CreatorContextBuilder.build` 读取当前 SQLite 的账号及其全部栏目；按绑定 ID/已登记路径读取本地 SKILL.md 根部元数据，不扫描资产或恢复工作副本。树仅含现有账号字段、栏目定位/受众/revision/状态、绑定引用与去重 Skill 元数据；自由文本上限 1000 字符，省略记录 original_chars/shown_chars。
2. `AgentChatService` 使用独立 ACCOUNT_INSTRUCTIONS；Loop 的可选 context_factory 每条 query 构建一次，成功 compose/update/queue/start/research 后的下一 Step 刷新。查询期间复用同一 as_of，不监控其他页面正在做的修改；写操作由原业务服务实时校验。
3. `build_model_context` 将数据块放在固定前缀和滚动摘要之后、近期原文之前，不改 messages.json。压缩/硬预算外置后的重建仍带树，摘要输入无树；省略/超限不修改数据库或原始消息。
4. Main Trace 的 account_context 保存实际树与 as_of/omissions，estimated_parts.account_context 单独统计且总和与全请求估算一致。用户可经已有 Session context-trace API 检查；未做新 UI。
5. 显式 `get_producer_skill` 经共享业务接口只读分页正文，供用户查看规则；工具实现可跨入口复用，但总览/CLI 没有账号树或新的账号模式。外层通常无需正文；研究与生产执行器的 Mind/单 thread 本地 Skill 读取沿用既有逻辑，本轮未重新生成内容。
6. 新账号 Agent Run 在创建前检查绑定当前元数据；文件缺失/无效返回 409，未创建任务。已有 Run 不重新检查当前工作副本，继续使用原冻结输入；普通 UI/总览的原生产入口未改变。

### 验证结果与复跑

- `python -m tests.smoke_account_context` 通过：隔离真实 SQLite/Skill 文件含单/双绑定、路径与 ID 去重、元数据修改刷新、105 个以上栏目无目录截断、长文标记、坏 Skill；确定性 Provider 仅验证 Runtime 数据投影/故障路径（不冒充任务能力评分）。覆盖成功写后刷新、账本/checkpoint 原文不变、压缩源无树、主请求/Trace 有树及超大树不发送主请求；隔离 HTTP 检查缺失 Skill 的新 Run 409、零任务/零生产、只读继续可用。
- `python -m tests.smoke_producer_skill_read_tool` 通过：真实隔离 HTTP、账号会话、登记/内置 Skill 连续分页、任意路径拒绝、缺文件/非法 UTF-8 不写回。未触发 Codex。
- `python -m tests.live_account_context` 真实 DeepSeek 通过：两条只读 query、3 次主调用，唯一工具 get_producer_skill；首次直接根据宿主树回答账号/栏目/Skill，第二条显式读取前 200 字符。每次树估算 254 tokens，input 总计 13,978 / output 332 / cache hit 8,704；另一账号未进入请求，零 Run/零 Producer 调用。夹具为合成账号，临时会话随测试清理；这是接线样本，不是运营决策或内容质量 Benchmark。
- 关联回归通过：smoke_account_sessions、smoke_account_scope、smoke_web_agent、smoke_runtime_context、smoke_agent_studio、smoke_studio_composition_tools、smoke_studio_api、smoke_auto_compaction、check_context_trace、check_step_compaction、smoke_context_protection、smoke_compacted_model_context；compileall 通过。仅后端改动，无前端视觉或浏览器验收声明。
- 新只读工具使旧自动压缩夹具的固定 6000-token 窗口装不下完整 schema，最初被正确预算阻止；调整测试窗口为至少 schema 估算＋1000 内容空间＋输出预留，仍要求 30k 字符旧结果触发一次摘要且保留近期请求。未放宽正式预算/断言，重跑自动压缩及 Trace 通过。
- 下一步先用真实账号问“今天做什么”，观察查询和建议质量；跨会话偏好/目标记忆、批量栏目筛选、反馈分析能力和 Trace 面板仍暂缓。生产仍由用户明确授权，不能把本轮上下文接线称作自动运营闭环。

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
