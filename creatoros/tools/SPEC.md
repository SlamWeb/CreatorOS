# CreatorOS Tool Exposure SPEC

## 2026-10-27 制作任务控制

- `manage_production_task` 通过当前账号受限的 Studio 接口按选题 list、stop、remove；运行中 stop 返回请求已发送及真实当前状态，未确认完成时不说已停止。移除只隐藏终态任务，保留 Run、图片及历史。
- 隔离 loopback HTTP + SQLite 测试两项通过，覆盖跨账号拒绝、排队任务停止与移除、参数和精简返回；未调用模型，不代表真实 Codex 中断验收。

## Agent 计划与持续执行（进行中）

- Agent 根据任务制定可调整的步骤并调用已有能力，宿主负责持久化、依赖、等待、终态续接和幂等，不增加专门的一键生图剪辑工具，也不让模型轮询。
- 本次要求和 Skill 显式固定；跨账号拒绝，失败项单独处理，已完成图片和历史记录保留。与 Web 使用相同业务接口，不隐式发布。
- 验收：隔离 HTTP/SQLite 的工具调用、重复提交、失败恢复、跨账号边界和真实前端 Agent；正式运行中的任务不受影响。

## 同一选题的独立制作任务（2026-10-27，隔离工具验收）

- 不增加工具：`start_content_run(topic_id, tasks)` 接受1～100项名称、完整brief与可选独立SkillOverrides，提交原选题下的独立Run并汇总同一制作素材集。与topic_ids、fresh、参考图制作和顶层skill_overrides互斥；模型不能自传request_id或task_id。
- 宿主使用会话、用户轮次、账号和父Topic派生稳定request_id；同轮重放不重复创建，变更要求复用同键由服务拒绝。未知结果交回原Topic/Run读取，不换身份另建。`manage_topics(get)`仅对production_task_count>0追加一次GET任务列表，后续进展不轮询。
- Guard在写入前检查父Topic当前账号、未移除状态和整批每项explicit Skill归属，候选仍按该账号已绑定家族过滤；旧冻结Run属于别的账号时拒绝。制作任务不增加审批、发布或切换栏目版本权限。
- 原始DTO保留完整brief与准确覆盖版本；模型投影仅返回任务名称、顺序、Run状态/版本、Skill摘要、具名Run入口和素材集入口，不回显长brief。
- 新增`tests.test_production_task_tools` 8/8通过（补充跨账号私有候选后27.851秒）：真实loopback FastAPI/隔离SQLite验证任务创建、原Topic不增条、宿主幂等与同键异文、读取任务、账号父Topic与整批版本/私有候选拒绝、账号候选试用不改绑定、移除/冻结归属拒绝；丢响应是实际POST成功后注入的传输故障。原host_tool_contract/topic_tool_contract/model_projection/skill_version_scope联合45/45通过（54.168秒），diff检查通过。生产者为受控本地夹具，此结果不证明真实Codex图像质量；正式服务和正式数据未写入或重启。

## Skill 上下文精确去重与批量编辑（2026-10-26，隔离验证通过，未正式加载）

- 用户先授权减少重复/低信噪比内容，随后要求核查Chinese History Story的工具设计并修改。不改压缩阈值、摘要、历史推理或等待续接；不增加模型工具，只扩展现有Skill编辑工具的batch参数。显式读文件仍返回完整所需正文，不用增加模型往返换取节省。
- 模型视图仅对author任务去掉已原样包含在规范Skill正文中的输入；普通提炼的独立参考素材保留，显式get即使pending也保留独有输入与草稿。列表/编辑/保存/取消只回必要索引和凭证，不回显刚写入的全文；原始DTO/Trace/账本不改。
- 同一资源、文件、digest、分页且正文精确相同的重复读取，在本次可见历史中保留第一份正文，后续用content_ref指回该tool_call_id；不同版本不混用，裁掉前读后下一读自动恢复正文。不修改assistant工具参数或reasoning_content，不删除工具调用与结果。
- 验证边界：模型视图纯转换红绿回归、真实旧Trace离线重放对比、隔离真实HTTP读写凭证及DeepSeek验证。正式数据和运行中任务不动，不自动重启服务。
- Chinese History Story最近20问/52次模型请求中，整组换图一问有11次模型请求；9次单文件写入均成功，属于工具粒度造成的往返，不是重试死循环。管理选题中的不同批量新增/原ID读取编辑不误判为重复写入。
- get_producer_skill(path=SKILL.md,list_files=true)一次向模型返回同digest正文和文件树；编辑batch按实际候选ID/digest顺序执行1–20项，错误/未知立即停、不回滚、不自动整批重提，明确报告已完成项。每项仍由原受控API校验账号、附件和CAS。已入库草稿冲突提供saved_skills准确身份，转编辑库中候选而非列全库绕路。
- 批量编辑失败时保留具体上游错误信息，让 Skill 权限失败与附件不存在可区分；仍保留已完成项及未知结果的处理方式。
- 定向联合99/99和skill_extraction_tools smoke通过；新增检查加入零付费门禁。真实原前端DS整组换图1/1：2工具调用、3次模型请求，9项修改全成功，候选仅余2图，原版/绑定不变；创建→回读→入库组栏目→候选编辑3问也通过。旧Trace两个节点本地估算减少16.75%/15.28%，不是供应商实测节省率。完整证据与边界见docs/agent-studio/web-chat/CONTEXT_SIGNAL_RESULTS_2026-10-26.md。

## 后台等待与栏目名称（2026-10-24，已验证）

