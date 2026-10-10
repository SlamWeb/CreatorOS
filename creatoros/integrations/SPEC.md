# External Integrations SPEC

## 生产提交错误的对象归属（2026-10-11）

- start 已成功创建/读取的 Run 是当前请求的授权对象；execute 的 producer_busy 错误可能携带另一账号全局 active Run ID，不能转发作当前任务。客户端错误句柄始终绑定已经创建的本 Run；不更改全局执行容量、自动恢复或重新提交。
- 纯本地声明 transport 故障验证 busy 外账号 ID 被替换为本请求 Run，错误仍保留并且不生成已成功执行的入口；真实业务/模型成绩另跑，不把故障单测算 E2E。

## 讨论／调研收尾隔离（2026-10-09，完成）

- 用户明确授权代写本步；范围仅讨论与调研的诊断/收尾，不接 CI、不修改正式记录、不自动重试模型、不改生产/审批。
- 讨论 completed/failed/interrupted 的权威 record 先保存，再尽力保存 reply.txt/error.txt；用量副本同样是可选诊断。调研失败（含 preflight）先保存含失败状态的批次，再保存错误详情；Trace 与 response 副本不能遮蔽 SDK 成败。
- 调研观察器还会保存 thread/任务状态，不能整体当可选日志吞 OSError；SDK collector 提供显式关键观察器模式，调研仅隔离其 Trace 文件写入。讨论禁止工具的 RuntimeError 保持原样传播，权威 job/thread/worker receipt 保存仍严格失败。
- 验收：故障注入错误文件/回复/用量/Trace 拒写，确认原失败类型或成功结果保留、权威状态先落盘、单次 SDK 消费且不自动重提；额外注入权威保存错误必须传播。实际隔离 HTTP 读取/刷新验证同任务和原产物不变，随后统一可靠性基线。
- `tests.test_worker_finalization` 7 条 unittest 通过；含 failed/interrupted、preflight 与运行期、成功答复、真实固定 SDK collector、无搜索/坏回执、禁止工具和权威写入故障。HTTP 走实际隔离服务，模型通知/磁盘故障采用受控注入，不调用付费模型或修改正式数据。
- 新负例在进程内加载提交前的三个模块后出现 8 个失败子例与 1 个 error，确认能捕获旧实现；工作树没有回退。扩展为 15 组的统一基线全通过，证据 `tmp/reliability-20261009-125005-161671/report.json`。
- 额外 `smoke_topic_research`（实际 loopback HTTP）与 `smoke_content_discussion_tools` 通过，编译/diff-check 通过；只读子 agent 复核未发现安全/验收回退，并独立复跑新 7 条单测通过。
- TASK 的交付说明同步区分讨论 record.reply/调研 ResearchReceipt 与可选 reply.txt/response.txt。讨论副本降级仅服务日志提示；没有新增所有页面的 warning 展示、全盘不可写恢复或自动重试。

## SDK 执行与诊断故障边界（2026-10-09）

- 实际 Skill 提炼因 progress JSON 原子替换 WinError 5 经 observer 冒泡而被宿主 interrupt；不是已证实的登录/额度或 SDK 服务错误。占用者未知，不通过扩大权限或自动重发模型规避。
- ProgressWriter 对进度、用量、元数据 Trace 和可选公开活动的 OSError 记录安全降级警告；独立临时文件 + 有限 Windows sharing/access 替换重试。诊断记录不能决定业务成功/失败，其他 RuntimeError（含讨论工具禁止规则）、权威 worker receipt、真正 SDK 错误不吞掉。
- `require_completed_turn` 在 6 个适配器验收前统一检查：completed 才能继续解析/验收；interrupted 保留证据并投影中断，未知终态拒绝。尤其 native 的 interrupted 不进入 delivery repair，不重新生图。
- 提炼/改稿/试产失败处理先保存权威终态再尽力记录错误；并未承诺所有服务的失败处理已经统一。其他待治理项与测试基线见 `docs/reliability/SPEC.md`。
- 新 Windows 文件占用/故障注入 7 条单测、6 入口 completed 门槛、既有调研/讨论/生产/交付/执行器等 14 组基线末次通过；另同图真实无生图提炼通过。证据与首次失败保留，见 `docs/artifact-to-skill/SPEC.md` V6。不把注入测试视为真实产出质量评测。

