# ContentRun SPEC

## 2026-11-04 · 已分析 BGM 的视频输入

视频制作选用账号音频时，若该音频已有分析，提交时与音频一起按摘要冻结到任务素材；后续池中内容变化不改变已授权任务。尚无分析时仍可使用音频；直接提交分享原文的既有路径继续可用，不依赖导入或预先分析。已有任务和冻结输入不回写。

## 2026-11-03 关闭与恢复中的未知交付

- 执行器关闭或心跳续租失败时先通知在途 worker 停止，保留 owner 等待实际结果；worker 超过关停时限则保留执行锁和生产状态，不提前声称可以恢复。图片或视频结果未知、视频证据冲突与未确认中止优先于普通取消，记为不可技术重试。即使 lease 已到期，仍须在 SQLite 写事务内确认 owner 未变、Run 仍 producing 且 Attempt 仍为当前 running 才可补记未知；不得覆盖新 owner 或已完成结果。
- 持有数据库执行独占锁且已确认执行记录清洁后，重启检查原 Attempt 的图片提交证据及视频 checkpoint：未确认或冲突记为不可重试失败，保留原文件和队列记录，不创建新 Attempt；无新增证据的旧任务仍按原规则标记 interrupted，已完成证据仍可走既有离线恢复。若 guard 写入失败且进程在数据库收到未知结果前直接崩溃，原 Attempt 与旧无证据任务可能无法区分，不能保证单凭重启检查识别，需人工核对。
- 隔离 SQLite、独立 Windows 进程和受控 Producer 覆盖崩溃后图片/视频未知与旧任务、关停、续租失败、过期和新 owner；测试不调用真实模型，不证明外部幸存进程可由缺失日志识别。
- 未知或明确视频失败写入数据库失败时，在仍属原 owner/Attempt 的条件下再尝试一次；仍失败则保留执行记录和独占边界、暂停派发，不能用普通关停状态覆盖。队列完成在写事务内核对原 Attempt、队列授权版本及当前状态，旧 worker 不得完成新 owner 的队列行。
- 重启按同 Revision 从新到旧检查可用 Attempt：空的新 Attempt 不遮住先前未知证据；图片 checkpoint 声明回执却缺失或摘要冲突、视频已接收文件缺失或摘要冲突均不可重试；已持久的视频终态失败保留原原因。合法已接收视频的字节和回执一致性只证明可继续离线验收，不代替媒体解码。

## 2026-11-03 栏目转移与未开始生产

- 显式入队或恢复前核对当前账号、栏目、选题与 Run 冻结归属，且在重置队列状态与历史错误之前拒绝不一致；claim 时再次核对归属及 Revision/Run 身份。若排队后栏目已转移，派发前拒绝该任务、队列标为 `needs_attention`，不创建 Attempt、不调用 Producer，已有 Run、输入、历史回执和文件保留。队列项进入 `needs_attention` 后，即使栏目转回原账号也不会自动重派；用户可再明确恢复。
- 同一 Studio 内栏目转移按执行器锁 → Run 写锁与 claim/提交串行；已派发的执行者不被取消，锁不覆盖模型生产时长。独立 HTTP/隔离 SQLite 与受控 Producer 验证转移时竞争、在途完成、等待项停止、其他任务继续、归属恢复后的显式重试及旧错误保留；不证明真实模型生产。该互斥仅限同一 Studio 进程，直接改数据库或其他进程不受此锁保护，不支持多个宿主共享同一 SQLite 作为并发协调方案。

## 2026-11-03 图片提交结果未知

- 图片交付提交写入可能部分成功时，生产报告不可技术重试的 `image_delivery_unconfirmed`，保留原 Attempt、回执和图片；即使同时收到取消，也不覆盖为可重试的用户中断。同 Revision 显式恢复也不得启动新回合，须先核对证据。检查命令、旧回执兼容及断电限制见 [交付 SPEC](../delivery/SPEC.md)。

## 2026-11-03 Windows 执行记录共享冲突