- 提炼提交/改稿Web宿主最多观察60秒，每2秒读取同任务；进度不送回模型。CLI保留异步；等待中断/超时为unknown，不改后台任务。每问同一提炼/RSI最多一次真实状态GET，再次查询返回既有凭证并结束聊天轮，完整工具批次必须配对落盘。
- 拟题默认DS；联网工具仅限确实需要最新信息/外部证据。复用update_series_composition的name-only分支改名，不增加CRUD工具，保留原组合接口；宿主身份生成幂等键，归属和revision由API校验。
- 验证入口：tests.test_background_agent/test_pending_tasks/test_background_wait，以及隔离前端真实DeepSeek拟题和改名。正式任务、图片与旧评分不修改。
- 联合72/72、改稿状态补充后定向52/52；同一真实提炼的旧27次GET证据保留，不重跑。运行中草稿可能status=ready且operation=revise，同样按待完成限查，不误当终态。真实DeepSeek普通拟题零工具/零Codex，改名只调一次现有编辑工具；完整用量与正式载入边界见web-chat/BACKGROUND_WAIT_RESULTS文档。

## 账号素材集对象工具（2026-10-24，隔离验收）

- 新增manage_media_collection(list/get/create/update/remove/export)，复用Web集合服务，不改生产参数或任务包；账号由宿主绑定，名称查找不猜ID，动态图片索引分页而非塞入固定上下文。创建宿主身份幂等、更新CAS、未知不自动重发；删除只移除集合关系。
- ZIP持久冻结且可作为未来剪辑材料引用，原始导出ID/校验和/URL留ToolResult和Trace，模型只读业务摘要；宿主从真实回执提供准确下载链接。后续剪辑Skill本轮未连接。
- 新10项真实loopback工具/HTTP/SQLite测试及既有17项集合/宿主测试通过；真实原前端DeepSeek最终两次工具调用(list→export)、实际ZIP下载/刷新/原数据保护通过，失败探针保留，详情见docs/media-collections/SPEC.md。正式服务未重启，CRUD私有候选权限仍为下一步设计，不冒称当前全账号操作已补齐。

## 批量生产共用三十工位（2026-10-23）

- `start_content_run(topic_ids)` 与Web宿主说明同步为后台最多30选题并行；参数、接受回执和任务链接契约不变，仍共用单个ManagedRunExecutor。每题独立会话，不把同篇拆成多个thread。
- 生产并发与真实loopback HTTP/工具联合15/15通过：旧HTTP/工具共用队列测试显式保留5工位，新30工位隔离调度验收见runs/SPEC。本轮无模型调用、正式数据写入或服务重启。
- 原生产队列与宿主工具契约联合16/16通过，包含真实作用域HTTP与固定工具Schema；未增加工具。

## RSI 真实生产状态与恢复语义（2026-10-22）

- 校准模型投影保留stage、active_round实际试产提交/状态/图数/原记录入口、next_action和可恢复动作；完整事件、公开模型响应及失败用量仍按需查看，不灌入每轮账号上下文。
- get_creator_tasks包含校准任务；同选题普通生产与RSI互斥，拒绝时交付原校准入口而非另建任务。评审恢复只重试当前阶段，不加预算或重画；continue仅耗尽后明确再授权。
- manage_topics原ID修改后从头重做读取当前Topic正文，旧Run冻结输入不变；本轮不增加替代选题、任意prompt或更多工具。

## 选题对象工具收敛（2026-10-22，隔离真实前端验收通过）

- 本轮仅迁移选题：模型只见 manage_topics（list/get/create/update/remove），旧 list_series_topics、queue_topics 保留内部调用与历史 Trace，不再暴露给模型。栏目、Skill 工具暂不迁移；后续 Skill 权限采用共享可引用、账号候选可编辑、不能跨账号删除模型。
- update 修改原 Topic，需最新 edit_digest；请求身份由宿主会话/用户轮次/目标/动作生成。CAS、成功回执和同键异请求校验在生产共用写锁内执行；未知结果只读回执，不新建条目绕路。
- remove 沿原 TopicRemoval：无历史物理删除、有历史归档；排队/生产/核验/讨论期间拒绝。不改旧 Run/Revision/Attempt 快照、图片、审批或任务；管理能力与生产动作分字段返回。
- 验收先隔离 SQLite/真实 loopback HTTP 的权限、重放、旧请求覆盖和历史保护，再原 SPA→真实 DeepSeek→原 ID 修改/移除→刷新/Trace；不改正式数据、不重启运行中的正式服务。未执行前不标完成。
- 已在独立8895原前端连续发送4轮真实DeepSeek：create两条→get/update原ID不增条→remove指定旧条目→跨账号请求拒绝；点击权威栏目入口、编辑框全文、刷新与Trace均核对。8次模型请求，输入116381/输出604，无Codex或生产。SQLite每轮前后与原文件一致性均断言，完整证据与边界见docs/agent-studio/web-chat/TOPIC_MANAGEMENT_RESULTS.md；不改旧Eval分数。正式服务未重启，后端待安全重启加载。
- 定向联合33项及完整零付费门禁36/36组通过，新增topic_management组已接CI显式allowlist；不把受控故障与模型行为混成同一个成功率。独立只读复核与隔离HTTP时序另核验Guard→路由作用域锁，等待生产锁期间不能插入HTTP栏目转移。

## 指定版本与有界 RSI（2026-10-22）

- 现有 Skill 读取工具以 include_versions 查询准确版本、候选与真实目录摘要；账号目录按已绑定家族过滤，历史接口不泄露其他账号同家族的栏目绑定。
- 编辑仍只保存完整目录候选；用户明确指定候选制作时可按本次 skill_overrides 试产，不要求先修改栏目默认。发布与切栏目继续只允许人工入口。
- 仅新增 manage_skill_iteration 对象级任务工具：start/get/pause/resume/continue。写请求身份由宿主会话+用户turn+操作生成，不接受模型自选会话或本地路径；返回模型的仅状态/轮次/Judge版本门槛/反馈摘要与链接，完整历史在任务页和Trace。
- 普通改稿不启动RSI；人工保存可用版本不要求先填选题或试产，栏目切版仍由用户确认。试用可选；本轮RSI或自然语言明确迭代才授权最多3轮候选试产，继续需新授权。聊天 iteration_mode 纳入持久重放比较，不自动继承到下一轮。
- 定向隔离作用域、会话/重放与工具契约测试详见 docs/skill-iterations/SPEC.md；受控传输/持久化测试不冒充真实模型质量验证。

