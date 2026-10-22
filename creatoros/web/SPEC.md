# CreatorOS Web API SPEC

## 可复用安全维护入口（2026-10-23）

- Studio入口登记本地实例身份并先取得数据库宿主锁，再做迁移；活跃旧执行者持锁时拒绝启动。关机先关闭数据库，再释放宿主锁。
- 新的维护启动模式推迟消费者启动和旧任务恢复，健康/旧行/文件核对通过后才恢复。普通create_app及日常启动保持原行为。维护期拒绝新写操作，已在途操作或真实任务存在时不进入维护；控制接口仅loopback、随机token、准确实例身份，不向模型工具暴露。
- 隔离真实HTTP/进程启停验证与故障注入分开；不启停正式8765、不改旧RSI标准/分数/图片，不调用Codex。实现、命令和失败边界见creatoros/maintenance/SPEC.md。

## 新 Judge 策略与本轮正式加载（2026-10-22）

- 用户要求先确认空闲：8765原本无监听；生产/Attempt/执行队列/调研/发布/RSI均无活动记录，75份辅助元数据无活动项，未停止或中断任何生产。SQLite先独立备份，启动前再次通过空闲门禁，使用项目deepcode Python启动正式8765 PID32924。
- 已加载当前分支的RSI恢复、原Topic编辑、聊天增量及新建任务Judge v2；迁移0017仅按既有选题编辑回执契约升级。启动后的9张相关表全部原行列值与启动前一致，包括完整旧skill_iterations config/state、生产与调研队列；4617个旧Skill/任务/输出文件SHA256完全一致，integrity=ok、外键错误0。旧failed RSI未resume、未重评，没有新生产或Codex调用。
- HTTP健康/数据库ok，首页与准确静态包HTTP200；服务实际返回index-BfCpolbP.js，字节与当前web/dist一致。只读OpenAPI确认迭代接口存在，没有向正式账号发测试聊天或创建任务。备份、before/after与HTTP核验记录在tmp/restart-rsi-policy-20261022/，不提交业务库与凭证。
- Judge修复的隔离回归和真实DS建标探针详见docs/skill-iterations/SPEC.md，不能把健康检查当作新端到端生产或内容质量证明。

## RSI 与聊天可观测契约修复（2026-10-22，隔离验收完成）

- RSI 进度必须包含当前阶段和真实试产状态；同选题活跃校准不能被误读成尚未生产并另开普通 Run。失败评审恢复复用已验收图片及冻结输入，不增加三轮预算。
- 聊天条目关联 turn / model request / tool call；历史缺失标识仅提供只读稳定展示 ID，不猜测调用关系。原始工具参数和结果通过已有脱敏 Trace 按需读取。
- SSE 首次发送 snapshot，之后发送带 base_revision/stream_revision 的 patch，文本只追加增量。业务 version 仍用于用户提交 CAS，流序号每次保存单独增长；失序必须重新取快照。
- 旧任务、图片与正式数据不修改；隔离回归和真实前端 DeepSeek/Codex 链路分别记录，不以受控故障测试冒充真实模型验证。
- 实际新稿与精确候选干净重做已通过原前端、真实DeepSeek/Codex；旧25文件/Run全列、原RSI/栏目/共享版保持。真实坏JSON只重评一次后返回评分，见 `docs/agent-studio/web-chat/RSI_RECOVERY_RESULTS.md`。正式服务没有为测试重新启动或写入。

## 原选题编辑与账号管理能力（2026-10-22，隔离后端验证）

- GET `/api/topics/{id}` 返回正式选题当前正文、`edit_digest` 与独立 `management_actions`；旧 `available_actions` 继续仅表示生产动作。列表和单条读取共用权限投影；待选建议不是正式 Topic，不伪装成可直接修改的选题。
- 原 PATCH 兼容无身份的旧网页；Agent 来源（包括总览）必须同时提交所读摘要和 request_id。修改与生产共用写锁，并持讨论锁，在事务内再次核实摘要、移除事实、栏目启用、所有关联 Run 的生产/验收状态、pending/running 执行授权及活跃讨论。修改只影响原 Topic，不创建替代选题，不写旧生产快照。
- 成功写入在同事务保存 `edit_topic` 回执，冻结账号、目标、准确请求内容与写后正文。同键异目标/内容拒绝；精确重放返回首次结果，不覆盖后来修改。GET `edit-receipts/{request_id}` 只查询成功回执或 unknown，no-store，不重新执行。账号同时校验回执原归属与当前资源归属，栏目转移后不能窃读旧回执。
- 选题移除沿既有 TopicRemoval 语义：无历史删除、有历史归档、旧产物和原记录保留；补齐待消费生产授权及非活动栏目保护，返回权威 series_id/title 供宿主生成入口，重放仍保留权威 series_id。普通服务和正式 SQLite 未由本轮测试操作。
- 新增真实隔离 loopback HTTP/SQLite 测试覆盖同 ID 修改、CAS、幂等、账号越权、队列/生产/验收/讨论保护和历史全字段/图片 SHA256 不变；原 topic_crud、topic_remove、account_scope smoke 已通过。真实前端 DeepSeek 验收由主流程另记，不用本地受控产物声称已完成真实模型 E2E。

## 本轮修改正式加载（2026-10-22）

- 用户授权仅空闲时重启。两次检查正式SQLite：无producing/validating Run、running Attempt、pending/running生产队列、在途调研或发布；69份提炼/安装/调研/聊天/讨论元数据均无活动项且全部可读。7条旧queued Run没有待消费执行项，原样保留，不替用户提交生产。
- 已先备份SQLite，再核对8765确为PID31952的项目Python服务后替换为PID21676；没有终止Codex进程、提交试产、切换任何栏目或修改Skill。新服务自动升级至0016，健康/数据库均ok，首页和准确构建资源HTTP200。
- 正式OpenAPI确认publish只必填expected_digest/request_id、不再必填试用字段；RunStartRequest含skill_overrides，RSI路由可只读查询且无意外新任务。原6张业务表的全部列值（6账号/15栏目/100选题/90Run/102Revision/100Attempt）及3211个旧Skill工作副本/产物文件逐项一致；SQLite integrity=ok、外键错误0。
- 本地备份和只读核验记录：tmp/restart-skill-iterations-20261022/before.sqlite、before.json、after.json及服务日志。不把本次部署验收当作新模型测试或内容质量提升证据。