- 执行日志的临时文件仍先 flush 和 fsync，再原子替换；Windows 替换或删除遇系统错误 5、32、33 时，在独占锁与 guard 内部互斥锁下最多等待约 1 秒并重试。每次重试前核对旧日志字节，发现变化则拒绝覆盖；其他错误或超时继续抛出，保留旧日志和已登记 owner，执行器停止派发，不自动重试生产。该锁只协调同一数据库的 CreatorOS 执行器，不承诺与任意外部写入者原子比较交换。
- 文件替换或删除已经完成但随后返回共享异常时，变更检测拒绝同进程继续派发；最后一条日志可能已不存在，不能从内存状态推断它会跨重启保留。隔离子进程重启验证 producing/running 恢复为 interrupted/needs_attention，已完成任务保持 awaiting_approval/succeeded，均不自动再次调用生产；这不能证明缺少日志时能发现仍存活的外部进程。
- 隔离 Windows 真文件句柄验证限时及持续占用、末 owner 删除、异常变更保护、多个受控生产任务和队列暂停。该冲突能复现历史 WinError 5，但原始日志未识别当时占用者；不据此断言原始占用来源。受控 Producer 不代表真实模型执行。

## 2026-11-03 显式恢复的队列历史

- 显式重派同一 Run/Revision 的 failed 或 needs_attention 队列行前，将状态、版本、错误及提交/开始/完成时间追加到 `production_queue_history`，与队列重置同事务；Run 详情的 `production_queue.history` 可按顺序读取，Agent 模型投影只包含历史条数。pending/running 重放不会重复追加；旧记录不回填。历史写入失败则整个重派回滚，不启动生产。
- SQLite 无绑定 session 的入队先取得写事务，防止独立提交者同时归档同一终态；已有绑定事务继续沿用其事务边界。后续认领时冻结输入/文件出错仍可查看原队列证据；不改变原 Attempt 或自动恢复规则。队列历史随其 Run 删除，不能从中推断生产已发生或成功。隔离 SQLite 测试覆盖旧输入/冻结文件错误、成功与并发重派、历史写失败回滚、模型投影及迁移保护；Windows spawn 的独立进程竞争验证单条重派授权及历史，bound session 外层异常验证回滚。状态轮询只查询当前队列状态，不加载历史；Run 详情仍可查询完整历史。受控 Producer 不代表真实生产。

## 2026-11-03 fresh 重放与首次授权

- Agent `start_content_run(action=fresh)` 与 `StudioClient.start(mode=fresh)` 对同一请求只做首次授权；`/api/runs/{id}/execute` 的 `initial_only` 在 SQLite 写事务中复核账号、版本和队列。已存在队列时只读原回执，包括 `needs_attention`、`failed`、`cancelled`，不重排、不清错误、不启动新 Attempt；无队列且仍为全新、未尝试的 fresh Run 才入队。响应丢失时原身份与运行记录用于核对。
- 未设置 `initial_only` 的用户显式执行/恢复沿用原有规则；数据库外的副作用仍不能回滚。隔离 SQLite、真实本机 HTTP 和受控 Producer 覆盖重放、中断、并发、归属、响应丢失和显式恢复，不调用真实 Codex。

## 2026-11-03 普通生产与 Web 重做重放

- 批量生产、普通 StudioClient 生产和网页从头重做共用首次授权边界：已有队列只返回原回执，避免在查询与提交之间重置 `needs_attention`。旧自定义 key 可用，空标识或无法核实的旧快照提示核对；用户明确点击恢复继续走原执行路径。
- 栏目及详情“开始生产”按当前 Revision 首次授权（包括新返工版本）；只有明确的“恢复生产”走原恢复接口。受理回执与当前状态分开，快速失败时显示需要核对；重做详情同样显示未确认队列。真实本机 HTTP/隔离 SQLite 配合受控竞争和浏览器交互验证，不调用真实 Codex。
- 详情页的开始、恢复与取消在点击时冻结操作类型、Run、版本和授权语义；离线等待期间界面状态变化不会改变已经点击的请求。真实 TanStack MutationObserver 断网、更新页面参数再恢复网络的受控测试覆盖此语义。

## 2026-11-03 初始生产授权事务边界

- `ProductionSubmission.transaction()` 统一执行器就绪检查、执行器锁 → Run 写锁、数据库事务，以及提交成功后的唤醒。事务对象提供普通制作任务和参考图的初始 Run/队列授权操作；参考图保留 `BEGIN IMMEDIATE` 与未确认队列不自动重入队的规则。批量请求仍使用原来的延迟事务，staged 的冻结、素材导出和 dispatch 未迁移。
- 业务调用方继续使用事务对象暴露的 SQLAlchemy session 维护各自选题、任务、归属、请求回执及幂等规则；这个边界不阻止调用方自行提交 session，也不是新的认证或独立进程协调机制。事务回滚不回滚 Skill/素材冻结文件，执行器启动恢复仍先于本次授权事务。

