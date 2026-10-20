# ContentRun SPEC

## 已验证隔离交付回填原选题（2026-10-21，正式离线回填通过）

- 用户授权在空闲时重启服务，将60506acd原输入真实重放所得8图离线恢复至原Run页面；同Revision新增Attempt，旧失败Attempt、错误与文件不变，不重新生图、不自动批准。
- 回填前核验冻结请求/Skill/归属、完整Checkpoint及成功执行回执、产物哈希；使用本地执行独占锁、最新Run及版本/lease门禁，复制到正式新Attempt后重验。数据库、队列、事件一致且同源重复操作幂等。
- 先隔离副本故障/幂等验证，再在确认生产、调研、提炼、讨论与聊天任务均无进行/排队项后停服备份、正式回填并重启；正式验证以原页面8图、待验收状态、旧失败与原图哈希保留为准。新Skill版本和生产模式不在本轮范围。
- 新 `offline_adoption` 仅本地显式CLI/domain入口，无网页任意本地路径导入；12项隔离回归覆盖成功/幂等、输入/图片/归属篡改、版本/最新Run/租约/移除/取消/队列门禁及复制失败。首轮一处TopicRemoval夹具缺少必填字段已补，未改产品迁就夹具。后续单Skill摘要和参考组校验的主路径另通过。
- 正式停服前检查SQLite生产/调研/发布状态、68份任务与会话JSON以及执行日志，均无活跃/排队任务；7条Run queued没有生产队列授权，不误认为正在执行。PID27488确认身份后停止，SQLite备份与quick_check完成。
- 原Run同Revision新增Attempt2，8张图片与来源逐字节一致、状态awaiting_approval、旧失败Attempt行/旧事件/18份旧文件哈希不变。本次模型调用0、生图0；真实重放费用留source_usage。数据库仅预期6张表改变；证据 `tmp/maintenance-20261021/recovery-verification.json`，备份同目录 `before-recovery.sqlite`。服务重启与网页验收另记。

## 生产环境故障与内容冲突分开（2026-10-20）

- `codex_environment_error`是可显式重试的生产技术故障，不要求改Skill或提出内容返工；真正`skill_composition_needs_input`仍需明确选择。
- 新Attempt重查blocked的前置检查，旧Attempt/原响应不改写；先检查已验收、已接收和当前thread未索引图片，任何已有图片证据均禁止按“零张”直接重生图。
- 历史已误存为不可重试的Run不自动迁移或悄悄改分；本次旧任务采用冻结输入隔离重放验证，不声称正式页面已恢复。

## 选题级五工位并发（2026-10-19，隔离验证通过，正式加载待空闲）

