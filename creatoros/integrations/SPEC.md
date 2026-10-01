# External Integrations SPEC

## 单视觉 thread 的逐页交付（2026-10-01，完成实现与分层验证）

- 实际故障：Claude Code 主题 Run `76fbc0f9-2952-4a84-aa10-219fcf23320d` 生图与 SDK turn 均完成，7 个 headline、发布 title/body 是空格；旧 visual_prompt 禁止交付这些字段，VisualReceipt 却强制要求，导致整组拒绝。另有资源路径以 Attempt 为基准而不是制作 Skill 为基准。不是超时。
- 维持一篇一个新 Mind thread、一个新 Visual thread。Visual thread 内先定稿整套 Prompt，再按页执行多个 turn；不是每页新建会话。每页成功立即由宿主核验图片、保存 Prompt/来源/字节摘要和 checkpoint；缺少最终整组 JSON 不再丢失全部图片。
- 图片交付与展示文案解耦：逐页只交付 order / source_image_path / image_prompt / reference_assets / warnings，宿主从原内容生成展示标题与草稿发布文案，不要求视觉模型再抄写内容。资源只归一化确实存在于冻结制作 Skill 的路径。
- 技术恢复只选同 Run、同 Revision 的上个 Attempt；输入与两份冻结 Skill 的 digest 一致才导入其 storyboard/已核验 checkpoint。新 Attempt 仍用新会话，显式给它本次内容与已完成页；人工新 Revision 默认不复用旧图。
- 逐页 turn 不自动审美重画；宿主最多一次无生图回执修复，阶段总超时保持有限，技术中断保留已核验页。提示词禁重复生图是软约束，不宣称能拦截 Codex 内部所有 imagegen 调用。宿主不自动重试付费生图。
- UI 只显示 checkpoint 核验过的部分图片，明确未验收；显示 current_page / completed_pages / phase，服务 heartbeat 和 SSE 连接不冒充生产进展。中断或失败不自动批准/发布。
- 验收：隔离 transport 故障注入覆盖晚页失败、同线程多 turn、跨 Attempt 缺页恢复、篡改/路径越界/乱页拒绝、仅一次 JSON 修复和时间/取消边界；真实 SDK 文本多 turn 验证与既有真实图片只读复用检查。此轮不重画整套内容、不写正式账号资料。
- 本节取代下方历史“技术恢复重新生成整套”约定；每个新 Attempt 仍新建会话，恢复用明确、校验过的内容/图片文件，不依赖旧 thread 记忆。额度耗尽发生在有效 checkpoint 后时，允许用户恢复；没有自动重试或绕过配额。
- 复核补两项边界：返工 Revision 的技术恢复沿用第一次 Attempt 保存的 previous_pages 输入，不把新产出当输入造成 digest 漂移；SDK initialize/thread-start/turn-start 都受阶段总期限和取消信号保护，enter 失败也显式 close 以释放 RPC 等待者，close/interrupt 有 5 秒有限等待。故障注入覆盖初始化卡住与启动取消。
- 回归通过：smoke_visual_checkpoint、smoke_production_sessions、smoke_production_progress、smoke_content_run_service、smoke_studio_executor、smoke_codex_producer、smoke_pair_production、smoke_studio_partial_cards、smoke_receipt_recovery；篡改图片/内容/输入与错误 Prompt/引用被拒绝，已完成页可在失败后复用，最多一次回执修复。
- 真实无生图探针 live_visual_thread_protocol 通过：同一新视觉 thread 两 turn 记住随机标记，gpt-6-luna/xhigh，input 67,932 / cached 48,384 / output 470 / reasoning 154；证据 tmp/visual-thread-probe-1sjxq0l3。不等于新版整套生图质量已验收。
- 启动限时改动后再次真实文本验证通过：thread 01a0f81d-f97d-7461-b311-595b6ccee2df，两 turn，input 67,901 / cached 48,384 / output 498 / reasoning 220；证据 tmp/visual-thread-probe-qklo24c1。同样未生图、未联网调研或改正式库。
- 正式 Claude Code Run 的 7 张原图经维护导入进入新 Revision 2 待批准；原失败记录保留，未重新生图/批准/发布。代码新生产行为只做隔离故障测试、真实 SDK 文本与已生成图片验证，本轮没有重画新完整图组。
- 测试记录：最初并行大量 smoke 时 0.6秒 lease 夹具的 heartbeat 版本断言一次失败，独立及后续顺序重跑通过；未据此修改生产 lease。恢复测试草稿文案断言已更新为明确草稿标签后通过。首次 npx E2E 用系统 Python 缺 rich，指定 CREATOROS_PYTHON=deepcode 后通过；不计作产品链路成功。
- 说明及架构图：docs/production/production-chain.md。画图调用内置 imagegen，不能指定或确认“2.5”模型标签。