## Skill 候选写入回执（2026-10-21）

- 不新增模型工具。现有 update_producer_skill_file 写发布版会另存候选，回执保留新 skill_id、digest、family_id、parent_id、version_number、version_status；后续读写必须跟随候选 ID。
- 账号 Agent 可读写其已绑定家族的候选，但版本试用、发布和栏目切换均为人工 Web 入口；不能声称保存候选等于栏目已更新。完整验收见 docs/skill-versions/SPEC.md。

## 调研排队状态（2026-10-20）

- 不增加工具；research_series_topics 可返回 queued，与 researching 都属于已接受、尚未完成。宿主观察同一批次时覆盖这两个状态，不因为排队而误报已完成或自动再次提交。
- 后台独立请求持久化串行排队，同参数活跃请求复用；候选人工入队/生产边界不变，关闭会话只结束观察，不取消后台请求。

## 原始双 Skill 直接使用（2026-10-19，验证通过）

- compose_series / update_series_composition 复用现有 mind_skill_id、production_skill_id、CAS 与账号 Guard；说明明确直接引用两份原件，无需融合、不改写共享 Skill。同一 native 生产会话读取冻结资料，不添加模型工具或让模型管理 thread。
- 总入口与账号宿主覆写的 description 一并调整，避免账号 schema 仍暗示先融合；参数身份、回执、旧栏目与授权不变。测试核对两种真实 schema 和原 Tool→HTTP→SQLite 接线，不以 schema 检查冒充模型选择质量。
- test_host_tool_contract 6/6及smoke_studio_composition_tools通过。真实前端DeepSeek隔离探针实际先列库再compose_series一次，以两个原ID创建当前账号栏目，无融合/改稿/生产；完整证据和用量见web/SPEC.md，不称为模型永不选错或创作质量提升。


## 批量生产共用五工位（2026-10-19）

- 原start_content_run(topic_ids)参数与HTTP契约不变，说明由串行改为后台排队、最多5选题并行。Web/Agent共用单个ManagedRunExecutor，不按调用方各建容量，不新增工具或自动审批/发布。
- 同Run重放仍复用队列回执，同Topic互斥至旧物理执行者退出；调用后返回接受与任务链接不等于图片已完成。用量和失败记录保留，未调整旧Eval分数。

## 已有 Skill 参考图编辑（2026-10-19，隔离验证通过）

- 不增加模型工具：update_producer_skill_file 保留 write_text 默认行为，扩展 add_image/replace_image/delete_image；图片来源仅当前会话 attachment_id，写之前取真实文件目录 digest，换图不猜本地路径。
- 用户授权可同时看旧参考/新附件，再依次改图与正文；每一步使用最新回执或重新读取的摘要。修改共享 Skill 影响后续绑定栏目，不自动改旧产物/快照/批准状态；不派 Codex 修改 Skill。
- 库删除只提供网页入口，避免账号 Agent 对无归属共享库执行全局删除；创建栏目不追问定位/受众，旧可选字段仅为兼容保留。
- 三项新Schema/宿主身份/真实账号HTTP工具测试通过，与相关旧用例合计32/32；真实前端DeepSeek完成取文件、同路径换附件图、用新摘要保存正文，四个工具调用零错误、未使用Codex。图片回执仅路径/摘要/操作，不把base64喂回模型；引用保护错误保留referencing_files，便于先改引用。完整证据、用量及正式加载待空闲边界见 integrations/SPEC.md。

## 自写提示词与参考图保存草稿（2026-10-19）

- 总入口与账号入口增加 create_skill_draft：用户已有提示词/SKILL.md 时直接保存原文与指定会话附件，不再委派 Codex 提炼；需要从作品归纳方法仍用原提炼工具。宿主生成同轮请求身份，不接受任意本地路径或其他会话附件。
- 沿原草稿查看/编辑/入库工具；用户检查并明确确认后才能入库，创建栏目必须使用入库返回的真实 Skill ID。账号由宿主绑定，保存草稿不自动入库、组栏目、试产或发布。
- 原始回执、正文与附件证据保留 Trace；下一轮模型只收到状态、摘要、文件目录、预览入口，不能把文件存在当作已经看图。恢复消息时使用同一投影，不把原始诊断重新塞回模型。
- 验证包括真实 loopback HTTP、隔离 SQLite/文件的往返与权限/幂等；真实前端 DeepSeek 选择与后续入库建栏目单独记录，不用受控本地测试宣称模型永远选对工具。
- 真实浏览器两轮独立运行均按原文保存草稿，经第二条明确确认后才入库并创建同账号栏目；最终c轮调用顺序严格为 create_skill_draft / save_extracted_skills、compose_series，没有提炼/生产。原文与图片读回、实际 Skill ID 绑定与 Trace 均已核对，详见 integrations/SPEC.md；只证明这些已执行路径。

## 从头重做与直接改 Skill（2026-10-17，已验证）

- start_content_run 保留单选题/批量接口，增加显式 fresh 模式（单个来源 Run、所见版本）；用宿主本轮身份生成幂等 request_id，不让模型管理 thread 或复制选题。仅用户明确要求清空上下文/按最新 Skill 重做时调用。
- CreatorOS 通过 read_image 核实图片、既有文件编辑工具改共享 Skill，不再为普通作品讨论要求委派 Codex；旧只读 Codex 讨论记录保留可查，明确请求专家讨论的旧入口兼容。
- 新任务是同一选题的新 Run，不复用旧页、旧提示词、旧图或旧检查点；技术恢复与从头重做不是同一动作。人工批准和发布边界不变。
- 联合35条本地/真实 loopback 用例通过；真实浏览器中 DeepSeek 正确使用 fresh 参数提交一次，随后另一轮直接看图仅 get_content_run/read_image，没有新 Codex 讨论或生产。既有 Skill 文件编辑及冲突浏览器用例通过；未因本轮修改正式共享 Skill。集成证据见 runs SPEC。