## 2026-11-03 参考图重放与未确认队列

- 参考图请求重放时，即使首个 Revision 尚无 Attempt，只要原队列项已经进入 `needs_attention` 或其他非 `pending`/`running` 状态，也保留该队列项及其时间、错误和已有回执，不借重放重新授权生产。崩溃可能发生在队列标记 `running` 之后、创建 Attempt 之前；没有 Attempt 本身不能说明可以自动恢复。
- 首次提交与正常 `pending` 重放继续使用已有原子入队语义。需要恢复时仍走现有显式授权入口；历史未标记回执保持相同保护，不改写旧回执。数据库外的冻结文件和模型调用不受事务回滚保护。
- 真实隔离 SQLite 和受控执行器复现重启后的未确认状态，验证新旧回执重放、四路并发、既有数据库记录不变和显式恢复。相关参考图、持久队列、RSI 与锁顺序共 42 项测试通过；使用隔离文件及受控 Producer，不调用真实模型。

## 2026-11-03 参考图制作应用服务

- 公开入口为 `creatoros.runs.ReferenceProductionService.submit(series_id, ReferenceProductionInput, creator_id=..., origin=..., origin_session_id=...)`。调用者提供已建立的账号身份；服务先校验归属，再在原子授权事务中复核。它不读取 HTTP 对象，也不返回 Web 查询 DTO。
- 服务集中稳定 Topic 身份、旧回执兼容/请求冲突、已接收 Skill 版本重放、参考原图与 Skill 冻结，以及 Topic/Run/写回执/首次队列授权的同事务提交。执行器→Run 写锁顺序、SQLite BEGIN IMMEDIATE、提交后唤醒及已有 Attempt 不重入队保持原有语义。
- Web 参考图端点和 Agent `start_content_run(reference)` 共用装配的同一服务；路由只处理 HTTP 请求、入口权限和 Run 响应投影。直接调用者也必须通过相同账号、参考组和 Skill 作用域校验。账号作用域仍不等同于完整身份认证。
- 定向后端53项通过（含6项公开服务直接调用/共享实例证明，最终独立重跑6项通过）；新参考图选择浏览器2项与完整旧 topic-references 浏览器4项通过。制作请求 JSON Schema 与功能提交一致；验证使用隔离 SQLite/HTTP/文件、受控生产边界，不调用真实模型，也不代表全仓回归已完成。

## 2026-11-02 完成视频回执的离线纠正

- 对已完成但路径无效的旧视频回执，新增需要明确选择文件、先检查再显式执行的本地纠正；新 Attempt、双阶段归属复核及失败队列回执更新沿用 Run 的租约和事务边界，不重写旧回执，不调用 Codex。操作入口、前置条件、fingerprint、v2 摘要与证据局限见 [交付 SPEC](../delivery/SPEC.md)。`submit-tool-v1` 已完成但没有接收回执时禁止自动重试，保留现场核查。
- 复制后或最终验收前发现栏目归属变化时，离线纠正写入不可技术重试的 `offline_video_correction_failed`，显示原 Attempt 和回执已保留、需要核对归属及纠正记录；旧失败队列不变，不自动启动生产。即使栏目恢复原归属，同一未完成纠正请求也只提示核查，不再创建 Attempt；当前没有自动接续这个未完成纠正的入口。
- 对 checkpoint 尚未标完成但 worker 已记录最后同线程生产回合完成的窗口，显式恢复先核对两份证据；未接收视频时记为不可重试的 `video_delivery_unconfirmed`，不再发起 SDK 回合。会话或回执冲突单独以 `video_delivery_evidence_conflict` 停止，不能据此断言回合完成。历史 Attempt 原文件和原错误不重写。
- 最初 `tests.test_video_delivery_contract.VideoDeliveryContractTests.test_invalid_completed_receipt_can_be_repaired_on_explicit_retry` 标为 expectedFailure；重构基线 `a28ad5b9bbc8` 的归档源码去掉标记后，在隔离文件和受控 SDK 替身下复现第二次 Attempt 报 `CodexProducerError: 文件缺失或过大`，同一失败在重构后再现两次：原回执指向 `missing.mp4`，工作目录另有 `finished.mp4`。旧断言要求重试自动修复，但原回执并不能证明应选择哪个文件；现改为测试普通重试拒绝猜测、显式选文件成功及缺证据/并发/取消/归属变化等拒绝。验证使用隔离 SQLite、受控 SDK 替身和可完整解码的本地 MP4，不代表真实 Codex 成片质量。