## 生产可观测与已完成回执恢复（2026-10-01）

- 已确认故障：数据库主题 Run `5d51415b-ce24-4a98-83a1-09e61bee7ab4` 实际完成 11 张图片，但视觉阶段重复抄写 PageSpec 时把“沿调用流程”写为“按调用流程”，整组被逐字比对拒绝。不是生图仍在运行。
- 第一步：视觉输出改为 VisualReceipt，仅拥有页码、实际 Prompt、参考资源和图片；宿主用原 Storyboard 按序 join 成兼容 PairReceipt。缺页、乱序、重复图片、缺参考仍拒绝，原内容稿/审批摘要不放松。每阶段最终原始回复先保存 response.txt，即使后续 schema 失败也保留。
- 第二步（完成）：单消费者 SDK stream 记录安全事件元数据，并投影当前阶段/最近活动到 Run 页面；执行 heartbeat 与观察连接不冒充实际生产进度。不输出模型内部思考、原始工具正文或敏感参数，不造百分比。
- 后续独立步骤：逐页回执 checkpoint 与缺页恢复、并发 2 的渲染调度。此处尚未实现，不把指示模型并行等同宿主有可靠并发；本轮恢复原图不调用生图或批准/发布。
- 既有原图恢复采用显式人工核对的回执、原冻结 Skill 和原内容，通过现有 Run guard/Revision/验收服务写新版本；原失败 Attempt 不覆盖。恢复是否实际执行与结果单独补录。
- 第一步验证：`smoke_production_sessions`、`smoke_pair_production`、`smoke_codex_producer` 通过；覆盖内容原件 join、额外改稿字段拒绝、缺页拒绝、最终回执保存与既有审批摘要。故障注入无费用，不冒充真实新生图。
- 原图恢复已完成：维护工具 `receipt_recovery` dryrun + 显式 version/hash 接受后，现有数据库主题 11 张原图进入新 Revision 2 待人工批准。原失败记录保留，原内容不改，11 个 HTTP 图片 URL 返回 200；隔离恢复负例、原图字节和其他 Run 审计通过。没有重新生成或自动批准；详情见 runs SPEC。
- SDK stream 使用固定依赖 openai-codex 0.157.1 的私有 collector 作为单一兼容接缝；原始 Notification 原样交给它，以保留 final_answer 选择、最终 usage 和失败语义。升级 SDK 必须重跑 probe；没有一边 run() 一边消费第二条 stream，也不复制 SDK 的整套 collector。
- Progress sidecar 原子覆盖，trace 追加仅白名单事件/角色/工具类型与 ID、时间；大输出不整体序列化，delta 仅更新活动时间且限频写文件。工具调用计数不代表已完成图片数；视觉阶段知道内容页数，但当前没有逐页完成率。
- 真实文本探针 `live_production_progress` 通过：gpt-6-luna/xhigh，thread `01a0f7ad-4d64-7830-9e68-15039fd7b005`，input 33,457 / cached 16,128 / output 286 / reasoning 162；观察到工具/消息开始与完成，无联网调研、生图或正式库写入。证据 `tmp/sdk-production-progress-5zxn02bf`。仅验证 SDK 观察链路，不代表新版整套图已重跑。
- `smoke_production_progress`、`smoke_production_sessions` 通过单消费者、final/usage、去重计数、隐私过滤、失败/取消/超时；Studio HTTP 安全投影和既有 Run/Executor/Artifact 回归通过。Web 10 项隔离 E2E 通过，详见 web SPEC。

## 独立生产 session 与双 Skill 分阶段（2026-10-01，完成）