## 批量生产（2026-10-17）

- 不增加模型工具：start_content_run 兼容 topic_id，新增 topic_ids（二选一，最多100），经 StudioClient 调共用 `/api/runs/batch`。投影保留逐项接受状态/错误/Run 链接，原回执仍在 Trace。
- 只有用户明确选择的已入队选题可提交；调研待选不自动入队或生产，旧失败/中断不自动恢复，批准与发布不变。账号 Guard 在路由写入前检查整批归属。
- 宿主交付逐项内容入口；普通回答简短说明已排队数量，不把技术状态当生成成功，不让模型轮询等待整组图片。
- 真实 DeepSeek HTTP 账号会话验证：查询现有选题后，一次使用 topic_ids 提交两条，最终回复与两个实际 Run 链接均保存；Codex 后台串行完成、待验收。记录见 runs SPEC，浏览器受控用例另列，不混称真实模型浏览器评测。

## 已有反馈只读查询（2026-10-17）

- 新增 query_feedback：只 GET 现有账号 analytics，沿 StudioClient 及账号 Guard 读取 SQLite，不刷新 MCP、不发布、不写入内容。总入口显式账号；账号会话隐藏并宿主注入账号参数，执行前拒绝显式跨账号参数。
- 返回近期 7 天已核实发布笔记的最新累计快照、栏目、采样时间、缺失值和分页，按指定指标降序；同分不宣称优胜，NULL 不补零。默认收藏，最多每页 20 条；栏目分析需读取相关分页后筛选，不把账号汇总冒充栏目汇总。
- 紧凑适配结果就是模型结果，原结果保留 Trace；不注入动态固定上下文，不新增表、不改 HTTP DTO，不进行整套工具合并。
- 验收：test_feedback_tool 的 3 项纯转换/Schema/真实 loopback SQLite 测试通过；测试包含同分、NULL、最新采样、分页、非法参数、宿主绑定和 HTTP 越权拒绝，查询前后隔离 DB 字节一致，仅发 GET。既有 feedback HTTP smoke 通过。测试数据只用于边界验证，不冒充真实平台效果。
- 回归发现 test_host_tool_contract 的 20 工具数量断言已过时（改动前账号工具实际 29 个），删去硬编码数量，保留 allowed_tools 精确集合一致断言，并把新工具纳入宿主参数隐藏检查；该模块及新增测试共 9 项通过，既有 publication feedback 16 项通过。
- 2026-10-17 02:03 北京时间，从正式前端账号悬浮聊天发送只读反馈分析请求，真实 DeepSeek 两次模型调用，仅调用 query_feedback 一次，正确报告样本只有一篇、点赞 0/收藏 1/评论 0、阅读/分享未知；数据采样于 2026-10-17 01:51。给出两条建议但没有调研、入队、生产、刷新或发布调用。会话 6806c6a3-4a47-4b6b-a663-8bea63fdf37f，turn 6382cd0b-995b-4b55-9b95-23548a3119d9；输入合计 19,926、输出 307 tokens。截图 tmp/feedback-tool-20261017/browser.png（不提交）；正式 Studio 已重启加载本次工具。
- 范围限制：目前仅最近 7 天已核实发布笔记的最新累计数据，不支持任意时间范围或历史趋势查询。没有把本次 smoke 记入既有冻结 Eval 成绩，也不宣称全量系统回归通过。

## 发布预览与核对（2026-10-16）

- Web 总览及账号会话新增 `prepare_publication`、`get_publication`，使用与网页相同的 API 和账号归属守卫。后者按显式参数核对或刷新指标，不重发。
- 不给模型确认或直接发布工具：用户在网页确认当前冻结快照后，宿主直接接管发送，无需模型再调一次 publish。聊天承诺或 `confirmed=true` 不能替代前端授权。
- 普通结果仅状态、账号、标题、阻塞原因、内容页入口及已核实的链接/指标；不暴露平台 Cookie、xsec token、镜像路径或冗长原始响应。

## 栏目写入的宿主入口（2026-10-11）

- 真实 A14 GUI 基线发现创建成功但 `compose_series` 缺少顶层 URL，宿主因此没有栏目入口。创建、修改组合、分配统一从已通过 HTTP guard 的 `series.id` 投影栏目地址；不使用模型提供的地址，不改业务事务、幂等或归属规则。
- HTTP 失败/CAS 冲突不生成导航；缺失或非法回执仍不伪造成功。原回答和底层 API 原回执保持可追溯。
- 隔离真实 HTTP 创建/分配/改组合与失败负例已通过；随后独立冻结 A14 原前端真实 DeepSeek 回归，真实点击新栏目入口、完整 Skill 卡片与刷新均通过，创建回执和 DB 指向同一新栏目。首轮失败报告保留，不倒改分数；完整链路见 `docs/agent-eval/WORKBENCH-REGRESSION-2026-10-11.md`。

## 契约收敛（2026-10-11，完成）