## 2026-11-02 生产与交付职责拆分

- `creatoros.delivery` 现集中冻结输入、图片和视频的收件/回执校验、确定性产物验收及同 Revision 的文件恢复，详见 [交付 SPEC](../delivery/SPEC.md)。Run 生命周期、队列、任务创建事务和执行所有权仍由现有 Run 侧负责；等待依赖的任务仍允许没有 Run。旧模块导入、持久文件格式、历史摘要和接口保持兼容。
- 隔离文件验证对同一组重构前的图片与视频逐文件哈希及全部验收字段一致；固定 `file-v1` 图片回执的 `native-v1` 摘要也用重构前代码独立核对。交付入口及离线恢复测试禁止导入 Run/生产适配器。当时视频已完成但无效回执的修复仍是既有预期失败；后续处理见上节。
- 扩展回归初次发现旧 `test_native_thread_continuity` 有 2 项失败、2 项报错，`test_simple_skill_tasks` 有 1 项栏目外 Skill 应拒绝的断言未获得异常；五项均在 `git archive HEAD` 的隔离原始源码上以相同错误重现。已核对调用链与后续提交：旧 Attempt 同 Revision 恢复继续原交付协议，新 Revision 可复用 thread 并使用 structured 回执；账号可用 Skill 可由任务临时选用，不会自动加入栏目池，另一个账号独占的 Skill 必须拒绝。现以支持两种 SDK 回合及唯一 turn ID 的夹具验证续接，同时保留线程、图片、用量及无重画断言；账号测试检查池外可用、冻结不变及越权无任务/Run/素材集/回执。受控夹具不代表真实模型生产。

## 2026-11-01 干净重做的精确 Skill 列表（隔离验证通过）

- 工具新契约的 fresh 支持 skill_ids，宿主核对账号可用性并读取当前版本摘要，构造 selected Skill 执行规范。保留原选题和视频所需素材，不继承旧 thread 或旧产物。
- 历史冻结输入及产物不迁移，重做仍要求来源版本和稳定请求身份；真实运行任务不停止、不重启。
- 真实loopback HTTP/SQLite验证多Skill selected快照、同请求复用、外账号Skill拒绝、原输入保持；此验证不执行生产者、不调用模型，不能代表新生图质量。


## BGM 分享输入（2026-10-28）

- 视频任务 inputs 接受 bgm_material_id 或 bgm_reference（二选一）。分享原文直接冻结在执行输入，并与素材 ZIP、所选 Skill 和剪辑要求一起交给 Codex；提交前不下载、不解析，不要求已有音频 ID。
- 文件方式保持旧回放摘要兼容；分享方式在最终交付仍核验 MP4 的真实音轨。Agent 账号边界允许分享文本，但素材集和已保存文件仍校验归属。
- 隔离 HTTP/SQLite/网页验证分享全文不经解析提交、回放不重复及原文进入最终 prompt。链接任务用 SDK auto_review 评估浏览器等必要访问请求，文件任务保持原模式；失败返回 complete=false/error，成功回执仍兼容。严格 JSON 输出所有字段必填但可为 null。
- 真实隔离 Codex 收到 ZIP、完整 Skill 和用户分享原文；首次路由超时，同任务恢复后报告浏览器访问被拒绝、备用下载需要 cookies。改为自动审核后的新隔离任务遇到路由超时，未验证真实成片成功。旧失败回执保留，正式25张图片无写入。

## 2026-10-27 单任务停止与移除

- 用户显式停止排队 Run 时取消队列；停止正在生产 Run 时只通知其执行者，Codex SDK 发送 turn.interrupt，状态在执行者退出后变为 interrupted。停止未得到确认时记录失败，不假称已停止或自动恢复。验收阶段不强制中断。
- 隔离 SQLite 的停止、队列和多阶段回归 31 项通过；另有账号范围 HTTP 校验通过。受控 Producer 只验证信号与文件保留，不代表真实 Codex 已完成中断验证。
- 终态制作任务允许软移出当前列表；原 Run、产物和 Trace 保留。运行或等待中的任务必须先停止或取消，不能通过删除隐式中断。
- 隔离测试覆盖版本冲突、单项停止不阻塞其他任务、文件保留、归档后幂等重放及跨账号拒绝；不触碰正式运行中的任务。