## RSI 任务与版本可发现性（2026-10-22）

- 用户随后取消普通版本试用门槛：可直接人工保存或切换，无需选题/试产；旧trial接口保留供RSI内部，人工发布和切版边界不变。以下2026-10-21章节是历史证据，不再作为当前强制试用规则。
- GET Skill目录支持 include_versions/family_skill_id；账号作用域按已绑定家族包含候选和历史准确版本，并过滤版本历史中的其他账号栏目名。查询布尔别名遵循HTTP校验而不绕过过滤。
- 账号可创建候选和明确试用，但 publish-version / series skill-version 仍拒绝所有Agent来源；本次 Run覆盖额外核验显式Skill家族归属，不临时切栏目。
- skill-iterations 创建、读取、动作和受管文件均检查账号及当前栏目归属；会话附件只由宿主来源头确定，模型不能传 origin_session_id。列表将账号固定到当前会话；栏目被转移后旧账号与新账号均不能偷读旧任务。
- ChatTurnRequest增加严格布尔 iteration_mode，保存到请求账本及用户条目；同一request_id不能更改模式，旧请求按false兼容重放。当前请求开启不改变后续会话默认。
- 完整流程、持久调度及真实验证状态见 docs/skill-iterations/SPEC.md；本节不宣称正式服务已重启或完成付费试产。

## Skill 版本入口（2026-10-21）

- Skill 版本历史/创建候选、干净试用与用户确认保存通过独立版本服务接入。栏目切换使用 revision CAS 与 SQLite 幂等回执，版本 ID 精确绑定；账号 Agent 不能代为试用、发布或切换。
- Web/Agent 正文及图片编辑均只改候选；回执包含新 skill_id/digest/版本身份，后续工具必须沿该 ID 编辑。历史 Run、排队输入和其他栏目不跟随更新。
- 多版本家族暂不开放逐版删除，避免移出旧登记导致无法回退；与版本无关的旧 API 保持兼容。Observation 同时读取旧长路径和新短路径试用记录。
- 完整约束和验收见 `docs/skill-versions/SPEC.md`。

## 推理档位按用户五档命名（2026-10-21，正式加载）

- 新聊天默认high，选项low/medium/high/xhigh/ultra；按官方实际映射归一为low/high/high/high/max。保留用户requested值用于幂等/偏好，实际档位可检查；不宣称中高极高是不同底层强度。
- 旧none/max请求仍可按原请求ID显式核对重放，不重写既有账本或在途请求；独立业务Provider默认与structured调用不随聊天偏好变化。
- 本轮只修聊天档位，不实现Skill版本库、优秀作品池或DS自动校准；这些仍需按讨论后的范围单独开发。
- 8项后端回归、web_agent/account_sessions两组smoke通过。真实隔离medium一轮网络记录确认wire high + thinking.enabled，输入3054/输出87，零生产；证据tmp/chat-context-reasoning-live-20261021-bb076027/report.json。未重跑上一轮四档全量探针。
- 空闲检查无活跃任务后重启，正式8765 PID2296；现有会话只读DTO返回五档与真实映射，原Run八图仍逐字节一致并保留旧失败。正式网页新聊天显示默认高，无关闭选项；没有向正式会话发测试请求。

## 聊天请求推理档位与上下文读数（2026-10-21，隔离真实验证通过）

- turn请求增加实际DeepSeek支持的推理档位，缺省none兼容旧客户端；每轮冻结并纳入请求幂等比较，独立Provider不跨会话串扰。开启思考工具调用时保存并正确回传reasoning_content，不把其作为公开聊天流。
- session DTO持久投影最近一次主模型请求实际input/context_window及来源/时间；旧会话只读回溯已有Trace，不把所有轮次usage相加。窗口由Provider配置提供，不在UI假定所有模型都1M。
- 验证请求重放/档位冲突、会话隔离、上下文测量/重载、thinking工具续接与现有附件路径；真实DeepSeek调用单独记账。
- 7项定向单测及web_agent/account_sessions/reply_trace三个smoke通过；门禁新增chat_context_reasoning组。Trace/Observation响应去除reasoning_content正文，后端账本保留供实际模型工具续接；结构化独立业务调用仍关闭推理。
- 真实loopback HTTP→DeepSeek四档5次请求通过：none1次、low2次（真实Skill读取工具）、high1次、max1次，输入17297/输出1125；工具内与下一用户轮reasoning回传无400。证据 `tmp/chat-context-reasoning-live-20261021-a24f6559/report.json`。旧探针脚本参数错误、以及“简单问题必有非空思考”的过强断言失败均保留，后者四档API本身成功，不把断言调整算产品优化收益。
- 正式8765已随main重启加载；真实前端验证在8879独立SQLite账号进行，高档一轮返回账号名、actual input11240与会话DTO/刷新一致，未向正式账号写测试聊天。受控浏览器12项加入CI允许列表，新的offline_adoption12项与chat7项加入后端门禁；不据定向验证声称本轮全套CI已在远端通过。

## 内容最近更新排序（2026-10-20，隔离HTTP/SQLite验证通过）

- `GET /api/series/{id}/topic-library` 增加 `order=recent|queue`；缺省queue保持已有顺序和Agent契约，网页显式recent。
- recent在全列表筛选后、分页前排序；已入队项使用Topic创建/更新和卡片所指最新Run更新时间最大值，待选项用调研批次创建时间；同时间以ID稳定排序。内部批量读取时间，不扩展DTO、不逐项查数据库。
- 仅只读展示排序，不改Topic.position、生产队列、正式数据库、任务状态或模型输入。隔离HTTP/SQLite验证跨20条分页、旧选题新产物、待选混排/筛选、默认顺序兼容及全表原记录不变。
- `tests.test_topic_library_order` 5/5通过：27条真实隔离SQLite选题、3个Run、2条受控候选，经真实loopback HTTP验证；新排序时间投影恒定2条SELECT，无逐选题N+1。原表所有列和候选JSON未改变；不调用模型，不将受控候选视为真实调研。正式服务未重启，新参数需正常重启后加载。
- 原 `tests.smoke_topic_research` 通过，preview/入队幂等/来源保持/重启等既有调研契约不变。
- 主流程再次运行排序5/5及 `smoke_topic_library_covers` 通过：封面仍按当前有效版本批量投影，待选/未生产/缺图状态不变。浏览器4/4详见web/SPEC，不将页面纯浏览算作模型生产验证。