- 用户授权直接实施：普通回复只交付业务结果，内部引用和技术证据在 Trace；不靠删除 ID 代替资源归属校验。
- Web 工具 Schema 按实际宿主范围投影；固定账号参数由宿主补齐，执行端仍校验任何显式越权参数。CLI 原通用参数兼容。
- 模型结果采用按工具白名单投影；原始返回保留在账本、原文归档和请求 Trace，Skill 正文、候选来源、分页及 CAS 依据不丢失。模型请求只使用精简视图，格式投影失败回退原文，不把已完成写入误报为失败。暂不改变原 HTTP DTO 或压缩算法。
- 入队使用同会话、同用户请求、同规范化动作的稳定身份；未知结果只查询已有回执，回放核对账号/栏目/内容并返回真实落库 ID。新用户请求不按标题永久去重。
- 验收：纯投影与故障注入回归 + 真实隔离 SQLite/HTTP；最终必须在原前端发消息、真实 DeepSeek 执行、刷新与查看 Trace，不操作正式内容、不调 Codex 或生图。
- 本轮单测、真实 loopback、浏览器 DeepSeek 的分别结果与失败迭代见 `docs/agent-studio/web-chat/CONTRACT_RESULTS.md`；只承诺本地宿主同轮/同动作的重复保护，不宣称跨用户轮次语义去重或分布式 exactly-once。

## Skill 文本读取与图片能力边界（2026-10-09）

- 默认分页正文仅供阅读，不提供写入 digest；编辑必须列文件并按 path 读取当前全文/digest。更新 schema 与宿主明确禁止猜摘要或使用 source_digest，用户明确修改保存不重复追问。真实中文隔离编辑验收及失败尝试记录见 web-chat SPEC。

- `get_producer_skill(path=...)` 改走专用 `GET /api/producer-skills/{id}/files/text`，仅返回 Markdown/UTF-8 文本 JSON；图片与其他二进制返回 `skill_text_only`，明确文件路径/文件列表不代表实际看图。默认分页正文与 `list_files=true` 保持原契约。
- 工具与参数说明同步能力边界；不向账号 Agent 注入图片字节或图像 token。页面图片预览继续走混合 `/files/content` 路由。
- 验收使用 `tests.smoke_producer_skill_read_tool` 的隔离真实 SQLite/loopback HTTP：文本及 digest 正例、正常/损坏图片能力错误、跨账号 403、路径逃逸拒绝、读取前后字节/时间戳与数据库摘要不变。响应丢失仅用传输故障注入，不调用模型或改正式 Skill。
- 验证通过：`smoke_producer_skill_read_tool`、`smoke_producer_skill_edit`、`smoke_producer_skill_files`、对应 Python 编译与 `git diff --check`。既有文件浏览 smoke 的旧完整 JSON 断言补齐现行 digest/editable 字段，测试异常时先关闭数据库，避免 Windows 清理错误掩盖真实断言。

## CreatorOS ↔ Codex 讨论和任务查询工具（2026-10-08，完成）

- `discuss_content_run` 与 `get_content_discussion` 通过 Studio API 讨论指定已验收 Run/revision；创建任务需要 `request_id`、`revision_id`、`artifact_digest`、用户消息。模型工具不接受任意 thread ID 或产物路径，真实 SDK thread 与文件由宿主按版本定位。讨论只读，不改生产/批准状态。
- Web Agent 创建讨论后读取同一记录直到完成/失败；超时或服务关闭时返回原讨论 ID，不重复提交。周期活动只投影到原工具项，不另建 Agent 任务状态。CLI 没有活动回调时保留异步任务句柄。
- `request_content_revision` 仅在用户明确要求返工时调用，复用 `/api/runs/{id}/revisions`，按 `expected_version` 建立待执行版本，不自动执行。
- `get_content_run` 的摘要包含紧凑版本目录（revision_id、revision_number、artifact_digest、artifact_available），因此 Agent 可以引用实际可讨论的历史版本；不把卡片、图片或完整产物塞入摘要。
- `get_creator_tasks` 只查 `/api/creators/{creator_id}/tasks`，投影生产、调研和讨论现有记录。账号工具集同时开放这四个工具；Web/CLI 总工具目录复用同一适配器。
- Agent 宿主共享职责约定：CreatorOS 提供业务事实、账号 scope 和冻结输入；Codex 只处理当前显式输入。图像讨论只有实际传入图像后才可声称看图。一次性讨论反馈不会自动保存 Skill；只有用户明确要求才调用返工或 Skill 编辑。
- 账号 Scope Guard 对讨论读写、返工、账号任务读取逐个核验当前账号与冻结 Run 归属；任务列表从既有 DB/Research/Discussion 记录投影，无新业务状态表。栏目转移后含旧账号冻结 Run 的栏目按保守策略拒绝或从账号列表排除。
- 验收：隔离真实 SQLite/HTTP 验证工具 schema、错误 ID/跨账号 403、转移前后冻结归属、只读任务投影及 summary 计数；不调用 Codex 模型、生图、发布或正式运营数据。
- 2026-10-08 验证：`smoke_content_discussion_tools`、`smoke_worker_task_projection`、`smoke_content_discussion_tool_http`、既有 `smoke_content_discussion`、`smoke_research_chat` 通过。loopback HTTP 用真实 StudioClient 和 creator-session scope 验证版本选择、只读讨论提交/等待/历史回读及返工版本创建；隔离 Reviewer 替代 Codex，不产生模型或图片费用。

## 已安装 Skill 编辑工具（2026-10-06）

- Agent 通过既有 `get_producer_skill` 的 `list_files=true` / `path` 参数获取受限文件树和正文，再由用户明确授权后调用 `update_producer_skill_file` 更新一个文本文件；更新带整个 Skill digest，工具结果保留冲突时的 `current_digest`。复用读取工具保持账号 Agent 的固定 schema 体量。
- Web 账号 scope 仅对当前账号已绑定的 Skill 放行这三项文件工具；scope 候选来自服务端会话，不接收模型传入账号 ID。共享文件影响绑定栏目这一事实写入工具说明和宿主指令。
- 隔离验收见 `tests.smoke_producer_skill_edit`，不调用真实模型；包含合法 frontmatter 更新反映到 catalog describe、原件/快照保留、CAS 冲突和跨账号 403。
- 真实 DeepSeek Agent→本地 HTTP→Skill 文件验证见 `tests.live_producer_skill_edit --run`；报告在 `tmp/live-producer-skill-edit-20261006-222156/report.json`，模型实际选用了目录/读取/更新工具，未调用 Codex 或生图。