- 用户确认：Mind 只调研并交付内容 pages；视觉 Skill 只负责 IP/视觉化。SDK 默认生产不再一个 turn 同时注入两个 Skill。
- 每次 SDK 生产 Attempt 均新建 session；双 Skill 是两个新 session：内容阶段仅 Mind，视觉阶段仅制作 Skill 与完整内容文件。不 resume/fork 旧 thread，不继承运营对话。
- SDK 子进程配置关闭 `memories.use_memories` 和 `memories.generate_memories`，不改变全局设置/登录态。每阶段最小规则禁止读取其他任务/全局记忆；这不等于 OS 级完全隔离文件系统。
- 同时关闭项目开发 AGENTS 自动注入（`project_doc_max_bytes=0`）。保留原生 SkillInput，并明确给出本阶段冻结的 SKILL.md 路径；真实验证发现未登记的原生 Skill 引用不一定展开为模型可读路径，不能只传名字让模型去全局搜索。
- 内容 JSON / 可读 Markdown 在生图前保存；视觉请求为“@制作Skill 用这个视觉 Skill 把下面整套 pages 可视化”，并保留实际参考图/Prompt/图片回执。连续叙事不与单页工具调用混淆。不把历史 Prompt 中“不依赖前后页”复制进新任务。
- 重试/返工重新执行当前输入与明确返工要求，不依赖旧 session 隐式上下文；需要前版内容的返工只显式带本 Run 已保存的内容文件，不跨 Run 检索。历史图片/证据不覆盖。
- ContentRun 的 thread ID 表示当前 Attempt 视觉 session，阶段 Trace 另保存内容 session；新 Attempt 可绑定新 ID，同 Attempt 拒绝意外换 ID。冻结 Skill/digest/审批/原有单 Skill回执保持兼容。
- 验收：隔离测试阶段 Skill/输入边界、不同 Attempt/阶段不同 thread、故障前保存内容、回执改写/缺页拒绝、取消/超时；真实 SDK 无生图验证不同 thread、禁用记忆配置和内容阶段 handoff。完整真实生图暂不启动，不修改正式运营数据。
- 官方配置与 thread API： https://learn.chatgpt.com/docs/config-file/config-reference 、 https://learn.chatgpt.com/docs/app-server 。
- 隔离验收通过：`smoke_production_sessions`、`smoke_pair_production`、`smoke_codex_producer`、`smoke_content_run_service`、`smoke_studio_executor`、`smoke_studio_artifacts`、`smoke_studio_run_api`、`smoke_producer_skills`、`smoke_series_guards` 共 9 项。覆盖阶段输入、全新 ID、返工显式前稿、同 Attempt ID 冲突、取消/超时、失败后内容保存、视觉改稿拒绝，以及 handoff 文件与审批摘要一致性。传输 Mock 仅用于故障注入和本地边界，SQLite/HTTP 使用隔离夹具。
- 真实 `gpt-6-luna/xhigh` 无生图探针通过：Mind `01a0f6a8-490d-7363-bd1e-ad9c4b081ece`，Visual `01a0f6a9-d344-7290-a3c1-498f3956372b`。会话记录无注入 memory summary；两阶段实际读取各自冻结 Skill，内容交付两页“消息队列是什么 → 生产者与消费者交接”，视觉仅确认收到整套 pages。证据：`tmp/sdk-session-probe-9e4f0220f4d2`（忽略、本地保留）。这只验证接口与边界，不代表整套面试内容质量或最终图片已验收。
- 前两次探针虽返回结构合法 JSON，实际是“找不到 Skill”的占位页，判定失败；最终加入明确 Skill 路径后重跑成功，探针增加实际 Skill 读取与非占位页数断言。不能把 schema 合法等同内容完成。
- 下方早期“同 Run 同线程恢复/只补最后一页”的建议已被本节替代；SDK 重试与返工均重开，不隐式复用旧图或会话。旧 CLI resume 仍兼容保留；历史快照、原 Prompt 和正式运营数据未修改。

## 消息队列新版真实生产（2026-10-01，未完成：账号额度限制）