## 持久排队状态投影（2026-10-20）

- 提炼 `queue_state` 与调研 `queued` 在任务收件箱、Observation 中仍是活跃任务，但不冒充正在运行的 Codex。
- 不修改正式服务/数据；用隔离 SQLite 和任务目录验证排队、运行、完成与刷新。
- 真实前端两项Skill任务、两批调研均完成，校准新线程试产并确认保存成功；DeepSeek账号对话只读两批已有结果成功。正式8765进程保持不动，队列后端需下次正常重启加载，不在进行中热升级SQLite。

## Skill 校准与任务找回（2026-10-20，隔离验收通过）

- 新校准路由只处理显式选中的同栏目产物版本、参考作品和绑定目标Skill；草稿/试用复用提炼工作台，确认前不修改共享库。更新原Skill要求所见版本与同稿成功试用，备份旧目录、冲突零覆盖；不增加模型工具或开放Agent自动应用。
- 全站待处理入口只读投影现生产/调研/提炼状态，不触发任务/修复；已读状态留本机浏览器。账号Agent请求不获得跨账号收件箱与新校准入口，原作用域守卫仍有效。
- 验证在独立SQLite/受管目录/8877或独立live端口进行，正式在途任务不重启、不迁移；详见docs/skill-calibration/SPEC.md和docs/task-inbox/SPEC.md。
- 28项校准/收件箱契约与27项队列/线程关联回归通过；最终27项定向浏览器及真实Codex校准→一图试用→明确保存分别验证。首次500由真实进度datetime DTO未编码引起，已修并补回归；响应投影失败不回滚已提交Skill，Agent header不能自动应用。后台确定回滚且精确请求匹配才开放显式重新确认，不为保存重试重复生图。

## 生产并发容量（2026-10-19，隔离验证通过）

- `/api/runs/{id}/execute`、`/api/runs/batch`与账号工具仍进入同一持久化队列和宿主执行器；默认全局5工位，可由CREATOROS_PRODUCTION_CONCURRENCY设1–5。响应与Run/Revision/Attempt数据库结构不变，无迁移、无新增接口。
- 各Run现有详情、任务列表和Observation继续读取独立生产状态；待执行位置是等待顺序而非准确开工时间。同选题旧执行者退出前保留pending，后续独立选题可绕过，不造成队头阻塞。
- 测试使用独立SQLite、临时HTTP端口和本地故障生产者；正式8765在途任务和端口不变。本轮不加载新代码，不把隔离通过写成当前服务已5路运行。
- tests.test_parallel_production_api通过：Web4题+账号工具3题共用5工位，重复请求不另建Attempt，最终7份待验收结果可经HTTP刷新读取；局部受控Producer不等同真实模型或浏览器测试。关联118项及5组smoke通过。

## Skill 参考图与删除入口（2026-10-19，隔离验证通过）

- PUT /api/producer-skills/{id}/assets 接受 path/data_base64/expected_digest/overwrite(false)；DELETE 同路径接受 path/expected_digest。仅受管图片，复用目录摘要、错误响应与归属守卫。
- DELETE /api/producer-skills/{id} 接受 expected_digest，网页显式确认；使用中409并返回关联栏目，内置拒绝，未使用者可恢复移出库。账号 Agent 不开放全局删除。
- 当前会话 image-actions/skill-asset 把自己的附件转换为图片字节；核验 Skill 为当前账号绑定，禁止任意本地路径。正文原 PUT files/content 保持兼容。
- 目录移除入口拒绝所有Agent来源头（含总览），账号assets PUT/DELETE及会话转换分别核验绑定与当前会话；二进制不允许模型直接传本地路径。错误统一为409摘要/引用/占用、413大小、403只读或权限、422无效内容。
- 32项关联本地/真实loopback测试及独立前端DeepSeek回归通过，正式服务因正在生产未重启，OpenAPI目前无新assets路径；保持此状态不打断用户，完工后重启才生效。

## 自写 Skill 草稿的网页与会话入口（2026-10-19）

- POST /api/skill-extractions/author 将现成提示词/SKILL.md 与可选参考图保存为 ready 草稿，无 Codex worker/thread，不注册目录、不创建内容。复用原草稿文件预览、编辑、CAS 入库及试产契约。
- Agent 经当前会话 image-actions/author 入口转换附件；记录任务归属后才保存，禁止跨会话读写草稿，错误或请求内容冲突转为真实 HTTP 状态而非裸 500。
- 同请求重放不重复建草稿，同请求不同内容 409；服务关停、无效文件/声明、图片损坏或复制失败不伪报成功。账号 Agent 新栏目仍经过原绑定和归属检查，不开放任意路径写入。
- 正式数据不作试写；真实前端 DeepSeek、隔离文件/SQLite 的实际验收结果见 integrations/SPEC.md 与 web/SPEC.md。
- 新增17项自动测试及三组原smoke通过，真实Chrome→DeepSeek→工具→HTTP→文件/SQLite往返通过；正式服务已在无活动任务时重启加载，新路由健康检查通过，已有业务对象数量不变。不将新增接口通过等同于全项目无故障。

## 多图参考选题/制作（2026-10-18）

- 增加按栏目归属的reference-images上传/只读图片、reference-inputs冻结、reference-production直接生产；调研既有路由可接reference_id。账号Agent以当前会话附件调用image-actions/reference，所有资源再次核验。原图不进共享Skill。
- 直接制作预检有效账号/Skill，持久保存同一Topic/Run/执行授权；同请求不同内容409，响应丢失重放不重画。候选入队无论Web专用选择还是Agent通用queue均保留引用；公开Run只返回参考元数据，不返回image.path。旧协议与状态边界不变，详见 `docs/topic-references/SPEC.md`。

## 按版本下载原图（2026-10-18，已验证）