## Skill 文本专用读取与 StudioClient 错误语义（2026-10-09）

- `ProducerSkillCatalog.read_skill_file(..., text_only=True)` 在既有登记/路径检查后、图片读取/解码前拒绝非文本后缀，专用 API 返回结构化 `skill_text_only`；默认 `False` 保持页面图片及既有编辑契约。
- StudioClient 统一大写请求方法，GET/HEAD/OPTIONS 的连接中断返回 `studio_read_failed`；写方法返回 `studio_outcome_unknown`，保持零自动重试。非 JSON 仍为 `studio_invalid_response`，只有写方法文案提示可能已生效，读取失败不暗示写入。
- 真实隔离 HTTP 验收见 `tests.smoke_producer_skill_read_tool`，覆盖正常文本、图片能力错误、页面原图片响应与 GET/PUT 非 JSON 区分；传输故障注入单独覆盖 GET/POST/PUT 响应丢失。
- `smoke_producer_skill_read_tool`、`smoke_producer_skill_edit`、`smoke_producer_skill_files` 通过，页面原图片字节/MIME 读取与原子编辑均保持兼容；对应编译及 diff 检查通过。未调用真实模型、修改正式库或已登记 Skill。
- 扩展回归 `smoke_agent_studio` 初次并行运行遇到其 CLI 子进程 10 秒超时，独立重跑进入第 90 行后因旧断言要求摘要不包含 `revisions` 失败；现行 2026-10-08 摘要契约已包含紧凑版本目录，此文本读取修复未改该契约或无关测试。

## Observation 公开 SDK 事件 · 2026-10-08（完成）

- 在共用 SDK stream 单消费者处补本地公开事件记录；保留原进度/交付文件，不改变模型、线程、工具或生产状态语义。
- 保存可确认的 thread/turn/item 关联与公开输入输出，排除 reasoning 正文和已识别秘密；历史摘要不补造成完整轨迹。读取不调用 Codex，不扩展登录态或全局会话访问。
- 详细契约与验证见 `docs/observation/SPEC.md`；诊断写入故障不得将正常交付改判失败。
- SDK 中断正常返回时保留 interrupted，不误记 completed；完整 item 权威，未结束 delta 以 partial 保存。捕获、原生产进度、调研 SDK、讨论和交付 smoke 通过；未启动真实生图或发布。

## 明确任务输入与版本绑定讨论（2026-10-08）

- `ContentDiscussionService` 给指定 Run/Revision/digest 保存独立讨论任务；SDK fork 生产 thread 或 fresh snapshot，后续同版本 resume 讨论 thread，实际附图。只读讨论不接收为生产交付，也不改变审批状态。协议、上下文范围、工具隔离局限和真实验证以 `docs/codex-worker/SPEC.md` 为准。
- 调研快照补当前账号身份与同栏目尚未入队的历史候选；正式选题仍保留。避免误称已看全部账号、其他栏目或账号聊天；不做跨栏目语义去重和缓存已完成结果。
- 本轮 13 个后端 smoke 通过，覆盖讨论、工具/真实 loopback HTTP、作用域、任务投影、调研 SDK 接线、压缩、聊天与生产验收；2 轮真实 Codex 看图/续聊另见协议报告。隔离测试不改正式数据，未生图或发布。

## Skill working 副本原子编辑 API（2026-10-06）