## 统一图文/视频执行规范（2026-10-27，隔离验证）

- 新任务使用 `production_protocol=task-v1` 与 `TaskExecutionSpec`：kind=image/video，准确主/辅助 Skill ID+目录 digest，以及宿主已鉴权材料 ID+摘要。旧 skill_name/composition 协议保持解析；统一规范不强求旧二元绑定。
- 提交时冻结每份 Skill 全目录；等待依赖时也先冻结 Skill，之后只添加已取得的 ZIP/BGM。Skill 正文保持各自原文，不构造融合 Skill。执行前逐份摘要核对，素材在 Run 与 Attempt 中再次固定，HTTP 只投影 ID/摘要/名称。
- 统一复用 Run/Revision/Attempt、租约、线程登记、现有队列与 `_mark_*` 状态转换。视频首次新 thread，同 Revision 技术恢复沿原 thread/原 work/固定素材；已有完整 SDK 回执的宿主 IO 故障可离线收尾。普通视频返工把已验收前版 MP4 固定为单独 video 材料，不使用 image previous_pages；从头重做创建新 Run/thread。
- 视频交付为独立 `video_artifact.json`，不伪装 CarouselCard；验收核对固定输入/Skill/材料、SDK 完成回执、MP4 摘要、真实时长/尺寸/音轨与完整解码，返回 images=[]、card_count=0 和 video 元数据。模型内容质量、听感与完整实际剪辑成功仍需真实生产/人工验收，不能由受控 SDK 证明。
- 统一执行七项隔离本地测试覆盖主/辅助原文 native 生图、真实短 MP4、摘要篡改、完整回执离线恢复、同 thread/work 恢复、Run/返工/重做、SDK collector 生命周期，以及 HTTP Range 206 和 MP4 下载。旧 fresh/native 连续性/恢复 36 项、native_run_wiring 与 studio_artifacts smoke 通过。正式库/服务/历史产物无操作；真实 Codex 验证由主流程单列。
- 严格复查修复并补回归：显式旧 ZIP 不得遗漏等待的图片版本；waiting 任务移交账号后读取/控制均拒绝且状态不变；内置 Skill 可作阶段默认；单卡片普通 start 使用显式池默认。staged 八项通过，既有素材集立即剪辑路径亦通过。并发同内容上传实测复现唯一键冲突后，在素材最终短写事务使用 BEGIN IMMEDIATE，三项材料上传/来源门禁/跨实例并发回归通过。
- 视频适配异常归类为 `video_delivery_failed`，复用原 retryable/恢复入口；Run 层明确覆盖 SDK 已完成后宿主 IO 失败、原 Revision 新 Attempt 离线收尾且禁止再次调用 SDK。最终视频七项重跑 7/7（29.989 秒）；材料三项重跑 3/3（4.078 秒）。分享链接仅支持抖音/哔哩哔哩/小红书/YouTube 域及子域，其他来源要求上传；入口 DNS 私网/解析失败拒绝不等同于对第三方下载器全部后续网络行为进行隔离。
- 真实隔离图产出后曾因开发中向 native-v1 evidence set 追加 execution_spec/materials，导致重启后的摘要算法漂移、两条正确图片投影不可用。只读差分证明两条 stored digest 均匹配追加前算法；最小修正移除这次未版本化追加，保持 native-v1 摘要契约。原两条图不改文件、不改行、不重生图，重新核验均 stored=current 且 artifact_available=true/cards=1；typed image 回归 1/1（5.175 秒）。未来扩充参与摘要的证据必须显式版本化，不能就地改变已有协议。
- 最终 fresh 审查修正：普通单图 Run 的“从头重做”读取当前图片阶段默认，清空显式默认后拒绝隐式回退；普通返工继续原冻结规范。独立 ProductionTask 的 fresh 保留任务自己的明确选择，视频 fresh 不依赖栏目图片默认，也不接受会把视频悄悄变成图片的旧 skill_overrides；更换剪辑 Skill 需新建视频任务。三项新隔离回归通过（24.299 秒），含真实 loopback 账号作用域 HTTP；没有模型调用、正式数据或旧文件操作。

## 选题内独立制作任务（2026-10-27）