- 新增只读 GET `/api/runs/{run_id}/revisions/{revision_id}/download?digest=…`；复用 Run/Revision归属、Manifest身份、路径范围、整体摘要、逐图摘要与格式校验。只返回所选版本完整原图；单张返回真实图片MIME/扩展名，多张返回按页码命名的ZIP（不含内部快照/日志）。旧 `/download` 已批准发布包不变。
- 无新增依赖/数据库表/Agent工具，不触发模型、审批、发布或写业务状态；文件缺失/变更/跨Run/摘要过期则整体拒绝，不交付缺页ZIP。响应attachment/no-store/nosniff，文件名不使用调用者路径。
- 验收覆盖单张、多张、历史版本、坏摘要、缺图/篡改/路径逃逸及零写入；前端回归真实本地HTTP与独立SQLite，正式服务不启动。
- `smoke_studio_artifacts`通过：1/5/6图、ZIP顺序与逐图原字节、跨Run404、无摘要422、旧摘要/缺图/坏图/CRC坏图/Manifest及图片逃逸409、旧验证无逐图hash兼容；下载不改变Run/事件/Producer调用次数，旧已批准ZIP仍包含文案。额外拦截Pillow的SyntaxError/解压炸弹异常，避免下载失败抛裸500。
- `smoke_native_artifact_web`通过：native-v1 checkpoint原图原字节导出、冻结Skill篡改拒绝；`smoke_studio_api`关联回归通过。浏览器隔离真实HTTP最终4/4，详见web SPEC；不改正式文件/状态，不把本地工件测试写成付费模型Eval。

## Skill 显示名（2026-10-18，已验证）

- files/content PUT 增加可选 display_name，仅 SKILL.md 可用，复用原写权限、摘要CAS与原子保存；内部name/注册ID不变。目录/文件树/保存回执加display_name，旧客户端请求仍兼容。
- 账号元数据树同步提供显示名别名以便自然语言定位，仍只暴露name/description层级，不增加正文、图片或动态数据；已有get/update Skill工具直接使用原接口，无新增工具。
- 真HTTP/隔离文件与前端操作验证中文改名、合法YAML、旧摘要冲突/越权拒绝，正式库不试写。
- Skill编辑/文件浏览与账号context/scope回归通过；正式服务已加载新Schema，目录与原文只读检查通过，正式35条Run、9个Skill保留。具体字段写入与前端回归分别见 integrations/web SPEC。

## 同选题从头重做（2026-10-17，已验证）

- 复用 POST `/api/runs`，显式 `mode=fresh`、来源 Run、所见 version 与 request_id；新 Run 使用同一选题和栏目当前 Skill，旧 Run/图片不覆盖。不额外增加模型工具。
- 创建和执行仍分开：新 Run 经既有 `/execute` 进入持久化串行队列。响应丢失可用同一 request_id 核对，不能再次新建付费任务；来源正在运行、已经被替代或版本冲突时拒绝。
- 账号请求同时核验来源 Run 与选题归属，跨账号零写入。网页清楚区分“提出返工”（保留原生产依据）和“从头重做”（最新 Skill、干净上下文）；普通作品讨论走 CreatorOS 看图，不自动编辑 Skill/生产。
- 验收：隔离 SQLite/真实 HTTP 的幂等与权限、最新 Run 投影及新旧工件；浏览器点击从头重做并刷新，原任务可读、新任务待验收、一次执行授权。模型行为验证与本地故障注入分列，正式内容与既有 Eval 不改。
- 已验证联合35条测试、smoke_studio_api/native_artifact_web通过；真实 CUA→DeepSeek→Codex 单图重做待批准，旧 Run未改、最新关联正确。完整范围/证据见 runs SPEC。等待正式8765生产完成后，核实活动生产/聊天/提炼及授权待执行均为0，重启加载修复；健康200、OpenAPI含fresh、新前端哈希已就绪，原28条Run仍在，7条无队列授权的旧queued未自动执行。

## 批量生产队列（2026-10-17）

- 用户明确提交多个已入队选题，Web/Agent 共用 POST `/api/runs/batch`，每项独立返回接受或失败；重复提交复用同 Topic Run，不恢复旧失败/中断/返工版本。仅 `/execute` 显式授权恢复。
- SQLite 保存执行授权队列，单消费者顺序执行；未开始任务重启后继续，运行中结果不明转待检查，不盲目重开生图。旧 queued Run 无队列授权不自动执行。Run/Revision/Attempt 和人工验收仍是业务真相。
- 账号来源请求先检查整批归属，混入其他账号则整批拒绝且零写入。合法批次中单项失效不阻挡其他独立项；HTTP 响应丢失不自动重发付费任务。
- Run 详情按需展示 production_queue 调度状态；批量按钮只提交明确选择项，不默认生产所有选题，不接定时发布、多容器或自动审批。
- 验收：隔离 SQLite 的 FIFO/重复提交/失败继续/重启/取消与旧 queued 不启动；真实 loopback 的 Web/Agent 共用路由及跨账号零写入；浏览器多选提交、刷新和失败路径。故障注入不冒充真实 Codex 生图验证。
- 已验证：队列故障注入10项、真实 loopback 批量接口/工具5项通过；工具契约/导航相关合计23项通过。浏览器 batch + 原工作流 + gallery + 账号对话9/9通过；单条旧入口 API、Agent、账号作用域及生产服务回归通过。改掉旧测试的 busy 前提，存储 smoke 从写死旧迁移号改为检查 head，并在失败时关闭连接，不修改旧 Eval 结果。
- 真实 DeepSeek + Codex HTTP 集成另通过两条串行单图任务，最终均待人工验收、独立 thread、图片 HTTP200；保留首轮测试 ACL 失败证据，详见 runs SPEC。正式服务原进程未停止，重启 `python -m creatoros.web` 后才加载新队列和0012新表；没有替用户迁移或生成正式内容。
- 最终统一可靠性回归18/18组通过；首轮17/18为旧迁移测试版本断言过时，修正后另存新报告，路径与边界见 `docs/reliability/SPEC.md`。

## Agent 读取已有反馈（2026-10-17）

- 总入口与账号入口开放 query_feedback，沿现有 analytics 只读路由；账号参数由宿主绑定。没有修改反馈 HTTP/数据库契约、前端或固定账号上下文。
- 业务提示要求按实际指标、采样时间解释，缺失和同分如实处理；选题建议不自动转成调研/入队/生产。细节及验证见 tools/SPEC.md。