- 用户明确要求最多5个选题并行，不把一篇拆为多个thread；首次生产创建会话，技术恢复续接本选题会话，普通小修续用原会话，从头重做才读取最新Skill并新开会话。
- 延用SQLite明确提交队列和单Studio数据库独占锁；执行池默认5、环境变量CREATOROS_PRODUCTION_CONCURRENCY可选1–5。调度器按FIFO填满空位，独立owner/取消信号/lease/目录/日志，禁止同一选题并行写入。
- 执行日志支持多个本实例已登记owner；旧或未知执行记录仍阻断启动/恢复。普通失败只结束该项，执行者不明或结果无法持久化则暂停新派发，已运行的其他项继续。关机同时通知所有执行者，不自动重画中断任务；pending保留。
- 当前正式8765 PID33336有用户任务运行，本轮不停止、不重启、不写正式数据库或执行日志；新代码仅隔离验证，加载需另行确认空闲。
- 验收：隔离真实SQLite/文件与故障注入覆盖5重叠/第6等待、补位、防重复、失败隔离、定向取消、并发关机/重启、旧脏日志及原thread连续性。真实Codex测试只用独立目录，失败原样保留，不算历史Eval成绩。
- 已实现：调度器持有活跃topic直到Future实际退出；业务中断先落库但旧进程尚存时，重做排队，其他topic可绕过。全库OS锁保持，执行日志兼容旧single与多owner，未知/篡改/丢失日志拒绝继续。关机先通知所有任务、状态写入有界等待，所有等待共享预算；存在活跃写者/未清日志则保留锁。
- 118项联合回归通过：12并发/关机故障、10旧串行语义、14多owner、5原HTTP批量、8会话连续性、51收图/交付恢复、9fresh领域、3fresh HTTP、6宿主契约。原studio_executor/studio_process/native_run_wiring/native_production/native_artifact_web五组smoke通过。证据tmp/parallel-concurrency-final-unit-20261019.log，未调用付费模型，非真实生图E2E成绩。
- 首轮31项有1处关停重复抛已记录的DB错误，后续39项有2处（新活跃topic门禁使旧用例预期过时、同时取消时有worker先被误标failed）；分别修复清洁退出的错误处理/旧测试前提/cancel状态处理，再按上列完整组合回归，不覆盖旧日志。旧串行测试显式配置max_concurrency=1，不删除原断言。
- 本轮未运行5个真实Codex生图任务，避免与用户现有生产争用连接/额度；应用层5路重叠和恢复已经隔离验证，服务端实际限额/生成质量仍需空闲后另行真实验收，不保证5倍速度。
- 额外真实loopback验证通过：同一隔离服务Web提交4题、账号工具适配器提交3题，总共只有5活跃+2等待；重复工具提交复用原Run，7题最终均待验收且各1 Attempt，刷新HTTP/数据库一致。tests.test_parallel_production_api单独1/1通过，证据tmp/parallel-concurrency-http-final-20261019.log；使用本地可阻塞Producer，不经过LLM/真实生图，不冒称浏览器E2E。

## 参考作品直接制作（2026-10-18）

- Topic.reference_id 解引用不可变任务参考组，Run/Revision保存task_references，Attempt独立references目录；按当前Skill直接制作，不先另调模型生成候选。原图真实作为SDK LocalImageInput，默认只参考内容，呈现由显式选项控制，不写共享Skill。
- 参考组ID与每图hash纳入native输入/证据/Checkpoint校验，旧无参考输入digest不变；有图不能退回legacy协议丢图。直接入口复用持久化串行队列，人工验收/发布门禁不变。设计、恢复/隔离与真实模型证据见 `docs/topic-references/SPEC.md`。

## 同一选题按当前 Skill 从头重做（2026-10-17）

- 不把“从头重做”伪装为旧 Revision 的技术恢复。显式请求创建同一 Topic 的新 Run / Revision 1，重新读取当前栏目与 Skill 绑定，使用独立输出根目录；不复制旧 thread、页文、Prompt、讨论或 checkpoint。
- `restart_fresh(source_run_id, expected_version, request_id)` 校验来源所见版本、当前最新 Run、账号/栏目启用和 Topic 未移除；仅已结束/暂停的来源可重做，排队和执行中不能重做。外部导入没有生产 Skill，拒绝该操作。
- 请求标识持久保存为新 Run 幂等键，CREATED 事件保存来源 Run/version；相同请求响应丢失可返回同一 Run，不同要求冒用标识拒绝。保存本身不执行，仍须经过既有持久化队列显式授权。
- 历史 Run/Revision/Attempt/文件保持原样；旧 Run 不可再次执行、返工或取消，以免覆盖同一 Topic 的当前状态。历史批准仅改自己的验收记录，领域层仍安全保留；网页可统一只读旧历史。
- 同一 Topic 的当前 Run 按 created_at（而非最后活动时间）确定；普通 create 返回最新记录，不因默认旧幂等键又跳回首篇。显式自定义幂等键仍按原请求重放。首次执行仍按既有契约冻结当时的当前 Skill 文件；新 Run 从不使用来源 Run 的冻结目录。
- 验证：`tests.test_fresh_content_run` 9/9 通过，覆盖请求并发/重放、CAS、活跃/归档/外部导入门禁、结束状态、单/双 Skill 当前绑定与 assets、独立目录/空旧 thread/空前稿、旧文件与事件不变、旧 Run 不能改当前 Topic。`tests.test_production_queue` 10/10、`smoke_content_run_service`、`smoke_producer_skills` 通过。均为隔离 SQLite、本地 Skill 与故障夹具，没有调用模型或写正式数据；不能当作真实图像质量测试。
- 集成另验证 `tests.test_fresh_production_api` 3条真实 loopback HTTP/SQLite 用例及工具、作用域、链接等联合35/35通过；浏览器重做覆盖响应丢失后同一 request_id 重放、新 Run 一次执行授权、刷新零重提、同 Topic/旧图不变。旧 Run 页面禁止删除本 Topic/发布新内容入口，防止历史操作影响新制作。
- 真实 CUA 前端发送自然语言重做→DeepSeek 一次 start_content_run(mode=fresh)→实际 Codex 生图通过：新 Run `22eb3de8-3ef4-4bc4-890f-2b6399085bc6`，新 thread `01a14a06-0b00-7590-bdce-7b4435006914`，1个 Revision/1个 Attempt/1张图片，最终 awaiting_approval，队列 succeeded。请求 previous_pages/revision_instruction 均空、最新 Skill digest 与旧记录不同；图片 HTTP200，无批准/发布。耗时约2分钟，证据与用量保存在 `tmp/fresh-live-probe/run-372295dfb7/report.json`，不是 Eval 分数。来源失败记录由隔离夹具播种（不是历史生产成功声明）；正式 SQLite、Skill 与产物未修改。