- 用户明确要求重新生成：受众为完全零基础，范围为从零基础到能够应付 AI Agent 岗位消息队列面试。新建独立选题/Run，沿用“小白带你学AI”的本地 mind + 小白制作组合，不复用旧 Run 的 Skill 快照，也不覆盖旧批准产物。
- 通过既有 ContentRepository / ContentRunService / CodexSdkProducer 执行，使用 gpt-6-luna/xhigh 与原登录态；不限制页数，不自动批准或发布。完成后验证逐页 PageSpec/Prompt/参考资产/图片对应、产物摘要与内容递进，记录实际结果。
- 首次真实调用发现 Windows 权限接线遗漏：TemporaryDirectory 的私有 ACL 随重命名留在冻结目录，宿主能读，但 Codex 沙箱账号被拒绝。发布新副本后重置为目标父目录的继承权限，仅作用于新副本；不改变源文件、模型权限或整个仓库 ACL。补回归与真实读取验证后继续同一生产任务。
- 已验证：真实 gpt-6-luna/xhigh SDK 在只读沙箱下读取两份冻结 Skill 返回 `SKILL_READ_OK`；pair/producers/Codex/Run Service 四项 smoke 通过，pair smoke 增加 Windows ACL 继承断言。未降低生产沙箱权限。
- 新 Run：`eb90cfb9-8329-47a1-96d2-599ecf579d60`。Revision 1 因文件读取被拒，最终回执缺有效角色参考，落为 `invalid_production_receipt`，没有通过验收的图片。修复后通过既有返工机制创建 Revision 2，续接同一线程继续；旧已批准 Run 不变。
- Revision 2 / Attempt 1 实际生成 9 张图片后触及默认 30 分钟超时；Attempt 2 在同线程恢复，单次时限延长至 60 分钟并要求复用已有图片，恢复逐页证据时触及 `codex_usage_limit`。服务提示 3:09 后重试；没有更换账号/模型/API，也不自动等待或重试。
- 当前数据库状态 `failed`、`retryable=False`、version 11；SDK thread 为 `01a0f33b-f1b1-7691-9d67-a6ddec6d0f07`。执行所有权 journal 已释放，旧 Run `4d4f6bc6-22ab-401a-86f5-7644c9b457bb` 仍为 `approved`。
- 已有 9 张原生 PNG 按线程中记录的页序复制到新 Run 的 `partial-review/01.png` 至 `09.png`，逐一 SHA256 比对通过。仅为未完成预览，不生成伪造 Manifest，不进入批准/发布。完整 PageSpec 和实际 Prompt 仍在该 SDK 线程的本地 session ledger 中；10 页完整回执与末页尚未完成。
- 额度恢复且用户确认继续后，通过既有返工机制创建下一 Revision，再执行同 Run、同线程、同冻结 Skill；说明只补第 10 页、复用现有 9 页并重建完整回执。当前错误不是 retryable，不能直接宣称普通 resume 一定可执行。没有修改默认超时或错误分类。
- 本轮暴露的真实痛点：图片生成已完成不等于 CreatorOS 已获得可验收产物；最终回执前中断需要从线程记录恢复证据。本次不宣称逐页 checkpoint 已实现，也不宣称质量验收或端到端生产通过。

## 可编辑本地 Skill（2026-10-01，完成）

- GitHub 仅为导入来源；`versions` 保留导入原件，`working/<目录条目>` 是实际调用的可编辑本地目录。现有目录 ID 作为路径别名兼容 Web/Agent，已登记的 working 绝对路径也可绑定。修改正文/描述后无需重新安装或改绑。
- 首次执行 Run 时读取当时的工作副本并冻结到 Run 的 `skill-snapshot`；后续 Attempt/Revision 使用同一快照。历史 Run 优先沿用已有 Attempt 的冻结文件，缺失时只允许使用匹配原 digest 的导入原件。
- 当前本地 knowledge-to-storyboard-deep 改为简短教学指导：受众/范围由输入决定，真实问题推动循序渐进的讲述，页数自适应，交付逐页定稿与来源。宿主移除 6–12/首次6页/自行缩题要求。
- 验证：`smoke_producer_skills`、`smoke_pair_production` 通过隔离 Git/SQLite/HTTP 检查本地编辑、路径绑定、首次执行读最新、恢复保留旧版及历史快照。`smoke_codex_producer` 检查单 Skill 原生输入指向 Attempt 冻结副本；受控 Producer 只用于故障与接线验证，不冒充真实生图。
- 关联回归：`smoke_content_run_service`、`smoke_series_guards`、`smoke_studio_composition_tools`、`smoke_studio_executor`、`smoke_topic_research`、`smoke_studio_artifacts`、`smoke_studio_run_api`、`smoke_studio_api` 通过，合计 11 项。旧 JSON 无新字段仍可读取；Python compileall 与 git diff --check 通过。
- 只读重验正式消息队列 Run `4d4f6bc6-22ab-401a-86f5-7644c9b457bb` 的 Revision 2：产物摘要仍匹配数据库；原 evidence、原 Mind Skill 与导入原件 SHA256 和改动前相同。不重写历史快照。
- 本机工作副本 `data/producer-skills/working/knowledge-to-storyboard-deep--039c5af915d13784/SKILL.md` 已精简，Skill 格式验证通过；工作副本属于忽略的本地数据，不推送到上游仓库。本轮不调用模型、生图或发布，不变更正式运营数据库。