## 账号反馈 API（2026-10-16）

- GET `/api/creators/{id}/analytics` 只读 SQLite、no-store；PUT `analytics/settings` 严格布尔开关；POST `analytics/refresh` 提交短后台任务，重复点击复用在途采集，不调用发布。UI 返回状态不等同采集完成。
- 账号 Agent 来源头只能访问同账号路径，不能跨账号读统计或改开关。未将新动态指标写入 account_context，也未新增固定 Prompt；日常页面读数据库不触发 MCP。
- FeedbackService 与 Studio 生命周期绑定，先退出反馈再关闭发布服务。具体节流/状态持久化见 publication SPEC。测试区分真实平台只读反馈与隔离故障注入，不用测试发布验证。
- 真实隔离 HTTP/SQLite `smoke_feedback_http` 通过：默认只读空投影不调 MCP，严格布尔/额外字段拒绝，跨账号 GET/PUT/POST 拒绝，开关往返与不存在账号错误；现有发布确认和账号作用域回归通过。
- 正式服务 8765 已运行新代码：真实前端保存开启状态，后台心跳追加真实反馈，服务重启后开关/快照/节流保留；发布记录逐表保持不变。具体时间与数值见 publication SPEC。

## 发布单与网页确认（2026-10-16，已接线，真实发布待本人确认）

- POST/GET `/api/runs/{id}/publication-request` 准备/读取冻结发布预览；GET `/api/publication-requests/{id}` 查状态，POST `reconcile` 核对，POST `metrics` 手动刷新互动快照。
- POST `confirm` 只接受同源本机网页，拒绝账号/总览 Agent 来源头；以当前预览 digest 确认，服务再核实审批、版本、文件与实际平台账号。浏览器不是权限真相；单机防误操作不是对恶意本机程序的安全认证。
- 账号 Agent 沿现有 ScopeGuard 校验发布单与 Run 的真实归属。只暴露准备/查询工具，不开放确认接口；网页确认后由宿主执行一次发布。
- 外部发送任务脱离 HTTP 请求保存进度；超时/中断进入待核对，不自动重发。详细状态、去重与测试见 `creatoros/publication/SPEC.md`。
- 集成验收：发布 HTTP smoke、账号 Scope smoke、旧人工发布回归通过；真实 MCP 只读账号/笔记可达。正式前端完成授权验收与准备发布单，重启后状态保留，发布确认由用户执行；没有把受控故障注入计为实际发帖。

## 聊天图片附件（2026-10-15，真实链路验证完成）

- 用户授权代写：聊天 Ctrl+V/选图上传、DeepSeek 实际看图、查产物/参考图后由 CreatorOS 修改 Skill；Codex 不承担已有 Skill 修改。图片也可显式提炼 Skill 或导入指定栏目待人工验收，不自动生图、批准、发布。
- 附件保存到具体 Session 目录，账本及 Trace 只记录附件引用；Provider 发请求时解析为图像内容，不把 Base64 写入会话或 SSE。最多 6 张/次、4 MiB/张，校验真实格式、尺寸、摘要和路径；跨 Session 附件拒绝。
- Web 视觉模型使用官方文档明确支持图片的 deepseek-flash；其他 CLI/解析模型默认不变。用户上传是数据，不是系统指令；模型只能经受控工具读现有 Skill/Run 图片。
- 同 request_id 的文本和附件顺序必须一致；未知发送结果不自动重提。已发送图可刷新查看；失败保留前端草稿。上下文预算计入图片，摘要保留引用不冒充已看细节。
- 验收：隔离 HTTP 校验图片/越权/幂等/重启、消息及 Trace 无 Base64；受控 Provider 仅用于故障与接线；另从真实前端粘贴两图请求真实 DeepSeek 验证。正式数据不改。
- 实际链路：Chromium Ctrl+V 两图 → deepseek-flash 正确比较文字/颜色 → read_image 看 Skill 参考图并保存指定的一行修改 → 原图导入栏目、Run 为 awaiting_approval → 聊天提炼、真实 Codex 生成可浏览的单 Skill 草稿及两份 assets。没有自动入库、生图、批准、发布。隔离会话 `d1ff74f4-59de-4909-b088-bb676293a669`，导入 Run `26614d74-dd9d-412d-87ad-45a0b7a0f47e`，提炼 job `5b31bc96fa792bda49e29ec6d517dcf66e24498a84015d8c8e5980dc55a03d3d`；原始证据留在忽略目录 `tmp/live-chat-images-20261015`，不是新的 Eval 分数。
- 故障/边界回归：附件单测 16/16、真实 loopback HTTP/SQLite 图片与业务 smoke 通过；覆盖损坏 JPEG、伪造元数据、Windows junction、同摘要重传修复、跨会话拒绝、异文重放冲突。受控 Provider 仅测接线与故障，不算真实视觉质量。
- 读图的宿主消息标记为 tool_images，API 请求去掉内部标记，压缩不把它当新用户轮；完整工具批次与原用户要求保留。7 项原压缩回归、Web 聊天、回复 Trace、账号隔离/会话、原产物服务/读取 smoke 通过。传输预算保留最近 24 MiB 图像，旧图保留可重读引用；图片 token 是估算而非计费用量。
- 复现入口：`python -m tests.live_chat_images --serve --root <仓库>/tmp/<新隔离目录> --port 8905`，然后运行 `web/live-images` 的显式真实浏览器测试（会调用收费模型）；原报告可只读复验，不重发 query。不启动正式 8765、不覆盖历史 Eval。

## 任务筛选与显式扩展 Eval 数据集（2026-10-11）

- GET 账号任务允许 statuses 重复查询参数（1–14 个合法状态），默认行为不变；先按账号/栏目筛对象，再过滤状态，再计算统计，拒绝非法/空状态，不放宽其他账号访问。
- create_app 可显式注入 eval_cases_path，EvalStore 只接受该文件实际定义的 E/A/S/B/P 两位 ID；普通服务仍是原 12 题。独立工作台评测用单独报告根，不自动混入正式业务或声称扩展已运行。
- 本地真实 SQLite/HTTP、Pydantic 参数边界和默认/独立数据集只读回归通过。正式 8765 不重启、不迁移数据、不生图或发布。