## 显式批量生产持久化队列（2026-10-17）

- `ManagedRunExecutor.submit` 从容量满即拒绝改为持久化授权后返回；单项 execute 与批量入口共用该方法。新增 `ProductionQueue` 保存当前 Run/Revision 的明确提交，旧 `Topic.queued` 或 `Run.queued` 不补授权、不自动生产。
- `production_queue_items` 是调度回执而非另一套生产状态机：pending 等待、running 已派发，完成时依据 Run 得出 succeeded/failed/cancelled/needs_attention。Run/Revision/Attempt、lease、执行日志和产物校验继续是执行/验收权威。
- 单消费者依 FIFO 串行消费；只有执行当前项时才 claim/new Attempt，不让排队项提前占 lease 或冻结首次执行 Skill。已接受请求的响应丢失重试不会追加 Attempt；同一 Run/Revision 唯一，原 expected_version 或当前 version 可识别正在排队/执行的原请求，其他过期 version 拒绝。失败/中断重投必须显式携带当前 version，仍受原 retryable 门禁。
- 普通生产失败记账后继续下一项；外部执行者未确认退出、dirty journal 阻止继续派发，不因想清空队列越过独占锁。取消待执行项不会调用 Producer；直接业务状态变化在实际 claim 时再次检查。
- 消费者因执行所有权不明、数据库读取/错误落盘失败而暂停时，pending 保留原 FIFO/授权；`blocked_reason` 对外可见，尽力将原因保存到 pending 的 last_error。已暂停或意外死亡的消费者拒绝新提交，不返回虚假的 accepted；数据库不可写时仍保留内存暂停状态和日志。新实例只有通过原 guard/recovery 后才清除等待项的暂停标记，继续未开始任务，不自动恢复 running。
- 重启只自动消费 pending；历史 running 先经过既有 `recover_inflight`，已落盘 validating 可以离线重验为待批准，其余保留 interrupted/needs_attention，绝不自动重新生图。崩溃恰好发生在 dispatch 标记与 claim 之间也保守等待显式恢复。关机时未开始任务仍为 pending。
- `queue_snapshot(run_id)` 提供当前 Revision 的调度状态、待执行位置、时间和错误；无参只返回 pending/running/needs_attention，用于可观察性，不改变业务 Run version。
- 不改人工批准，不增加自动发布、定时发布、多容器、多 Worker 或自动付费重试。
- 验证使用隔离 SQLite 与无费用本地 Producer 进行失败/重启注入，不能作为真实 Codex 图像质量验收。`tests.test_production_queue` 覆盖迁移零漂移、旧排队不自动执行、FIFO/失败继续、重复提交、pending 重启、inflight 显式恢复、已交付离线验收、取消及 dirty journal；`tests.smoke_studio_executor` 已按新排队语义更新并通过。
- 真实模型集成：`tmp/live-batch-production-1deebc2ec51b/report.json` 通过。HTTP 账号会话的真实 DeepSeek 一次 start_content_run(topic_ids) 提交两条；真实 Codex gpt-6-luna/xhigh 串行执行，各1 Attempt/独立 thread/1张最终图片，图片 HTTP 均200，两条均 awaiting_approval，未批准或发布。不是浏览器真实模型 E2E，不计入旧 Eval 或成功率统计；两张最终卡不代表原生生产内部绝无有界图像修订。
- 首轮失败报告 `tmp/live-batch-production-q54tuu4z/report.json` 保留：测试目录 mkdtemp 在 Windows 下私有 ACL 导致 Codex sandbox 无法读 Skill/写交付，并非队列丢任务；第一条失败后第二条仍自动执行。只将 opt-in 测试脚本改为普通 mkdir 继承 tmp 目录权限，未放宽系统 ACL 或改变生产 Runtime；重新冻结后一轮通过，不覆盖原失败。