## P4 接线与 GPT-6 Luna（2026-09-26）

- 双 Skill 图片轮播适配契约、回执与验收见 `docs/studio/composition/SPEC.md` P4；保持单 Skill 路径不变。
- 生产/调研新建与恢复统一改用 gpt-6-luna/xhigh（用户指定）。历史验证中记录的 5.6 模型不追改。
- `openai-codex` 固定升级到 `0.157.1`。实测旧 `0.147.0` 捆绑 runtime 只列出 5.6 系列，真实生产返回模型不支持；同一登录态用新 SDK runtime 可列出并调用 GPT-6 Luna。未修改用户全局配置、未降级模型。
- `python -m tests.live_codex_sdk_model`：真实 SDK 新建与 resume 均以 `gpt-6-luna/xhigh` 返回 READY；无工具、生图、业务数据写入。调研继承 CLI 的统一模型参数，`smoke_topic_research` 验证命令与原调研契约；此检查不代表重新跑过联网选题调研。
- 双 Skill 通过两个原生 SkillInput 传入，宿主保存冻结的 Skill、逐页内容/Prompt 和轮播包。Prompt 为生产器报告值，不能宣称已核对图像服务内部最终 Prompt。SDK 的本地 codex_trace 目前仅完成后写 thread/turn/usage 摘要，不是完整内部工具轨迹。

## 栏目调研（2026-09-09）

- topic_research 复用 Codex JSONL/Schema/进程树，live web_search，候选批次与 Topic 分离。所有 Codex-as-Tool 新建/恢复均显式 gpt-5.6-luna/xhigh，不改全局配置。
- 真实调研及边界见 docs/agent-studio/topic-research/SPEC.md。

## 本机 Studio 客户端（2026-09-07）

- 2026-09-08：Web/CLI 复用客户端；返回摘要明确 cancelled/approved 是不可恢复或返工的只读终态，后续动作以 allowed_actions 为准。真实对话中的旧提示误导已记录为 badcase，见 docs/agent-studio/web-chat/SPEC.md。

- 新增 StudioClient，目录查询及生产委托已有 FastAPI API；默认 http://127.0.0.1:8765，可由 CREATOROS_STUDIO_URL 覆盖。
- 本机 HTTP、30 秒超时、不跟随重定向、不读取代理环境、不自动重试写入。连接失败提示启动服务；提交响应未知保留已知 Run ID，先查询再由用户决定。
- start 创建/取回幂等 Run 后，仅执行首版、无 Attempt 的 queued 任务。不回退直接 Codex，不隐式恢复或执行返工。
- 实施与真实验证见 docs/agent-studio/SPEC.md。

## 本轮目标：Codex Content Producer

- 把已登录的 Codex CLI 作为端到端图片轮播生产工具，通过 `codex exec --json --output-schema` 获取结构化生产回执。
- 一篇内容新建一个可恢复的 Codex thread；CreatorOS 保存 `thread_id`，后续返工可按该 ID resume。
- Codex 只返回卡片内容与生成图片的源路径；CreatorOS 负责复制图片、写入最终 `social_content_pack.json` 并使用 `SocialContentPack.load()` 验收。
- Tool 只接收 Creator/Series/Topic 身份与题目，固定使用 `knowledge-to-carousel` Skill；不让模型自行选择栏目或 Skill。