- 简化选择：任务新增 `skill_ids` 精确版本列表，由宿主读取摘要并冻结原始正文/资源。此节当时要求栏目池家族校验；后续账号库可临时选用池外 Skill 的行为以本文件 2026-11-02 说明为准。无需 Skill 输出声明，所选 Skill 均为 `selected`，原样并列交给 Codex；旧 `skills`、旧输出角色及已冻结任务按原协议读取。图片/视频提示词对 `selected` 不设置主辅优先级。原请求不含 `skill_ids` 时保持既有幂等摘要。
- 隔离真实 SQLite/文件与受控本地调度定向测试 11/11：无声明 Skill、多 Skill 冻结、同请求重放、池外拒绝、移除旧池引用但保留文件、图片/视频平等提示及旧提示兼容；不代表真实模型质量或端到端视频验收。

- [制作任务契约](../../docs/production-tasks/SPEC.md)：新增 ProductionTask 与 nullable ContentRun.task_id；旧 Run 保持 null，互斥/最新运行/父状态写入按新旧作用域区分。复用原队列与30工位限制，不创建第二生产器。
- Task、Run、Revision、队列与素材集/回执同事务；默认或指定 Skill 都在提交时冻结正文及附件。task fresh 默认保留自己的选用，不退回栏目；候选 digest 改变需明确重选。旧 fresh 不变。
- 新增核心 11 项通过；联合任务/素材集 34 项通过。真实两线程生图及前端收图、ZIP 另记 [验证](../../docs/production-tasks/VALIDATION.md)。正式环境未迁移或重启。

## 选题级三十工位并发（2026-10-23）

- 用户明确要求将普通生产默认并发由5提升为30；`CREATOROS_PRODUCTION_CONCURRENCY` 与显式参数均允许1–30。每个选题仍独立生产会话，不拆分单篇；持久队列、同题互斥、恢复与关闭语义保持原实现。
- 旧五工位回归显式配置5；新增隔离SQLite/受控本地Producer验证默认30活跃、第31项等待、空位补入、独立目录/thread及每题单Attempt，并核对默认/环境/显式参数上下界。不是30个真实Codex调用，不保证外部服务吞吐或30倍速度。
- `tests.test_parallel_production` 与 `tests.test_parallel_production_api` 联合15/15通过（62.102秒），包含30活跃/第31项等待及补位、原5工位Web/Agent共用队列、取消/关机/重启/同题互斥；使用真实隔离SQLite/loopback HTTP与本地受控Producer，未调用模型。初次系统Python缺rich/sqlalchemy而未加载测试，改用项目deepcode环境通过。
- 原生产队列与宿主工具契约联合16/16通过（19.622秒）；故障注入预期错误日志保留，不视为真实生产失败或质量评测。
- 本轮不修改正式环境变量、数据库或产物，不启停正式服务。运行中实例需之后安全维护重启才会加载新默认；已有环境变量覆盖仍优先。

## RSI 与普通生产互斥（2026-10-22，隔离定向验证通过）

- 同一选题的活跃 RSI（queued/running/waiting_production）拥有试产调度权；普通新建、从头重做、执行授权和实际认领必须拒绝重复生图，并返回原 RSI 入口。已失败、结束或耗尽的旧 RSI 不阻塞新制作，不改写旧任务。
- 暂停不取消在途 Codex：paused 已提交试产通过受管元数据纯只读 hook 确认，queued/running/未知保守阻止，明确终态才放行普通制作；不在生产写锁里获取 RSI 或提炼写锁。普通单项及批量拒绝均保留原 iteration_id/入口，不伪装为新 Run。
- 账号任务查询直接投影已有 RSI 数据，明确当前阶段、是否已提交试产及图片数；不因普通 Run 列表为空断言“没有生产”。当前归属与冻结归属不一致时不泄露旧任务。
- 原选题管理的读写共用 block_reasons 检查活跃 RSI 和暂停但未核实完成的试产；旧删除接口同样保护。成功的编辑/移除回执仍先精确重放，不因之后开始 RSI 改写或拒绝已完成请求。
- 选题已有已结束 RSI 即使没有普通 Run，移除也走原 TopicRemoval 归档，不物理删除其定位行；旧严格删除入口拒绝。原 RSI 所有列、参考图片字节与带账号作用域的详情/图片入口保持不变。
- 从头重做读取原 Topic 当前 title/brief 并保存新 Run 快照；原 Run/Revision/Attempt 及图片不变。隔离 SQLite 与 loopback HTTP 回归验证业务门禁，真实 DeepSeek/Codex 前端验收由主流程另记。
- 新 tests.test_rsi_production_guard 最终9/9通过（含已结束RSI归档及原图HTTP读回）；新增前8项与原 Topic 管理 HTTP9项、fresh9项、任务导航2项联合28/28通过；另生产队列10项及 worker_task_projection/content_discussion_tools smoke 通过。首轮两处失败为新增测试的空 tuple/list 比较和旧delete未带JSON请求头，分别修正断言/请求，不修改产品迁就测试。没有正式数据/服务操作、模型调用、图片生成或旧 Eval 改分。