- 浏览/读取接口返回整个 working 目录 SHA-256 和 `editable`；新增 PUT 只写已登记 Skill 的安全相对路径与既有 UTF-8 Markdown/文本文件，限制 512 KiB，内置 Skill 只读。
- `expected_digest` 在更新前校验；过期返回 409/current_digest。SKILL.md 先通过现有 SkillLoader name/description 规则校验，再由同目录临时文件原子替换。`describe()` 及列表读取显示新的描述；versions 和历史 Run 快照不随 working 修改。
- 编辑 SKILL.md 时另用 `yaml.safe_load` 校验真实 frontmatter YAML，name/description 必须为满足既有限制的字符串，并确认旧单行读取器解析结果一致；多行标量拒绝。`requirements.txt` 增加 `PyYAML>=6,<7`，只用于此编辑校验，不改变其他 SkillLoader 行为。
- 账号 Agent scope 将 GET 文件树/正文及 PUT 限定为当前账号栏目绑定的 Skill。隔离 SQLite/HTTP 验收见 `tests.smoke_producer_skill_edit`。

## 调研统一 Python SDK（2026-10-06）

- 调研从 CLI exec 迁移到现有 Python SDK app-server 通道，复用 `_production_client`、`_bounded_sdk` 和单消费者 `collect_observed_turn`；共享 client 增加可选配置/执行文件参数，生产与提炼默认行为不变。
- 默认 runtime 由 SDK 管理，不再依赖 CLI PATH、git init 或项目 AGENTS；显式执行文件覆盖用公开 CodexConfig 传入。调研 fresh thread、Luna/xhigh、live 搜索、只读且禁止审批，关闭历史记忆。
- 保留原 ResearchReceipt、候选批次/正式队列边界及聊天安全进度，扩展共享进度 stage 为 research。旧 CLI 生产器仅供历史兼容；下方旧文档所述“调研仍走 CLI”已被本节替代。
- 详细验收和续跑证据见 docs/agent-studio/topic-research/SPEC.md；不做 CLI/SDK 对照实验。

## 调研执行器定位与公开活动（2026-10-05）

- 用户真实三次调研在启动阶段报“未找到 codex CLI”，不是额度或内容质量失败。默认 CLI 复用已安装 `openai-codex==0.157.1` 的捆绑可执行文件；显式 `CREATOROS_CODEX_EXECUTABLE` 配置无效时拒绝，不修改全局 PATH/登录态。使用 SDK 私有 `_resolve_codex_bin` 是有回归覆盖的兼容接缝，升级 SDK 必须重新验证。
- CLI JSONL 单消费者仍写完整本地 Trace、解析回执与用量；可选 observer 只投影公开消息、搜索、工具状态。调研窗口最多 30 条/每条 1000 字，已知凭证/本地路径脱敏，不展示 reasoning。旧失败 GET 仅投影具体安全错误，不改写旧证据。
- 相同栏目/配置/数量/归一化要求仅在任务进行中复用；不是语义去重或已完成缓存。不同调研仍受全局单 worker 限制，无自动排队；失败不自动重试。Web 原聊天观察逻辑见 web-chat SPEC。
- `smoke_topic_research_observation` / `smoke_topic_research` / `smoke_codex_producer` 通过；PATH 为空时真实捆绑 `codex-cli 0.157.1 --version` 成功。另有真实 DeepSeek → Codex `gpt-6-luna/xhigh` 联网验收，74.7 秒、1 个有来源的英语候选，原聊天显示公开活动且正式队列不变，证据 `tmp/research-chat-live-3v6w09qo/`；不生图、发布或修改正式库。

## 本地 Skill 融合草稿（2026-10-05，后端切片）