## Web 调研等待终态（2026-10-05）

- `research_series_topics` 只提交一次；Web 提供公开活动回调时宿主每秒 GET 同一批次，终态候选/安全错误再返回模型。`get_topic_research` 也可观察已有活跃批次。CLI 无回调时仍异步返回，不增加新工具或模型轮询循环。
- 有界等待为 Codex 宿主超时 + 10 秒，读取超时 2 秒；停止/等待超时/读失败保留原批次 ID 和 unknown，不自动重发 POST、取消后台任务或谎称研究失败。真实 failed/interrupted/stale 返回 error ToolResult，公开活动不反复塞进模型上下文。
- `smoke_research_chat` 通过真实隔离 HTTP/SQLite，模型受控以验证竞态：两会话共享同批次、独立 Provider/账本、终态/错误回到模型、等待中关服务/重启不重提、读失败/超时零重提、无自动入队或生产；低成本真实模型验收另见业务 SPEC。

## 账号会话工具范围（2026-10-04）

- Web 账号模式复用同一工具实现，仅缩小固定 allowed_tools；全局安装/提炼/栏目转移留总览。执行器照旧再次拒绝未开放工具。
- RuntimeContext 的 creator_id 和 agent_session_id 来自不可改绑的会话，不由模型传参。Studio 工具经现有 Client 携带会话头，API 验证真实资源归属；不复制业务服务，也不增加 Tool Router。
- 目录只返回本账号，Skill 目录只读共享。CLI 默认全局行为不变。隔离 HTTP 和真实 DeepSeek 证据见 `docs/agent-studio/web-chat/SPEC.md` P1。

## 账号 Agent 按需读取 Skill 正文（2026-10-04）

- 新增只读 `get_producer_skill(skill_id, offset, limit)`；仅在用户明确询问 Skill 写法/规则时调用。可读目录已登记 Skill 和固定内置 `knowledge-to-carousel`，每页最多 4000 字符；返回 name/description、正文和 `page.total_chars/has_more/next_offset`。
- 通过同一 Studio API `GET /api/producer-skills/{skill_id}/content` 读取；账号会话 guard 只放行该精确 GET 路径。服务端按注册 ID 映射工作目录或内置固定目录，不接收任意路径；校验受管目录与 `SKILL.md` 不为 symlink、正文直接位于 Skill 根目录。
- 读取不调用 `describe()`，不补建缺失工作副本、不计算全部 assets digest、不写数据库；正文缺失或未登记返回 404。`read_file` 范围不变。
- 验收：`python -m tests.smoke_producer_skill_read_tool` 覆盖真实隔离 HTTP、账号会话、固定内置/已登记 Skill、分页、超限、任意 ID/路径拒绝、缺失副本不恢复和非法 UTF-8 不写回；不访问正式数据库或外部服务。

## Artifact → Skill Agent tools (2026-10-02)

- Web 与 CLI 共用 extract/get/save/cancel 四个 Studio 工具。提炼支持用户明确提供的本地 `image_paths` 或已有 `upload_ids` 二选一；CLI 本地读取复用 `read_file` 的项目根目录与敏感路径规则，且在有界读取前检查大小。Web 的 `archive_only_reads` 宿主只接受 Skill 页已上传的 `upload_ids`。图片限 1–6 张 JPEG/PNG/WebP、单张最多 4 MiB。
- `request_id` 必填并透传服务端幂等协议；上传与提交不自动重试，结果不确定时调用方查询/复用同一 ID。无 `job_id` 的 get 查询历史任务。
- 草稿查询和保存分开；保存描述要求先展示草稿并获得用户明确确认，传回 `expected_digest`。工具不自动保存、绑定栏目、生产或发布。
- 验收：`python -m tests.smoke_skill_extraction_tools` 使用 StudioClient 传输桩验证 schema/请求与路径安全，不触发真实 Codex 图像提炼或 Skill 库写入。

## 安装工具描述与实际实现对齐（2026-10-02）

- 将 install_producer_skill 的旧“委托 Codex 下载”描述改为 CreatorOS 使用 Git 下载核验到本地生产 Skill 库；明确不调用模型、不安装到 Codex 全局目录。安装流程与参数不变。
- 同步 get_skill_install / list_producer_skills：登记不等于绑定或生产兼容；明确现有组合工具入口与当前本地工作副本，去掉仅网页可绑定的旧限制描述。
- 验收：`python -m tests.smoke_studio_composition_tools` 通过，包含模型可见 schema 描述回归及真实本地 HTTP/隔离数据库跨入口检查；未调用模型或启动真实调研、生图、安装，未修改正式运营数据。安装器使用现有隔离 Git 文件夹夹具，本轮只验证描述与接线，不冒充真实下载验收。

## 可编辑本地 Skill（2026-10-01）

- `list_producer_skills` 返回当前工作副本元数据及 `local_path`；`compose_series`/`update_series_composition` 可使用此路径或兼容目录 ID，经同一 API 规范化保存。
- 没有增加编辑工具或放开 Web `read_file`；本地文件由用户编辑，Codex 生产读取首次执行冻结的目录。隔离共用接口/工具回归通过。

## 栏目组合 Tool（2026-09-23，P2）