## 入队回执与账号工具契约（2026-10-11）

- 原 POST queue 复用同一事务服务，重放的 topic_ids 改为读取首次成功计划，不返回本次未落库的随机 ID。
- 新增只读 GET `/api/series/{series_id}/queue/receipts/{request_id}`：成功读取原回执，缺回执返回 unknown，不执行、不取消、不自动重试；Cache-Control=no-store。绑定账号同时核验栏目当前归属与回执冻结归属。
- Web 账号模式对查询账号/创建栏目参数由宿主补齐；模型显式覆盖不同账号先拒绝，原 API Guard 继续逐资源检查。CLI 原参数兼容，未开放的账号工具不会因 Schema 投影增加。
- 正常路径使用真实隔离 HTTP/SQLite，前端 DeepSeek 验收与故障注入分列；正式服务和正式数据未操作。验证细节见 `docs/agent-studio/web-chat/CONTRACT_RESULTS.md`。

## 账号 Agent Eval 只读接口（2026-10-10，完成）

- `create_app(eval_root=...)` 注入独立报告根，默认 `data/agent-eval`；EvalStore 构造与 GET 不创建目录、不查 SDK、不调用模型/工具、不更新业务库。复用账号 guard 与本机写入限制，不引入公网权限系统。
- GET `/api/eval` 返回 12 题公开定义与已有报告状态；`/runs?case_id=...`、`/runs/{id}`、`/runs/{id}/evidence?name=...` 读取白名单证据。没有报告就是未运行，损坏/路径不安全/超限明确错误，不假称通过。
- POST `/api/eval/runs/{id}/review` 仅显式人工结论，CAS 绑定 report + review，冲突 409；独立原子写 sidecar，不改报告。自动通过要求真实执行完成、全部检查和四维通过；执行失败固定投影失败，不能人工把自动失败改为通过。
- `tests.test_eval_store` 11 项通过；`tests.smoke_eval_api` 真实 loopback HTTP/SQLite 验证零 GET 写入、账号 scope 403、路径/凭证/错误、复核/CAS/重启持久化。故障注入只验证接线，不冒充模型质量成绩；E01 真实调用另见 `docs/agent-eval/RESULTS.md`。
- 关联 `smoke_account_scope`、`smoke_account_sessions`、`smoke_account_context` 全通过；正式服务/数据库/Skill 不改，不启动 Codex、生图或发布。

## 选题移除（2026-10-09，完成）

- 用户授权实现：卡片/详情统一移除待选及正式选题。无 Run 的 Topic 物理删除，有 Run 的保留原行/状态/图片/Trace 并退出内容工作区；生产/验收进行中返回 409，用户先停止，不自动取消。
- POST `/api/topics/{id}/remove` 带 request_id；待选还带 batch_id/candidate_id，核验确定性 ID 与栏目归属。SQLite 保存 TopicRemoval 作为移除事实及幂等回执；不改原候选 JSON，不因正式 Topic 删除而恢复成待选，不额外调用模型。
- 默认内容列表及账号/栏目计数过滤移除标记；Observation 和直接历史 Run 仍可读。移除后的选题不得被旧 Preview 重新入队，也不能从旧 queued Run 启动/返工生产。原严格 delete 接口兼容保留。
- 验收：实际隔离 HTTP 删除/归档/待选移除、刷新/重启/重复/异文请求、越权、活跃任务/旧 Preview 竞争、历史图片与原 Run 不变；隔离迁移旧数据保持/metadata 无漂移；真实浏览器点击/失败/刷新与桌面手机截图。不改正式库、不生图/调研/发布。

- 活跃作品讨论同样阻止移除；归档作品仍可只读讨论。queued Run 可以移除但不能重新 claim，历史 Run 列表/详情仍返回原状态与图片、动作投影仅 view。总览与账号任务摘要不再把移除 Run 当作当前生产工作，历史 Observation 不过滤。
- 栏目删除将移除记录计入历史，不能因删空栏目级联清掉回执。精确 request_id 重试按首次冻结账号核验，栏目转移后的新请求按当前归属核验；候选来源异文 409，缺失批次 404。本地 Studio 共享锁与事务控制，不宣称跨进程/分布式幂等。
- 隔离真实 loopback `smoke_topic_remove` 与迁移 smoke 通过；审查捕获的栏目级联、隐藏项调序、总览动作和转移后回放边界均补回归并通过。首版历史 Topic 测试把生产后的状态误设为 queued，修正为移除前后状态比较，不改业务迁就夹具。
- 相关操作计划/解析、PendingOperation、栏目删除、账号作用域、封面查询、Observation 与调研 8 个 smoke 通过；前端真实交互与故障注入分别列在 `web/SPEC.md`。正式服务未重启，日常启动自动应用 0009 迁移后才加载新接口。

## Agent Skill 文本 JSON 路由（2026-10-09）

- 新增只读 `GET /api/producer-skills/{id}/files/text?path=...`，复用受管目录/路径/UTF-8/512 KiB 校验，仅返回 JSON 文本与整个 Skill digest/可编辑状态。图片及其他二进制在读取解码前返回 415 / `skill_text_only`，不向 Agent 返回图片。
- 账号 scope 仅放行已绑定 Skill 的精确 GET 文本路径；跨账号与 PUT 文本路由均拒绝。浏览器的混合 `/files/content` 图片预览、既有 PUT 编辑和默认分页正文保持兼容。
- 验收见 `tests.smoke_producer_skill_read_tool`，使用真实隔离 loopback HTTP/SQLite，包含 Markdown 正例、图片负例与跨账号 403；不改正式数据、不调用模型。
- 新文本读取 smoke、原文件浏览 smoke、原子编辑 smoke 均通过；读取前后隔离数据库及 Skill 文件字节/修改时间不变，原混合路由正常返回 JPEG 字节，GET 错误不暗示写入。

## Observation 只读投影 · 2026-10-08（完成）