## 当前边界

- CLI 可前台同步等待；Studio 通过 `ManagedRunExecutor` 在单独线程中提交生产，HTTP 只返回已持久化的 Run 状态。JSONL 出现 `thread.started` 时仍立刻持久化句柄；中断与技术重试由 runs 模块管理。
- Producer 支持向指定 Revision/Attempt 目录生产，并用 `codex exec resume` 续接同一篇内容的 thread。
- 执行器只接收一个显式任务；忙时返回当前 Run，不自动排队或调度。
- 不实现长期栏目会话；每个新 topic 使用新 thread。
- 不信任 Codex 返回的最终身份字段，也不允许它指定任意目标目录。
- Producer prompt 接收 `topic_brief`、`series_description` 和 `audience`；老的 `produce_to` 调用不传这些字段时保持兼容。
- 本轮 smoke 隔离 JSONL 解析与落盘；低频真实 `codex exec` 另行验证。

## 验收

- 能解析 `thread.started`、最终结构化 agent message 与 `turn.completed` usage。
- 图片只能从 Codex 生成图片目录读取，复制后生成有效 `SocialContentPack`。
- `produce_content_pack` 进入 Tool Registry，结果包含 pack 目录、Manifest 和可恢复 thread ID。

## 真实验证

- `live_codex_producer=passed cards=6`：真实 Codex thread `01a05ef3-c515-7600-b6d9-57f02ac34473` 生成 6 张图片，CreatorOS 复制后重新加载 Manifest 通过。
- 本次 usage 为 input 371,066、cached input 287,232、output 7,608；完整 JSONL 和图片不进入 Agent messages。
- 使用保存的 thread ID 执行 `codex exec resume`，WebSocket 重试后自动回退 HTTPS 并返回 `RESUME_OK`；续接请求为 input 47,341、cached input 46,720，证明同篇内容可恢复且前缀缓存命中。
- 真实画面逐张检查通过：中文清晰、风格一致、机制/原因/对比/处理顺序完整；未执行平台发布。
- 全量 42 个 smoke 中 41 个通过；唯一失败的 `smoke_routing_projection` 会真实连接 PersonClone，当前本地 8000 端口未监听，未用 Fake 掩盖该外部依赖失败。全包 `compileall` 通过。

## S4 验证与子进程边界（2026-09-04）

- `produce_to` 接收可选停止 Event 和进程生命周期 callback；旧 Tool 参数保持兼容。完整 JSONL 保存到 Attempt 目录的 codex_trace.jsonl，HTTP 仅暴露 trace 是否存在。
- Windows 先以 suspended/no-window 启动进程，加入 kill-on-close Job Object，记录 PID/创建时间后再恢复执行；超时、宿主关闭和异常均只回收本次 Job 内的进程树。POSIX 用独立进程组，尚未进行 Linux 集成验收。
- `smoke_studio_process` 在本机 Windows 通过真实无费用子进程故障注入；`smoke_codex_producer` 的纯解析/落盘回归通过。
- 收尾运行 `live_codex_resume_protocol`：真实新建与 resume 共 2 次请求，同一 thread `01a06b41-bb2d-7332-958c-4a064019eee0` 通过，不调用生图。该旧探针未打印 usage，不能据此报告 token 数为 0。
- 本阶段早期还曾从隔离页面提交真实生产并停止；没有把它算作完整生图验收。最终页面耗时/故障 QA 使用隔离受控 Producer，完整真实图片生产留到 S7。
## 生产 Skill 安装与动态接线（2026-09-08）

- producer_skills 复用 Codex JSONL/output-schema/进程回收，安装用独立 workspace-write；真实 Git 文件核验后登记版本。生产 Prompt 和 Manifest 改为实际 skill_name，保留内置默认兼容。验证见 docs/agent-studio/producer-skills/SPEC.md。

## Python Codex SDK 内容生产（2026-09-09）