- 多 Skill 融合复用 `SkillExtractionService` 单 Skill 工作台 Job；已登记来源各自冻结摘要与过滤后的文件副本，源目录从不作为模型工作目录。目标草稿自包含安全来源资源，并保留可编辑/改稿/试用/显式保存的既有门禁。
- 文件总量上限 2,000 项/32 MiB；链接/越界内容拒绝；隐藏敏感项过滤；白名单外文件类型显式拒绝。来源规则冲突由模型要求写成待人工决定项，不由宿主静默合并。
- `tests.smoke_skill_merge` 使用隔离 SQLite/HTTP 与受控提炼器通过：快照、摘要、资源保留、改稿上下文、幂等、仅显式保存入库和原件不变；包含符号链接外部零写入、改稿删除来源后恢复、实际当前 frontmatter 能力检查。
- 真实 SDK 隔离探针 `python -m tests.live_skill_merge --run` 通过，gpt-6-sol/high，281.3 秒，thread `01a10c35-200f-7b12-877b-4359405fe634`；生成 `bilingual-word-carousel` 的 5 个文件，包含两份来源说明/两张参考图且原件摘要不变，未入库、生图或发布。证据 `tmp/skill-merge-live-y57mg83w/`。此前一轮失败原因是探针 mkdtemp 根目录私有 ACL 导致沙箱不能穿越父目录；补既有 inherit_copy_permissions(root) 后重跑，不扩大正式目录权限。此例证明实际融合文件链路，不证明所有 Skill 组合内容质量。

## 已安装 Skill 文件浏览读取（2026-10-05）

- 目录只接受已登记 ID 的当前 `working/<id>` 副本或固定内置 Skill；读取复用严格路径/重解析点检查，不经 `describe()` / `locate()`，不修复副本、不执行脚本、不访问任意本地路径。
- 列表返回 Skill 元数据及受限相对文件树（最多 500 个文件、2,000 个目录/文件项），每项包含 `path/kind/size`；隐藏目录、Git 元数据和凭证/密钥/数据库文件不暴露。超出边界或发现可见路径链接时拒绝整个读取。
- `.md`、UTF-8 源码/文本可按需读完整内容，单文件最多 512 KiB；仅列出的 PNG/JPEG/WebP/GIF 后缀可请求图像预览，实际内容还必须解码为 PNG/JPEG/WebP/GIF，限制 16 MiB、40M 像素、最多 100 帧，响应 MIME 跟随解码格式。扩展名和真实编码不一致时仍提供原始字节与真实 MIME；损坏图拒绝。其他类型返回 415，超限返回 413；现有分页 `/content` 契约保持不变。
- `tests/smoke_producer_skill_files.py` 使用隔离 SQLite/工作目录覆盖长于 4,000 字的完整正文、嵌套脚本与文本、正常 PNG、JPEG 编码但命名 `.png` 的原始字节/真实 MIME、损坏图拒绝、unsupported、UTF-8、路径逃逸、缺失/损坏元数据、链接拒绝及 GET 前后数据库/受管文件树无改写；无模型调用或正式 Skill 修改。
- 常见 Python/JS/TS/Shell/SQL/CSS/HTML 源码只以 JSON 文本返回；补 `.js` 与 `.html` 真实 HTTP 用例检查 `application/json` 与完整源码。新文件浏览 smoke、原分页 smoke 均通过；前端实际操作/失败修复与截图见 `web/SPEC.md`。

## 账号会话 StudioClient 接线（2026-10-04）

- Client 可接收宿主固定的 agent_session_id，以请求头传到同一 Studio API；账号绑定从服务端本地会话读取，模型不能通过工具参数选择另一会话。
- 未提供会话 ID 的 CLI/普通 UI 保持原全局契约；旧总览会话同样兼容。地址限制、禁重定向、超时与写入不自动重试保持不变。
- 业务数据仍 SQLite，会话仍 JSON；不触碰 Codex SDK 配置、生产 thread、Skill 工作副本或登录凭证。详情与证据见 `docs/agent-studio/web-chat/SPEC.md` P1。

## 产物提炼 Skill（2026-10-02）

- 2026-10-05 V4：已确认真实 Mind 提炼被旧宿主 180 秒时限中断；提炼/改稿默认 600 秒，可通过 `CREATOROS_SKILL_EXTRACTION_TIMEOUT_SECONDS` 设置 30–1800 秒。四段 Prompt/model 不改，无自动重试。单 SDK stream 以可选 observer 保存公开消息/工具输入、结果及错误到任务 `public_events/`；默认生产元数据 Trace 不变，reasoning 不保存。具体错误分类及旧任务只读投影、隔离故障注入和真实同图 Mind 探针见业务 SPEC。