## 移除选题的生产门禁（2026-10-09，完成）

- create、claim/恢复与 request_revision 在权威事务中检查 TopicRemoval；已移除拒绝新生产/返工，不改原 Run 状态、version、审批或已有文件。移除与本机 executor claim 共享 `_write_lock`，生产/验收进行中不能移除。
- 排队但尚未认领的 Run 可以归档，之后 claim 会拒绝，不暗中取消或改写旧状态；历史只读接口和 Observation 仍可访问。验证只在隔离库，完整契约见 web/storage SPEC。
- 实际隔离 HTTP 回归通过：活跃生产/验收/讨论移除被拒、旧 execute 与新返工被拒，未调用 Producer；归档前后 Run/version/事件/revision/图像 SHA256 不变，详情和 Observation 可读。没有改变生产 lease、审批状态或正式任务。

## Native 单 thread 协议接线（2026-10-02）

- 新 `ContentRunService` 默认将 `production_protocol=native-v1` 固化进 Run/Revision 输入；调用方可显式传 `legacy`。未含协议字段的历史 JSON 仍解析为 `legacy`，返工沿用原输入快照。
- 原生双 Skill 组合标记为 `native-carousel-v1`，要求 mind/production 角色和有效 Skill 文件，但不再按 Skill 名称、GitHub 来源或角色图资源白名单筛选；旧 `pagespec-xiaobai-carousel-v1` 门禁保持不变。本地 Skill 的 `github_url`、`commit` 可为空。
- 服务仅对 native-v1 producer 传协议标记和当前 Revision 明确冻结的 `previous_pages`，不给它注入 Run 上记录的旧 thread 或隐式历史内容；同 Revision Attempt 的交付 checkpoint 恢复由原生产模块负责。旧协议 producer 参数保持原状。
- Native 新 Revision 仅从同一 Run 前一 Revision 已验收的 native checkpoint 读取 delivery 文案和每页 content/image_prompt，不复用图片路径；同 Revision 重试读取首个 Attempt 冻结的 `previous_pages`。前一版本摘要变化或缺少有效交付时拒绝空上下文返工。
- Native 生产前置 Skill 组合审查若记录 `skill_composition_needs_input`，Run 保持 failed 且不可原样恢复；保存用户澄清只创建 queued Revision，不触发 Producer。后续显式 execute 可在前序 Revision 有完整、路径受限且无 pages/delivery 的 `needs_input` checkpoint 时，把澄清作为新 Revision 输入并冻结 `previous_pages: null`。缺失/无效 checkpoint、已有交付之外的普通缺失产物仍拒绝空上下文；同 Revision 的技术重试可沿用该已冻结 null，仅在前序澄清证据仍有效时放行。
- Skill 组合审查无法解析有效结果（`skill_composition_invalid`）没有可供用户选择的冲突结论，允许用户显式恢复同一 Revision 重新执行审查；`skill_composition_needs_input` 仍不可原样恢复。
- 服务在产物验收与批准时都从 Revision 的不可变输入读取协议。`native_delivery_failed` 作为生产阶段可显式技术恢复的错误类型记录。
- 验收方式：隔离 SQLite、本地任意名称且无 GitHub/character.png 的双 Skill、受控 Producer；不调用真实生产 API、不写正式数据库。
- 最近验证：`smoke_composition_run_flow` 通过，覆盖冲突错误不可原样恢复、缺失 pre-image checkpoint 拒绝澄清 Revision 空上下文、保存澄清不执行、显式新 Revision 收到澄清并冻结 null、timeout 后同 Revision 重试继续使用冻结 null，以及无效审查结果允许显式同 Revision 重试；仅隔离 SQLite 与受控 Producer。
- 最近验证：`smoke_native_run_wiring` 通过，覆盖任意本地双 Skill 冻结、legacy 白名单拒绝、native 前稿提取及 retry 输入冻结、历史输入默认值、native 参数隔离与 `native_delivery_failed` 可恢复；`smoke_native_artifact_web` 通过。Legacy 回归 `smoke_content_run_service`、`smoke_pair_production`、`smoke_producer_skills`、`smoke_series_guards`、`smoke_receipt_recovery`、`smoke_studio_executor`、`smoke_studio_partial_cards`、`smoke_studio_production_progress`、`smoke_studio_run_api`、`smoke_visual_checkpoint` 均通过；默认 native 创建回归 `smoke_studio_api`、`smoke_topic_crud` 通过。以上均使用隔离目录和本地受控依赖，没有真实生产 API 或正式数据库写入；`py_compile` 与 `git diff --check` 通过。