- 新增按需树/详情 API，复用 SQLite、会话请求快照、调研/讨论记录与受管 worker 日志；不新增业务状态、不调用 SDK 查询全局会话、不重放工具。
- ID 必须解析到已知业务归属，文件读取受目录边界与脱敏限制；仅按明确业务 ID 关联聊天与任务，旧记录未保存的数据标记缺失。
- 接口与验收见 `docs/observation/SPEC.md`。
- 时间线串联要求、模型/工具、Codex 执行与宿主验收；失败记录直接展示已保存且脱敏的错误诊断。聊天快照与业务任务双向引用，仅基于明确 ID，不推测来源。
- 隔离真实 SQLite/API/文件 smoke 通过；GET 前后持久文件 hash 不变，作用域 Agent 请求全局观察接口返回 403。正式库 mode=ro 历史节点抽查与浏览器导航通过，缺失历史仍如实标记。

## CreatorOS ↔ Codex 任务投影与 Agent 职责（2026-10-08，完成）

- Web 账号 Agent 与总览/CLI 共用 CreatorOS 工具适配器；账号可按需读取只读任务摘要、讨论已验收作品，并在明确要求时建立待执行返工版本。
- `GET /api/creators/{creator_id}/tasks?series_id=` 投影现有生产 Run、调研 batch 和讨论记录，不建立第二套状态。响应为 `items`、`summary(active/awaiting_approval/failed)`、`as_of`。
- 账号 Agent Scope Guard 显式允许当前账号任务查询、Run 讨论读写与返工写入，并逐项检查当前账号和 Run 冻结 creator_id。含其他账号冻结 Run 的已转移栏目不向当前账号投影。
- Web Agent 的讨论工具先提交一次，再 GET 同一 Run 的讨论历史观察相同记录直至终态；活动绑定当前 tool entry。超时、观察中断与状态读取故障均返回任务句柄，不自动 POST 第二次。Run 摘要额外投影每版 ID/digest/availability，供选择明确讨论版本。
- 讨论/返工/Skill 编辑的授权边界写入共享账号与 Web Agent policy：讨论不等于返工；用户明确返工才建立新版本；普通反馈不自动改共享 Skill。图片路径/摘要本身不能作为看过图的依据。
- 验收：隔离 SQLite/HTTP 对照现有 Run/Research/Discussion 记录检查投影字段和状态计数；跨账号与栏目转移拒绝；工具执行不隐式生产、批准或发布。
- 2026-10-08 验证：工具到路由 loopback smoke 带 creator session scope，确认讨论工具等到同一记录终态并把活动写回当前工具项，返工严格请求体成功创建待执行 revision；任务投影及原讨论/研究聊天 smoke 通过。只使用隔离数据库/测试图片与可控 Reviewer。

## 调研原聊天观察与启动健康（2026-10-05）

- Web 账号/总览聊天复用原 Loop，研究工具从“提交即结束”改为宿主等待同批次终态、公开活动随聊天 SSE/GET 持久化投影。最多 4 个不同会话并行，各自 Provider/账本/Trace；同一会话仍只接受一个指令。不把研究执行器改成并行 worker，也不改变生产异步提交。
- `/api/health` 用与调研相同的实际可执行文件 resolver，默认 SDK 捆绑 runtime、兼容有效显式路径/PATH；依赖缺失返回 unavailable。仍不探测登录态或额度、不发模型请求、不返回绝对路径。
- HTTP/SQLite 受控研究与故障、账号作用域/上下文、回复 Trace、原 Web/API/工具测试通过；真实 DeepSeek + Luna 联网探针 74.7 秒 ready、原聊天持有终态结果、1 个有来源的词汇候选，隔离目录 `tmp/research-chat-live-3v6w09qo/`。真实坏例旧记录只读解释，不自动重跑；细节见 web-chat/topic-research SPEC。

## 自由 Skill 组合与栏目移除（2026-10-05，完成）

- Skill 组合框不按角色限制：一个完整 Skill 直接创建栏目；多个 Skill 生成新的融合草稿，沿用提炼工作台查看文件、编辑、改稿、试用及显式入库，原 Skill 不改写。入库不自动绑定或生产，用户再次选择创建栏目。
- 融合请求记录输入 ID 与文件 digest，冻结本地资源；模型仅在任务草稿目录工作。刷新读取同一任务，未知结果重试复用 request_id，不自动重发收费任务。纯内容 Skill 不伪装为图片生产能力。
- DELETE `/api/series/{id}` 使用 request_id / expected_revision：无选题且无历史时删除，有历史时移出工作区但保留数据；活跃生产或调研拒绝移除。现有账号作用域和历史 Run 读取保持有效。
- 验收：完整单 Skill 实际建栏；任意多 Skill 先草稿后人工确认；融合失败/取消保留原库；刷新恢复任务；空栏目移除、不空栏目确认且旧产物保留；固定导航与绑定 Skill 详情可真实操作。

### 融合 API 后端切片（2026-10-05）

- `POST /api/skill-extractions/merge` 接受 2–8 个不同已登记 ID、`request_id` 和可选 `instruction`；返回 HTTP 202 的普通提炼 Job，增加 `task_kind: "merge"` 与源 ID/name/role/digest。一个 Skill 沿用既有直接建栏路径。
- 任务使用现有查询、事件、取消、编辑、改稿、试用及显式保存 API；同 request ID 重试复用任务，参数变化返回 409。隔离 HTTP/SQLite smoke 通过；前端全量 48/48、随后晚响应工作区回归 5/5 通过，真实 SDK 融合 ready（281.3 秒）且无正式库写入。模型输出质量不由接口验收代替。

## 安装 Skill 文件浏览 API（2026-10-05）

- `GET /api/producer-skills/{id}/files` 返回注册 Skill 的 `id/name/description/role` 与相对路径文件清单（`path/kind/size`）；只覆盖当前受管工作副本或固定内置 Skill，不公开绝对路径。列表与内容响应均 `Cache-Control: no-store`。
- `GET /api/producer-skills/{id}/files/content?path=...` 对 Markdown/UTF-8 文本返回完整 JSON 正文。只有 PNG/JPEG/WebP/GIF 后缀作为图片候选；还要验证实际解码格式在该栅格白名单中，响应 MIME 根据真实内容返回，后缀不匹配时仍原样返回文件字节，坏图拒绝。类型不支持为 415，文本/图片超限为 413，未登记、越界、缺失或链接路径为 404。拒绝 HTML/SVG 原样服务，现有分页 `/content` 保持兼容。
- 账号 Agent 会话的共享 Skill 只读访问仍遵循既有受限访问规则；普通浏览器不需要会话头。隔离 HTTP/SQLite 与受管文件树只读证据见 `tests/smoke_producer_skill_files.py`；不触发安装、生产、模型或正式数据写入。