- 新增 compose_series / update_series_composition / assign_series / queue_topics 四个写工具，全部经同一 Studio API（StudioClient 带 x-creatoros-origin 头），与 Web 表单同一服务、同一幂等与 revision CAS。
- queue_topics 是 A 策略直接入队：明确指令一次事务完成校验/写入/审计；prepare_topic_selection 保留为"先看影响"能力，两者都不暴露可复用的确认凭证。
- install_producer_skill 可声明 role（mind/production）；省略即未分类，可展示不可生产。
- Web allowed_tools（STUDIO_TOOLS）与 AgentPage 中文名同步更新；CLI 使用同一 tool_registry。
- 宿主指令补充 A 边界：明确给标题+栏目则直接入队（source=manual，不因库中不存在而追问）；查看/建议不写；删除/覆盖/发布不在入口内。
- 验证：smoke_studio_composition_tools 的真实本地 HTTP 跨入口回归保留；旧 A 策略模型评测于 2026-10-09 退役。当前账号题集见 docs/agent-eval/SPEC.md，模型任务尚未运行，不继承旧成绩。

## 统一选题查询（2026-09-14）

- list_series_topics 默认返回栏目统一选题库，state=all/pending/queued；待选提供批次/候选 ID 给现有 prepare_topic_selection，仍须用户确认。Tool 数量不增加。
- 真实 DeepSeek/隔离 HTTP 查询后直接准备第二条预览通过，来源保留且零入队。细节见 docs/agent-studio/topic-research/SPEC.md；旧正式队列 API 不变。

## 当前会话回读（2026-09-11）

- read_tool_result 从 Loop 绑定的 RuntimeContext.session_file 回读；模型不可指定文件或其他会话。无上下文直接调用兼容 CLI 默认文件。
- Web 开放 read_tool_result；同时开放受宿主绑定的 read_file，但 Web 只能读取当前会话的 `.tool-results` 归档目录，不能读取项目任意文件或其他会话。归档大文件用 `unit=chars` 分页；详见 docs/context-management/SPEC.md。

## 栏目选题 Tool（2026-09-09）

- research_series_topics 提交后台研究；get_topic_research 读取状态/候选；prepare_topic_selection 按结构化选择、修改和顺序生成 Preview。CLI/Web 共用 Studio API，确认仍不暴露给模型。
- 真实 DeepSeek 验证只选 c2 并修改标题成功，候选角度和来源保留；没有生产或确认副作用。详细记录见 docs/agent-studio/topic-research/SPEC.md。

## Web 宿主范围（2026-09-08）

- RuntimeContext.allowed_tools 为可选宿主范围；Loop 过滤模型 schema，执行器再次拒绝范围外请求。默认 CLI 保留既有完整工具集合。
- Web 只使用五个 Studio 工具。RuntimeContext.studio_url 显式传到同一 HTTP Client，测试库/自定义端口不通过改全局环境变量实现，避免宿主串线。
- 验证见 `docs/agent-studio/web-chat/SPEC.md`，本轮没有新增另一套业务工具或自动批准能力。

## 本轮目标

- 保留完整 `tool_registry` 供执行器、Skill Runner 和后台编排使用。
- 允许某些组合工具只作为内部 Python 能力存在，不默认暴露给 LLM。
- 默认模型工具列表只发送标记为可暴露的工具 schema；原子工具仍保持现有参数和执行行为。

## 当前假设

- `route_and_answer` 是 `route_hotspots → 选择 → ask_author` 的组合 Runner，交互 Skill 应优先让 LLM 编排原子工具，因此暂不放入默认模型 schema。
- `tool_registry` 仍必须保留 `route_and_answer`，已有 Runner 和 smoke test 继续通过统一执行入口调用它。
- “不暴露”只影响发给模型的 `tools` 参数，不影响直接通过 `execute_tool_call` 执行。
- `route_hotspots` 的作者队列为每个候选提供作者内 `position`，让 Skill 可以把一次用户选择明确绑定到一个作者和一个热点。

## 验收

- `route_and_answer` 出现在 `tool_registry`，但不出现在默认 `tools` schema。
- `read_file`、`route_hotspots`、`ask_author` 等原子工具仍出现在默认 schema。
- 既有工具执行 smoke 通过，新增验证能证明模型工具暴露边界稳定。

## Codex 内容生产 Tool

- `produce_content_pack(creator_id, series_id, topic_id, topic_title)` 把已选题目交给固定的 `knowledge-to-carousel` Skill，不让 Codex 决定栏目或选题。
- 一次 Tool 调用对应一个新 Codex thread；结果返回内容包路径与 `thread_id`，前台等待期间不重复调用主 Agent LLM。
- 生产失败返回结构化 ToolResult；成功结果只向模型投影摘要，不把图片字节或完整 JSONL 塞入上下文。
- `codex_producer_smoke=passed`，Registry schema、参数路径约束、回执解析、图片归属和 Manifest 验收通过。

## Agent → Studio（2026-09-07）

- 新增 5 个模型工具：list_creators、list_creator_series、list_series_topics、start_content_run、get_content_run，复用 Studio HTTP 接口。
- 旧 produce_content_pack 留给底层兼容调用，从默认 tools 隐藏。Agent Loop 用 model_requested=True 执行工具，拒绝历史消息或幻觉调用隐藏工具；宿主内部调用兼容保留。
- start 仅接 topic_id，返回 accepted/status/run_id/url；只提交尚未尝试的首版 queued Run，其余返回现状。查询状态不返回完整 Revision、图片或 Trace。
- 查询分页保留 total；busy/版本冲突/未知网络结果不自动重试。规范与验证见 docs/agent-studio/SPEC.md。
## 生产 Skill Tool（2026-09-08）

- install_producer_skill / get_skill_install / list_producer_skills 通过同一 StudioClient 暴露给 Web/CLI；安装返回句柄，不轮询等待、不生成或自动绑定。retry=true 只用于用户明确重试失败/中断。验收见 docs/agent-studio/producer-skills/SPEC.md。

## 会话图片工具（2026-10-15）