## 单视觉逐页保存与显式恢复（2026-10-01）

- 双 Skill 单视觉会话按页交付，技术失败后通过既有 execute 新建同 Revision Attempt；仅复用输入及冻结 Skill digest、原内容、视觉计划、图像 SHA256 全部一致的前 Attempt checkpoint。人工返工新 Revision 不复用旧图。本节取代下方历史的“技术重试整套重新生产”描述。
- 有效 checkpoint 后的交付失败为 visual_delivery_failed，可由用户点恢复；中断沿用原状态机。无 checkpoint 的 schema 错误仍不盲重试，额度错误不自动恢复。
- 失败也保存已观察的内容/视觉 usage，避免成本只在成功时可见；恢复的新 Attempt usage 与历史 Attempt 分开保存，不把历史调用记为免费。
- 内容、计划、逐页 Prompt/参考资产、checkpoint 与最终证据一致才验收。没有增加另一套状态机或数据库迁移。
- 隔离 SQLite/transport 注入晚页失败后，仅缺页执行通过；真实 SDK 同会话多 turn 文本探针通过。生产整组图片质量验收尚待下一次用户生产。

## 已完成图片的显式回执恢复（2026-10-01）

- 恢复不是重新调用模型或恢复旧 thread 推理：人工核对原 ledger 最终 JSON、原 storyboard、冻结 Skill 和实际图片后，通过既有 guard / request_revision / claim / execute_claimed 导入新 Revision。旧失败 Attempt 保持原样，仍需人工批准。
- 专用维护入口 `python -m creatoros.integrations.receipt_recovery --run-id ... --ledger ...` 默认 dryrun；`--apply` 必须提供所见 version 和接受的 receipt SHA256。只支持 invalid_production_receipt 的失败双 Skill Run，拒绝改动教学/屏幕文字、缺页/坏图/越界路径/错误资源。
- 正式数据库主题 Run `5d51415b-ce24-4a98-83a1-09e61bee7ab4` 已恢复 Revision 2 / Attempt 1，11 张原图通过验收，状态 awaiting_approval（version 9）。Revision 1 保持 failed，无模型/生图调用、批准或发布；导入 Attempt usage=0 仅表示导入成本，不是原生成成本。
- 正式恢复前已备份 `tmp/creatoros-before-receipt-recovery-20261001.db`，原始回执与来源保存在新 Attempt 的 receipt_recovery.json；原冻结 Skill / storyboard 和生成目录不覆盖。
- 隔离 `smoke_receipt_recovery` 通过；正式 HTTP 11 个图片 URL 均 200。其他 Run 未变化，导入图片逐张与生成源 SHA256 一致，最终证据保留原 11 页内容原文。
- 收尾复核补严格 resolve 后目录边界及 ledger 符号链接拒绝；恢复 smoke 重跑通过，原正式产物不再改写。
- 新的 Claude Code Run `76fbc0f9-2952-4a84-aa10-219fcf23320d` 已恢复：7 页、回执 SHA256 `1d2c5bbfd4798769d048e02b3d9616ad0ad116e1d71d3d53eed54728634891d7`，预检通过后用户授权停止服务，导入 Revision 2 / Attempt 1，状态 awaiting_approval。此前正式库备份在 tmp/pre-receipt-recovery-20261002/，原失败记录保留。新版无 PageSpec 的视觉回执只接受精确的空白标题/发布字段、匹配页序与图片路径、无 warnings；资源前缀仅按已存在的冻结 Skill 文件校验后归一化。产物展示文案从原 Storyboard 生成草稿，原回复与修复记录保留，不能声称为原模型文案。真实网页显示 7 张图片，翻页与内容/Prompt 展开已操作验证，未调用生图、批准或发布。