- ContentRun/`produce_content_pack` 默认使用 `CodexSdkProducer`，通过本机 Codex app-server 的 Python SDK 通信，不再为内容生产依赖 `codex` 可执行文件出现在 Web 后台的 PATH 中。
- 每篇内容仍创建或恢复一个独立 Codex thread；thread/turn 显式使用 `gpt-5.6-luna` 与 `xhigh`，并设置 `deny_all + read_only`，避免生产器隐式修改 CreatorOS 工作区。
- CreatorOS 对已核验的 Skill 继续保存 commit/digest 版本；SDK 路径用原生 `SkillInput(name, path)` 注入 Skill，不再把完整 `SKILL.md` 复制进 Prompt。ContentRun 和 Manifest 仍记录实际 Skill 版本。
- SDK 复用本机 ChatGPT 登录态（Plus 账户），不要求 CreatorOS 配置 OpenAI API Key。`CodexProducer` CLI 实现暂时保留给 topic research 与兼容路径；两条路径都遵守同一 receipt schema 和图片目录隔离。
- SDK 目前落盘紧凑的 thread/turn 摘要 Trace，尚未声称与 CLI 的完整 JSONL 事件等价；真实生图、取消和 resume 仍按 ContentRun 的隔离 smoke/人工验收逐项验证。

### 真实 SDK smoke（2026-09-09）

- 使用隔离安装的 `openai-codex==0.147.0` 真实调用本机 app-server：账户身份返回 `chatgpt / plus`，请求显式携带 `gpt-5.6-luna`、`xhigh` 和本地 `SkillInput`。
- turn 在服务端返回官方 usage-limit 错误，未进入生图；CreatorOS 已将该错误映射为 `codex_usage_limit`，不伪造 ContentRun 完成。额度恢复后再做真实图片产物验收。

### SDK 接线复核（2026-09-10）

本轮 Studio 验收计划：在 deepcode 安装已声明 SDK；使用 tmp/sdk-studio-qa 独立 SQLite/输出目录和 8879 服务，从浏览器创建账号、栏目、两张图选题并 Preview/确认，再开始真实 SDK 生产。检查切页/刷新不重复 Attempt、产物图片可读、批准为未发布；复用现有故障测试检查错误和版本冲突。不写正式运营库。

- 修复父类初始化覆盖 `CodexSdkProducer.native_skill_inputs` 的问题；现在 SDK Prompt 不重复嵌入 Skill 全文。纯本地回归断言覆盖该行为。
- 真实 `gpt-5.6-luna / xhigh` 文本请求返回 READY，账户仍为 ChatGPT Plus。随后在 tmp 隔离目录运行两张 HTTP 404 图片的生产验收，不写正式运营库。
- 真实生成两张 PNG，逐张打开确认中文清晰、暖纸手绘风格一致。首次探针人为设置的 240 秒超时在回执前触发；正式默认仍为 1800 秒。续接同一 thread `01a0874d-8d26-7063-931d-9b0e255a6982`，复用图片补齐回执成功，复制图片及 `SocialContentPack.load()` 通过。
- 证据目录：`tmp/sdk-production-20260910-015318-resume`，包含 images、social_content_pack.json、production_session.json、codex_trace.jsonl。此次验证生产器及 resume，不等于 Studio 数据库/UI 全链路验收。
- `tests/live_codex_producer.py` 默认改走 SDK，可通过 `--backend cli` 验证旧路径；`smoke_codex_producer` 通过。SDK 仍在 tmp 隔离依赖目录，deepcode 环境尚未安装。

### Studio 真实联调（2026-09-10）

- 在 `tmp/sdk-studio-qa` 独立 SQLite/输出目录启动 8879 服务，浏览器真实走通创建账号、创建栏目、手动添加选题、Preview、确认入队、开始生产、打开 Run 和刷新页面；刷新没有创建第二个 Attempt。
- 该 Studio Run 的生产请求被 Codex Plus usage limit 拒绝，Run 持久化为 `failed / codex_usage_limit`，页面保留可解释错误且没有批准入口；本次不重试、不写正式运营库。
- 已在此前独立 Producer 验收真实生图与 resume；本轮 Studio 只补真实 UI 状态与失败持久化证据。内置 Codex image tool 的当前官方文档标注为 `gpt-image-2`，CreatorOS 没有指定或声称使用 GPT Image 2.5；`gpt-5.6-luna/xhigh` 是执行模型配置，不是底层生图模型。