## 内容库批量封面投影（2026-10-05）

- topic-library 的已入队条目新增可选 cover_url/card_count；只对当前分页关联的 Run 批量读取活跃 Revision/Attempt，一次 SQL，不让浏览器逐卡请求完整 RunDetail。待选条目返回空封面，既有筛选、身份、顺序与动作不变，无数据库迁移或新路由。
- 复用现有 pack 和受控图片端点，校验 Run/Revision、冻结输入归属、受管 Attempt 路径、保存的 digest/首图 checksum 及栅格解码；当前新版缺失产物、缺图/坏封面返回 null，不借旧版或部分预览。完整图片端点仍保留原产物校验；列表不重新 hash 整套图片。
- 隔离 `smoke_topic_library_covers` 通过：真实 SQLite/HTTP，合法封面 200、一次批量 SQL、无 Run/事件/生产写入、当前新版本无图不回退、删首图后不投影。`smoke_studio_artifacts`、`smoke_topic_research`、`smoke_native_artifact_web` 关联回归通过。正式数据及原图不变，未生图或发布。
- 正式前端接线、账号会话、轮询与真实只读 DeepSeek/CUA 验证见 `docs/studio/account-workspace/SPEC.md` 的 2026-10-05 记录。

## 聊天回复 Trace（2026-10-05）

- 复用既有会话和 Context Trace：宿主 request_id 关联本轮所有主/摘要模型请求；Web 显式保存实际 ModelContext、模型回复与工具结果的本地快照，不影响原 CLI 默认行为。
- GET `/api/agent/sessions/{session_id}/turn-trace/{turn_id}` 只读请求索引；其 `/requests/{request_id}` 按需返回一份正文快照。校验 session/turn/request 归属和受管路径，坏/缺快照 404，合法响应 no-store；完整正文不进入聊天 SSE。
- 旧回复不补造请求正文，失败/中断和缺工具结果保留未知语义。已知凭证脱敏，不展示传输 headers 或模型内部思考，不重放工具、不访问其他会话。实现/验证及 DeepSeek Harness 参考集中在 `docs/agent-studio/web-chat/SPEC.md` 的“回复级 Trace”。

## 账号上下文树与 Skill 按需正文（2026-10-04）

- AgentChatService 由宿主提供 CreatorContextBuilder 回调；每条账号指令构建当前账号/全部栏目/绑定 Skill 元数据，成功业务写后刷新。总览和 CLI 不自动注入树，没有会话或数据库迁移。
- GET `/api/producer-skills/{id}/content` 只读受管 Skill 根目录正文，分页最多 4000 字符；账号 guard 放行共享库只读访问，Web read_file 仍仅限当前会话归档。
- 新建账号 Agent Run 前校验绑定的当前本地 Skill 元数据，不自动恢复缺失文件；已有 Run 仍使用原冻结输入，不因当前工作副本失效而改写旧任务。普通 UI/总览生产路径保持原状。
- 树进入请求投影及本地 Trace，不进入聊天账本。没有新 Trace 面板或前端样式改动，细节与验收见 `docs/agent-studio/web-chat/SPEC.md` P2。

## 账号 Agent 会话与作用域（2026-10-04）

- 总览和账号入口复用既有 AgentChatService/Loop；创建会话可指定 creator_id，绑定不可通过聊天或更新接口改变。旧 JSON 无字段仍为总览，不迁移表。
- 账号身份来自持久化会话，经 RuntimeContext → StudioClient 固定头传播。AgentScopeGuard 核验账号、栏目、选题、调研批次、Run；目录只投影当前账号，未列入范围的接口拒绝。全局安装/提炼/转移仍留总览。
- 同一 Studio 进程内，账号请求的归属检查和路由执行与 HTTP 写操作互斥，防止栏目恰在检查后被转移。不是跨进程数据库锁或多租户认证；直接改文件/数据库不属于此防误操作边界。
- 移动栏目如带旧账号冻结 Run，账号 Agent 保守拒绝其选题/调研/生产操作，由总览处理。普通本机 UI、CLI 和总览仍保留全局操作能力。
- 范围、兼容、测试与下一阶段见 `docs/agent-studio/web-chat/SPEC.md` 的 P1；账号记忆/自动运营仍未添加，2026-10-05 已补有界多会话并行，见本文件最新记录。

## 产物提炼 Skill 接口（2026-10-02）

- 2026-10-05：GET `/{job_id}/events` 返回当前操作最后 1–100 条公开摘要，before_id 读取更早页；GET `/{job_id}/events/{event_id}?stream_id=...` 按需正文，两个接口 no-store、只读、校验任务及当前操作归属与受管文件路径。正文不塞入 Job 列表，旧任务空记录不补造。失败 Job 的 error_type/文案反映已记录原因，旧记录只读投影；不泄露错误中的绝对路径或已知凭证。隔离 HTTP/分页/负参数/越界/链接与旧任务零改写通过，详情见 artifact-to-skill SPEC V4。

- `/api/skill-extractions` 共用持久化提炼服务，上传/提炼/查询/取消/确认入库拆开。图片限制为静态 PNG/JPEG/WebP，每张 4 MiB、最多 6 张；不接任意路径或远端下载 URL。
- 复用本地 JSON 写入门禁和 Skill catalog；输入幂等与草稿摘要检查，刷新不重提，确认不绑定栏目。Agent 调相同 API。
- 隔离 HTTP/SQLite 覆盖四模式、坏图/重复图/缺图、幂等冲突、失败取消、重启、篡改拒绝、部分登记后重试不重复。真实 Codex 图像输入在独立 catalog 验证，不改正式库；细节见 `docs/artifact-to-skill/SPEC.md`。

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
- 新增 DISPLAY_SCOPE_RULE 并接入 WEB_INSTRUCTIONS，明确筛选与禁止重述约束同样适用于补充说明。该规则仍保留；旧运营 Eval 已于 2026-10-09 退役，当前账号任务与判分约定见 docs/agent-eval/SPEC.md，不继承旧成绩。
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