## SDK 生产全新 session（2026-10-01，完成）

- SDK 每个 Attempt 新建会话，双 Skill 是 Mind / Visual 两个独立会话；旧 CLI resume 行为保持兼容。下方旧 SDK 同 thread 恢复约定由本节替代。
- 当前 Run 的 `producer_thread_id` 保存最新 Attempt 的视觉会话；每个 Attempt 保留自己的 ID，同 Attempt 不允许意外改绑。内容会话 ID 在该 Attempt 的阶段 Trace 中记录。无数据库迁移。
- 返工仅显式带入本 Run 最新已保存的内容 pages；不带旧生图 Prompt、旧会话或其他任务记忆。技术重试重新生产，不把旧图片自动复用。
- `storyboard.json` 与 `storyboard.md` 先于视觉阶段落盘；视觉失败时保留内容，成功时检查逐页内容一致并将两个文件纳入审批摘要。缺少 storyboard 的历史产物仍兼容，不补写原件。
- SDK 阶段隔离、技术重试/新 Revision 新 ID、同 Attempt 冲突拒绝、内容文件篡改/缺失拒绝，与既有 Run/Executor/API/Artifacts 回归通过。真实无生图探针结果见 integrations SPEC；本轮不重画消息队列图片、不批准或发布。

## 本地 Skill 执行快照（2026-10-01，完成）

- 排队时记录本地绑定；首次执行冻结当前 Skill。恢复/返工继续使用该 Run 的文件快照，本地后续修改影响新的 Run。
- 双 Skill 与已安装单 Skill 都复用同一 Run/Attempt 机制。历史记录按原 digest 复用旧冻结文件或导入原件，不静默切换到已编辑的工作副本。
- 无数据库迁移；新增 JSON 可选字段兼容旧输入。隔离测试覆盖 queued 后编辑、生产中编辑、恢复及新 Run 读取新内容。
- `smoke_producer_skills`、`smoke_pair_production`、`smoke_series_guards` 与旧 Run/Executor/Artifact/API 回归通过。双 Skill 历史恢复使用原 Attempt 文件；原图片、原 `production_evidence.json` 与导入原件未修改。验证具体清单见 `creatoros/integrations/SPEC.md`。

## P4 接线（2026-09-26）