## 单次显式选定 Skill 版本（2026-10-22）

- `create` / `restart_fresh` 可接 `skill_overrides`：完整 Skill 与内容/呈现二选一，双 Skill 可只替换一边并补栏目默认另一边。明确选择的每个 ID 必须带读取时的完整目录 `expected_digests`；不接受本地路径。不修改 Series 绑定、revision、旧 Run 或在途输入。
- 只有这一显式路径可读取候选；普通 `resolve`、栏目绑定与普通双 Skill 快照仍拒绝候选。指定版本在接受请求前冻结完整正文、assets、scripts；排队、重启或后续候选编辑均不重读当前目录。冻结文件缺失/变化时拒绝执行，不能偷偷换新版。
- 选定版本的 start 需要稳定 `request_id`，fresh 沿用既有来源/version/请求身份门禁；重放校验明确选择的 ID 与 digest，相同请求返回原 Run，输入变化拒绝。单 Topic 有现成记录时必须结束后通过 fresh 另开干净 Run，不新增同 Topic 并发。不扩展 batch 覆盖参数；参考图直接制作另以可选 skill_ids 复用精确任务版本边界，详见 docs/topic-references/SPEC.md。
- Agent 宿主使用会话/turn/topic 稳定派生身份，所有写请求走原 Studio API 与持久化执行队列；没有新生图执行器，不自动批准/发布。跨账号 Skill 家族和选题作用域继续由 Web 宿主校验。
- 隔离 `tests.test_selected_skill_run` 原13项全通过，追加第14项及账号HTTP正向2项单独通过：正文/图片/scripts 即时冻结、候选后改不污染、部分双 Skill 默认补齐、单/双形态本次替换、并发重放/输入冲突、读后复制竞争、陈旧摘要、重启/完整性丢失、全局候选门禁、未选默认失效不阻塞明确覆盖、旧 Run/文件不变及真实 loopback HTTP 工具递交。旧fresh领域、fresh API与版本管理27/27回归通过。HTTP 生产器为显式故障注入（收到冻结请求后失败），验证幂等与状态，不代表真实 Codex 生图或质量 Eval；未改正式数据/服务。

## 已验证隔离交付回填原选题（2026-10-21，正式离线回填通过）

- 用户授权在空闲时重启服务，将60506acd原输入真实重放所得8图离线恢复至原Run页面；同Revision新增Attempt，旧失败Attempt、错误与文件不变，不重新生图、不自动批准。
- 回填前核验冻结请求/Skill/归属、完整Checkpoint及成功执行回执、产物哈希；使用本地执行独占锁、最新Run及版本/lease门禁，复制到正式新Attempt后重验。数据库、队列、事件一致且同源重复操作幂等。
- 先隔离副本故障/幂等验证，再在确认生产、调研、提炼、讨论与聊天任务均无进行/排队项后停服备份、正式回填并重启；正式验证以原页面8图、待验收状态、旧失败与原图哈希保留为准。新Skill版本和生产模式不在本轮范围。
- 新 `offline_adoption` 仅本地显式CLI/domain入口，无网页任意本地路径导入；12项隔离回归覆盖成功/幂等、输入/图片/归属篡改、版本/最新Run/租约/移除/取消/队列门禁及复制失败。首轮一处TopicRemoval夹具缺少必填字段已补，未改产品迁就夹具。后续单Skill摘要和参考组校验的主路径另通过。
- 正式停服前检查SQLite生产/调研/发布状态、68份任务与会话JSON以及执行日志，均无活跃/排队任务；7条Run queued没有生产队列授权，不误认为正在执行。PID27488确认身份后停止，SQLite备份与quick_check完成。
- 原Run同Revision新增Attempt2，8张图片与来源逐字节一致、状态awaiting_approval、旧失败Attempt行/旧事件/18份旧文件哈希不变。本次模型调用0、生图0；真实重放费用留source_usage。数据库仅预期6张表改变；证据 `tmp/maintenance-20261021/recovery-verification.json`，备份同目录 `before-recovery.sqlite`。服务重启与网页验收另记。
- 正式服务已从main重启（PID22504，8765）。HTTP八张图片逐张与原生成文件比对通过；原网页1/8与8/8切换正常、1254×1254真实图片加载，技术记录同时显示Attempt1原失败和Attempt2成功；Run version5、待批准。服务启动后再次核对旧记录/文件与备份，未出现其他表变更。证据 `http-verification.json`、`restored-original-run.png` 同维护目录；没有批准、发布或重复生产。

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