- 2026-10-03 V3：按用户四段短 Prompt，让 Codex 直接写本任务 `draft/skill|mind|visualize/`；不显式绑定 skill-creator，不向模型暴露内部角色，也不要求 JSON 草稿。SDK cwd 仅本次 draft，workspace-write；宿主读取完整目录，沿用版本校验与确认入库。配套文件随编辑、改稿与试产快照保留。此设计替代以下历史 V2 的“JSON 返回、宿主创建文件”接线。

- 2026-10-03 工作台 V2：默认完整 single；图文/纯文案输入、版本化编辑、Codex 改稿、显式草稿试产与入库解耦。试产直接复用 native-v1 Producer，以冻结本地路径/SkillInput 使用未注册草稿，正式数据零写入；Mind 不复制视觉示例，完整/呈现 Skill 保留原图 assets。文件浏览/试产记录与 Agent 工具共用 extraction API。暂不换 IP、不自动生图/入库、文字制作尚无试产适配器。范围及分层验证见最近业务 SPEC。

- `SkillExtractionService` 接收参考图，fresh SDK thread 按用户要求使用 gpt-6-sol/high（gpt-6.1-sol 在本机 ChatGPT SDK 调用返回模型不支持），使用 LocalImageInput 分析作品；四模式直接写文件，host 核验并保存可预览版本，显式确认才复用 register_local 入库，不改全局 Codex Skill 或栏目。
- 请求幂等、有限期限（当前以 V4 为准）、取消/重启中断、原始回复与安全进度记录沿用本地持久化风格；不自动重试模型或生图。制作类 Skill 的图片产物能力声明由宿主补齐以兼容既有栏目绑定，不增加 PageSpec/格数约束。
- 完整接口、隔离测试与真实图片输入证据见 `docs/artifact-to-skill/SPEC.md`。

## 双 Skill 的轻量组合检查（2026-10-02）

- native-v1 双 Skill 先在生产 thread 做只读文本检查，再由同一 thread 产出；Mind 的内容需求、制作 Skill 的视觉规则与跨两者的分页/分格分开处理。单 Skill 不增加这一轮。
- 默认偏好自动适配；互斥硬要求保存为 needs_input 并抛出 `skill_composition_needs_input`，停止后续生产，不走图片索引修复。沿用已有返工入口接收澄清。短结论保存在 checkpoint 和可读文件，技术恢复复用，旧 checkpoint 兼容。
- 完整范围、软/硬约束边界和真实 gpt-6-luna/xhigh 文本验证见 `docs/single-thread-production/SPEC.md`；本轮未生图或改正式数据。

## 单 thread 原生文件生产（2026-10-02）

- 新 Run 保存 native-v1 协议：同一个 SDK thread 显式加载选中的一份或两份本地 Skill；不再强制 PageSpec 或按 Skill 名称/GitHub 仓库白名单接线。详细边界、验收与真实试产在 `docs/single-thread-production/SPEC.md`。
- 中间内容可以自由组织，只要求 `work/delivery.json` 描述最终真实图文件与生产器报告的 Prompt。宿主核验本 thread 图片、冻结 Skill、内容证据并保存 checkpoint，继续交付 SocialContentPack。
- 同 Revision 技术恢复 resume 原 thread 并核验旧文件；新 Run/新 Revision fresh thread。至多一次无生图的索引修复；宿主不自动重试图像生成。提示词禁重画是软约束，不冒充 SDK 硬性工具预算。
- 下列历史双阶段/逐页协议仍服务缺少 production_protocol 的旧 Run，不覆盖旧证据，也不自动把旧任务迁移成新流程。旧 receipt_recovery 明确拒绝 native-v1。

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