- 可选 composition 输入快照承载双 Skill，旧快照仍可读取；复用已有 Run/Revision/Attempt/显式恢复，不另建执行器。
- pair 产物证据和 Skill 资源纳入验收/审批摘要；详细边界与验证见 `docs/studio/composition/SPEC.md` P4。
- 隔离 `smoke_pair_production` 通过：HTTP 幂等创建、配置冻结、中断后续接同 thread、新 Attempt、页/Prompt/图对应、缺文件/篡改/越界路径拒绝审批、坏证据 API 不产生 500。故障注入使用受控 Producer，不冒充真实生图。
- 旧链路 `smoke_content_run_service`、`smoke_series_guards`、`smoke_studio_artifacts`、`smoke_studio_executor` 回归通过。没有新建恢复状态机或新增数据库迁移；不宣称逐个教学/生图阶段都有独立 checkpoint。

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
## 按快照使用生产 Skill（2026-09-08）

- _produce_claimed 将既有 input.skill_name 传给 Producer，并使用数据库相邻的 Skill 注册目录。外部 Skill ID 指向不可变版本，改绑不重写已有 Run/Revision；内置默认兼容。验证见 docs/agent-studio/producer-skills/SPEC.md。

## 2026-10-06：完成交付的离线恢复

- Run/Revision/Attempt 仍是业务状态唯一来源；新 `worker_receipt.json` 只保存 SDK thread/turn 执行事实，不引入第二套业务状态。
- native 生产中的预览可以更新；完成后才冻结。SDK 已完成但旧宿主误拒收的交付，显式恢复创建同 Revision 的新 Attempt，核验最新 work 后离线组装，原失败 Attempt/日志保留。取消状态不能因文件存在而恢复。
- Web production_progress 在 Run 已终结时由数据库状态校准，不继续展示旧进度文件中的 running；GET 不修改文件或数据库版本。
- 回归：smoke_worker_delivery、smoke_studio_production_progress、smoke_content_run_service、smoke_studio_executor、smoke_studio_run_api 通过；真实历史隔离回放见 docs/single-thread-production/SPEC.md。

### 2026-10-18：索引拒收后的零生图恢复

- 0张核验不代表0张已生成；native Producer先重验磁盘交付与成功回执，current-thread原图的work副本仅通过SHA一致性归一化。修复次数耗尽在SDK前停止，显示核验页数、真正索引错误及“非账户额度”，不先安排production。
- 已授权的正式失败Run经数据库备份/真实材料隔离回放后，通过普通execute创建Attempt3并进入待人工验收；两个旧failed Attempt/用量/错误和原文件字节保留，新Attempt用量全0。通过普通ProductionQueue.finish核对当前队列结果，不把旧Attempt改为成功，未批准或发布。
- HTTP读取详情与图片200，1254×1254图像哈希与原始生图一致，前端路由可达；没有修改前端组件，不声称浏览器视觉验收。新增19条故障回归及相关native/worker/Run服务回归通过，详见integrations/SPEC.md。

## 外部图片导入（2026-10-15）

- 增加明确 `external-import` 输入协议：图片来自会话附件，不声称由栏目 Skill/Codex 生成；复用 SocialContentPack、Run、Revision、Attempt 与原图校验，状态直接为待人工验收。不会批准或发布。
- 入口按账号及栏目校验，当前会话与宿主请求 ID 绑定幂等；文件验证后才一次事务写入 Topic/Run/Revision/Attempt/事件。失败可留下未登记的导入目录，但不留下半条业务记录。
- 外部导入拒绝生产、恢复与自动返工，防止旧入口误调 Codex；仍可查看、人工批准、取消/移除。
- 验证：隔离 SQLite 与真实本地 HTTP；图片为本地测试夹具，不调用付费生成模型。
- 实测 `tests.smoke_chat_image_actions`、`tests.smoke_content_run_service`、`tests.smoke_studio_artifacts` 通过；包含损坏图片零业务记录、目录逃逸及链接拒绝。失败保留未登记目录供诊断，不自动清理或冒充验收成功；可在确认不属于任何 Revision 后人工回收。