## 2026-11-03：生产提交与账号授权

- 新制作任务、参考图直做以及直接创建/从头重做先在 SQLite 写事务外解析并冻结本次输入；新默认单/双 Skill 也在提交前冻结。旧 Run 未标记该契约时继续按其历史方式恢复，不重写旧记录。账号、栏目、选题、来源归属或所选授权在准备后变化时拒绝新提交；已接受的 Run 恢复继续使用其已授权的冻结文件。
- 提交前在进程级 Skill 锁下预解析版本家族和候选来源，并取得账号引用快照；短 `BEGIN IMMEDIATE` 内比较最新数据库范围和内存授权证明，不在 writer 内等待 Skill 文件锁或复制文件。新旧选择共享同一账号可用规则：共享发布版需有使用引用，其他账号的私有候选不可用。即时视频还按冻结导出复核实际图片来源及集合/BGM；waiting 视频仅复核当下输入集合与音频，不冻结未来图片。事务成功后才唤醒执行器。
- 同一数据库和 request_id 的正常并发准备由引用计数的进程内单飞锁协调；同一选题不同请求仍可并行准备并协调收图集与位置。成功回执优先于旧快照校验。同账号可核验新式 fresh 的原来源、版本及完整选择后只读重放；历史无完整选择签名的旧记录保留旧回放路径。锁不跨进程或断电，不能声称 exactly-once；进程崩溃或结果未知时保留可能未引用的准备目录，不删除旧文件，也不自动重提。撤权若恰好发生在准备复制窗口，拒绝新写库后也可能留下本地未引用的 Skill 副本；这些目录不能按孤儿 Run ID 经 API 读取，后续回收需先证明目录归属与未提交状态。
- `GET /api/topics/{topic_id}/production-tasks/receipts/{request_id}` 只读返回该请求的已接收任务，未找到返回 `unknown`，不表示未提交。需要当前账号拥有选题且回执原账号一致；Agent 会话额外受账号作用域守卫约束。本机 Web 沿用已有本地信任范围。提交确认丢失或不可判定的响应标为 `studio_outcome_unknown`，不重派生产。
- 拆分任务已接受的同 ID 在 executor 不可用时可只读返回；参考图直做目前仍先检查 executor readiness，离线时既有回执可能暂不能直接返回，应等待核对原请求，不换 ID 重提。范围限于上述入口；staged advance、RSI 和历史目录交换恢复尚不能据此认定采用同一锁序。文件与 SQLite 仍不是单一原子事务。
- 从头重做的请求身份已属于同一账号时，若被用于不同的选题、来源或 Skill 选择，返回幂等冲突；来源已归档也不能误报为跨账号。不同账号的已有回执仍拒绝访问。冲突不创建新 Run、任务或图片；历史回执和产物保持原样。

## 栏目归属变更与未开始授权

- 栏目账号实际变化时，同一 SQLite 事务将该栏目的 pending 队列与 waiting 阶段任务转为 needs_attention；running 不受影响，已冻结 Run、阶段定义和依赖不改。无变化的幂等赋值不暂停。转回原账号不会自动派发；Run 必须显式恢复。阶段任务显式恢复时验证原依赖 Run/Revision 仍是最新版本，变化时拒绝，不能静默改绑。
- 被覆盖的队列旧错误进入队列历史；阶段任务原状态、错误与依赖进入独立 production_task_history，显式恢复前的错误也保存。任务 history 查询仅在当前栏目与冻结账号一致且可用时返回；一般任务列表和模型投影不包含明细。0028 降级在存在历史证据时拒绝。
- Run、快照与 Revision 的 task_id 必须一致，历史普通 Run 缺失按 None；任务 Run 缺失拒绝且不创建 Attempt。归属提交经公开协调边界按 executor → Run 写锁排序，锁不跨生产调用。锁为进程内机制；绕过同一 Studio 进程、直接共享 SQLite 的写入者不享有此序列化保证。