- `read_image` 仅允许当前会话附件、账号已绑定 Skill 参考图、账号所属 Run 的指定版本图片。读取真实受检图像后登记为当前会话附件；ToolResult.details.image_attachments 交由宿主显式加入模型上下文，文本结果不包含图片字节。
- `extract_skill_from_images` 把当前会话图片提交既有提炼器，草稿仍需用户审核后入库；任务所有权另存于会话根的 image-extractions，账号 Agent 不获得全局提炼任务列表。
- `import_content_images` 导入已完成作品为待验收内容，不是生图队列。宿主绑定 session/turn 幂等，模型不可选择其他会话或任意路径。
- 隔离回归包含实际 HTTP 图片读取、账号拒绝、幂等导入、待验收状态、图片可读、批准校验及禁止生产/返工；正式数据不变。
- `tests.smoke_chat_image_actions` 已通过：Skill 图片按绑定账号读取、非图片/越界拒绝、ToolResult 图片引用、跨会话隔离、提炼任务所有权、外部成品事务与文件校验。此项是工具/HTTP 回归，不冒充真实模型视觉或提炼质量验收。

## 任务参考工具输入（2026-10-18）

- 不新增模型可见工具：research_series_topics支持当前会话attachment_ids与reference_presentation；start_content_run新增参考作品直接制作分支，与已有Topic/fresh参数互斥。queue_topics保留候选reference_id，生产不只收到标题。
- 图片由宿主绑定会话/栏目验证，不接受本地路径；宿主保存用户原话，模型整理的instructions不能替代原话。同会话同参考意图跨turn复用请求键；未知结果携带request_id/topic_id/回执入口，终态重放不伪称重新入队。验证和局限见 `docs/topic-references/SPEC.md`。

## 多阶段制作工具（2026-10-27）

- 2026-10-28：模型视图的 manage_material 仅 list/get，不再提供 import_link 作为制作前置；历史工具调用得到明确 bgm_reference 交接提示。非模型内部导入移除接口不支持的request_id字段。独立理由图/场景图明确一图一task、多项同题并行，后续视频通过计划等待；旧多页单任务仅在用户明确要求连续制作时使用。

- 2026-10-28：视频任务 `inputs` 接受 `bgm_material_id` 或 `bgm_reference` 二选一；后者保留用户完整分享文本，直接作为 Codex 输入，不以链接预解析成功为提交条件。Agent 不预先调 `import_link`，受理不代表 Codex 已取得音频或完成视频；原素材文件输入保持兼容。

- 2026-10-28：计划步骤可加入 `request_review` 检查点；自动续接绑定原计划，只开放受限读取与计划修改，不直接启动生产或另建计划。自动修改只提交变化步骤时宿主保留已执行历史，已提交/未知生图不能换步骤重提；可安全跳过只读失败并保留原错。模型投影保留恢复所需参数、是否可跳过和各制作项实际状态。隔离 HTTP 及真实 DeepSeek 读取失败恢复已验证；失败由隔离测试计划注入，不代表真实 Codex 故障已验证。

- `start_content_run.tasks[].skill_ids` 直接指定本栏目内一个或多个版本，不向模型要求主辅或输出声明；现存 `skills` 保留历史兼容。栏目与账号 `manage_skill_library` 结果提供过滤权限后的 `available_versions`，投影保留 ID/digest，不开放别的账号私有候选。模型修改栏目池可只传 `skill_ids` 与版本，宿主保留仍有效的旧默认并清理已移除项；旧 `defaults` 参数仍可用。

- 新增两个对象级工具：`manage_skill_library` 的 get/add/remove/set_pool 管理账号引用和栏目阶段默认；`manage_material` 的 list/get/import_link 查询、导入 BGM 分享链接。账号 ID 由宿主绑定，模型无文件路径、音频字节或请求键入口。
- `start_content_run.tasks` 支持 image/video、精确 Skill ID 与 primary/supporting、集合/导出/BGM 对象 ID 和 depends_on。Agent 的 digest 可省略：宿主同次调用按唯一 Skill ID 读取完整目录摘要；显式摘要不符拒绝，HTTP/冻结层仍校验完整 digest。既有图片入口保持兼容，typed tasks 直接走任务路由。
- 请求键由可信会话/用户轮次、Topic 和补齐摘要后的规范任务 payload 派生；同轮图片与视频分别提交，同一有效规范（包括省略/显式同摘要）重放取回原任务。模型不能指定请求键。等待无 Run 的回执不拼造 Run URL，依赖由后台推进，不让 LLM 轮询。
- 紧凑投影保留准确版本/权限、阶段等待状态、素材 ID，以及后续视频所需的集合/export_id/checksum；去掉本机路径和重复正文，用户回复仍不展示内部编号。
- `tests.test_staged_production_agent`、`tests.test_production_task_tools` 与精确导出回执定向回归共 17 项通过；最终规范化请求键和省略摘要的同轮图片→依赖视频→重放路径另定向 3 项通过。真实隔离 loopback HTTP/SQLite/Skill 文件、真实音频探测解码；图像生产使用受控本地夹具，不代表真实视频或模型质量验收。
- get_creator_tasks 包含尚无 Run 的 waiting/needs_attention/cancelled 制作，具名选题/素材集入口由宿主严格重建；已存在 Run 时不重复计算 task。紧凑投影保留任务类型/等待依赖和恢复入口，排除 definition。相关新旧 HTTP/导航 20 项通过（缺 codec 时三项明确 skip），worker/Observation 只读 smoke 通过。
- 既有 reliability allowlist 新增 skill_libraries/staged_production 组、等待可见性和 Observation smoke。配置真实 ffmpeg/ffprobe 后相关 43 项通过；未配置时仅 codec case 报 skip，库/权限/结构/导航仍执行。通过 PATH 或 CREATOROS_FFMPEG、CREATOROS_FFPROBE 指向现有可执行文件，不为 CI 额外下载媒体运行时。
