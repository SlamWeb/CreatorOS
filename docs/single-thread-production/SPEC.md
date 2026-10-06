# 单 thread 文件交付生产

状态：2026-10-02 已实现并完成真实一图试产；最后回归结果见下。

## 双 Skill 组合协调（2026-10-02）

- 保留两份独立 Skill，不生成第三份合并 Skill，不增加 PageSpec 协议。栏目约定沿用 series_description，本次要求沿用 topic_brief / revision_instruction。
- 双 Skill 在同一个 thread 先完成一个只读、无调研/生图的短文本 turn：区分 Mind 的内容与教学关系、制作 Skill 的 IP/视觉，以及需要综合适配的分页/分格。只返回 ready/needs_input 和一段简短说明。
- 用户明确要求及最新返工说明优先于 Skill 默认偏好；默认版式可调整，必要内容与明确要求不得静默舍弃。无法同时满足的硬要求需提问，不把缺省配置或可调整偏好一律视为冲突。
- 宿主保存结论到 checkpoint 和可读记录；ready 后在同一 thread 进入原有生产。needs_input 以明确错误码 skill_composition_needs_input 停止，不触发生图阶段、不运行交付索引修复；沿用既有失败展示及“修改要求/返工”进入新 Revision，不重复原请求。
- 技术恢复复用已保存结论；历史已产出页面但缺少组合记录的 native Run 保持恢复兼容。单 Skill 不增加检查 turn。上下文/usage/取消/总超时沿用现有机制，检查的 token 纳入本次用量。
- 文本检查禁生图仍是工具指令约束，不宣称 SDK 的 read_only 沙箱可关闭所有图像能力；宿主只保证未通过检查不启动后续生产 turn。
- 验收：正常组合、可适配偏好、硬冲突；同 thread、无错误交付修复、恢复不重复检查、取消/超时、旧证据兼容及隔离服务错误/返工；真实 gpt-6-luna/xhigh 文本探针，不生图、不发布、不改正式运营数据。结果见下。

### 组合协调验证记录

- 新增 `Checkpoint.composition_review`（可选以兼容旧证据），只包含 `status` 与短 `note`。宿主保存 composition_request.txt / composition_response.txt / composition_review.md，Trace 记录 composition.reviewed；不把模型内部推理当作适配说明。
- `smoke_native_production` 通过：两份 Skill 在同一 thread 的检查/生产两 turn；累计 usage 不重复相加；硬冲突和坏检查 JSON 不进入生产或索引修复；同 Revision 恢复不重复检查；单 Skill 不增加调用；旧图片 checkpoint 仍可恢复；取消、超时继续生效。
- `smoke_native_run_wiring`、`smoke_native_artifact_web`、`smoke_production_progress`、`smoke_production_sessions` 通过；没有改前端 DTO、全局 Skill、账号/栏目或生产数据库。
- 真实文本探针三项通过：`tmp/skill-composition-probe-ok5yo_bd/report.json`。正常职责衔接（含 PageSpec / 仅负责 Prompt）→ ready；八词与默认六格 → ready，保留全部内容与双语；八词各独立格、一张图、固定六格 → needs_input，明确让用户选放宽图片数或格数。
- 追加真实澄清测试通过：`tmp/skill-composition-probe-w0b7htxy/report.json`；最新返工允许两张图后 → ready，保留八词及双语、每图六格。四项均为 gpt-6-luna / xhigh，事件未出现 imageGeneration/webSearch/collabAgentToolCall；不是生图质量或普适冲突识别准确率评估。
- 首批探针没有计为成功：mkdtemp 根目录在 Windows 下仅授予 owner 访问，子目录 ACL 重置不足，模型读取两份 Skill 被拒并报 needs_input；修复测试根目录 ACL 后重跑。首个报告脚本误把 dict 传给仅接收 Pydantic model 的 atomic_json，也已修正。原证据保留在 tmp/skill-composition-probe-q80bqcup 与 tmp/skill-composition-probe-lluaprov，不掩盖消耗。
- 隔离 HTTP 测试发现并修复原生返工无条件要求上版完整图片的问题：生图前的 needs_input 有有效空交付 checkpoint 时，允许澄清进入新 Revision，冻结 previous_pages=null；同 Revision 技术重试保留这个输入，并重验上版澄清证据。缺失 checkpoint、普通缺失/损坏的既有产物仍拒绝，不放宽验收。
- `smoke_composition_run_flow` 通过：冲突不可原样重试；保存澄清不执行，显式开始才把澄清交给 Producer；澄清后 timeout 可以恢复。坏格式 `skill_composition_invalid` 属于可显式重试的技术故障，不是需要用户选择的冲突；`smoke_native_production` 另验证坏格式后同 thread 重新检查并继续。服务测试使用故障注入以避免重复生图费用，真实文本结果与服务状态接线分开验收。
- 收尾回归 `smoke_content_run_service`、`smoke_studio_executor` 通过；既存 Starlette/httpx 弃用警告未改依赖处理。本轮没有新增 UI 状态或浏览器交互，冲突暂通过现有失败详情和“提出返工”入口展示，不冒充新的对话式澄清界面。

无生图续跑命令：`python -m tests.live_skill_composition --case all`；也可指定 `--case adaptable conflict`，每项新建隔离 thread，使用已有登录态与额度，证据仅写 tmp。

## 范围与原因

- 一份或一组本地 Skill 由同一个 Codex thread 完成；内容/呈现分工是任务说明，不再由两个 thread 和强制 PageSpec JSON 交接。
- 复用 ContentRun / Revision / Attempt、执行锁、事件进度、SocialContentPack、人工审批。先只支持图片交付，不实现通用视频管线或产物转 Skill Web 工具。
- 新输入保存 production_protocol=native-v1；缺少字段的历史输入仍走 legacy，不改旧快照、图片和审批摘要。新协议组合只检查角色/文件与最终图文契约，不按名称或 GitHub 来源白名单选择。

## 最小协议

- 开始时冻结选中的 Skill，显式提供 SKILL.md 绝对路径及 SkillInput；整个生产库不加入全局发现目录。沿用 gpt-6-luna / xhigh、禁用生产记忆和项目文档自动注入。不宣称已经屏蔽所有原有全局 Skill。
- Codex 在本 Attempt 的独立 work 子目录中用 workspace-write 工作；宿主 checkpoint/最终包存父目录。内容稿可以是 Markdown 或 Skill 自己的格式。
- Codex 持续写 work/delivery.json：title、text、hashtags、complete、artifacts。每项只包含 order、source_image_path、image_prompt、reference_assets、可选 content_file；不要求 PageSpec 或小白角色资源。
- 宿主持续核验当前 thread 的真实图片路径、解码与顺序，逐项保存到 partial-images；保存 input digest、thread id、图片 checksum 和 Prompt/内容证据。文件未就绪不计为完成。
- SDK 事件用于最近活动，已验收文件数用于产物进度；不能把工具调用次数作为图片数。最终成功要求 turn 完成、delivery.complete 与全套文件验收均成立。
- 同 Revision 技术恢复只读取同 Run 的已验收 checkpoint，并优先 resume 原 thread；没有 checkpoint 才新建。新选题、新 Revision 默认新 thread，显式返工说明不隐式带入别的任务。
- 新 Revision 可以显式携带上一 Revision 核验过的内容/Prompt/发布稿，不带旧 thread 或旧图路径；第一次 Attempt 请求固定，技术重试不因前稿变化而漂移。SDK thread 累计 usage 保存 checkpoint，各 Attempt 只记相对于起点的增量，离线完成导入不重复记模型消耗。
- 最多一次无生图的交付索引修复，不自动重跑付费生图。失败/取消后文件保留，重启不能伪造仍在生成；拒绝恢复输入变化或篡改文件。

## 本地测试 Skill

- 新增 english-word-scenes：从参考图提炼“易混词的具体场景对比 + 一组统一动漫分镜”，是一份端到端 Skill。口述/本地作者可直接登记本地目录，不必先 push GitHub。
- 源文件放 production-skills/，安装进 data/producer-skills 的版本/working/registry；不放 creatoros/skills 或用户全局技能目录。
- 只添加这份 Skill 到正式本地库，不改已有栏目。真实试产使用隔离 SQLite / 输出目录，1 张六格图，词组 look / see / watch / stare / glance / gaze，人工验收、不批准、不发布。

## 验收

1. 本地 Skill 登记/重放/修改/路径与资源校验；已有 GitHub 安装回归。
2. 单/双 Skill 同 thread 输入；非白名单组合可创建新协议 Run；旧 Run 协议不变。
3. delivery 增量落盘、坏图/跨任务路径/Prompt 缺失拒绝；中断恢复不重复已有图；缺索引修复不生图；取消/超时落终态。
4. 同一 HTTP/Agent/网页读取进度、部分图片和最终包；审批摘要覆盖 Skill 与生产证据。
5. 真实 SDK 一图试产 + 图片人工检查；恢复故障用本地注入，不反复付费生图。若额度/服务阻塞，记录真实状态，不宣布通过。

## 实现接线

- ContentRunInput.production_protocol: legacy（缺省历史）/ native-v1（新建）；服务允许测试显式选择 legacy。
- integrations/native_production.py 提供 produce_native(producer, **request)、load_checkpoint(directory, digest=None)、verified_pages(directory, checkpoint)、evidence_files(directory, composition=None)。load_checkpoint 从 production_request.txt 与 skills 子目录核验输入；返回 pages，每页具有 order/image_path/sha256/warnings/image_prompt/content 属性。
- runs.validate_artifact 增加 production_protocol 参数（默认 legacy），native-v1 必须核验并摘要 native checkpoint/Skill/内容证据。Web 单/双 Skill 都能显示 native 部分产物与 Prompt；不改变页面组件。
- legacy adapter 原样保留；新的组合快照使用 native-carousel-v1，仍冻结两份本地文件，不强制其内容产出格式。

## 结果

- 本地登记已提交 `048202b`：正式库仅新增 `english-word-scenes--bd89b5d9cb32a407`，未改现有账号/栏目。原件及可编辑 working 副本保持分离，没有写用户全局 Skills。
- 真实试产：`tmp/native-production-trial-20261002-b`（独立 SQLite/产物），Run `075614dd-2080-451d-96a8-c81c33208d84`，单 thread `01a0fcc1-b13c-7e22-92aa-078eff99330c`，gpt-6-luna / xhigh。约 276.5 秒，一张 1024×1536 六格真图，2,430,973 bytes，进入 awaiting_approval；一次正常交付，无索引修复、无额外重画、无批准或发布。六词为 look/see/watch/stare/glance/gaze，内容稿保存词典链接，Prompt 由生产器报告，不声称拿到图像服务内部改写后的 Prompt。
- SDK 报告本线程累计 input 459,058、cached 405,248、output 11,632、reasoning 7,783；input 是多次模型调用累计，不是单次上下文长度。原始结果保存在 production_session.json/native_checkpoint.json/codex_trace.jsonl；未提交私有运行材料。
- 浏览器真人式操作（隔离 8876）：打开验收页 → 展开内容稿与 Prompt → 放大图片 → 返回 → 刷新，同一产物仍待批准且文件检查通过。原图已目视检查：六格与文字清晰、角色和视觉风格一致；不把这等同于语言教学有效性评估。HTTP 负例覆盖坏 checksum、冻结 Skill 被改后拒绝，以及查询不增加 Run version。
- 试产 init 脚本第一版漏传 series.description，尚未调用模型就失败；补齐后在新隔离目录重建，第一目录保留作诊断。旧目录没有正式数据，也没有付费产物。
- 尚未证明单 thread 质量/速度优于双 thread；只有单 Skill 一图的真实 SDK 测试，双 Skill同线程与故障恢复用受控 transport。未完成严格全局 Skill 可见性隔离：显式路径、禁记忆/项目文档不等于屏蔽所有已安装全局 Skill。单线程也不等于图像并发。
- 逐项验收的图片才计入部分进度；制作中 partial 页面暂只显示图片，最终产物可看内容与 Prompt。本轮保留旧 DTO，无主观质量自动打分。
- 恢复时已验收图直接按 checksum 复用；同一 thread 已生出但来不及 checkpoint 的图，可从保留的原工作稿修复索引后重新走路径/解码/Prompt 核验，不强制为同一结果再付费重画。原始未核验索引从不直接变成最终包。

### 复跑与查看（PowerShell，仓库根）

```powershell
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.live_native_production status --directory tmp/native-production-trial-20261002-b
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.live_native_production serve --directory tmp/native-production-trial-20261002-b --port 8876
```

`serve/status` 不生产；新的收费试产需对新 tmp 目录显式 `init` 然后 `produce`。失败/中断的同 Revision 可显式再次 `produce`，恢复校验好的文件；已经待批准的任务不应重复生产。现有 Studio 服务需要重启才加载新代码。

### 自动回归

- `smoke_native_production`：单/双 Skill 原生输入、同 thread、部分失败/同 Revision resume、已完成页不重画、一次索引修复、取消/超时、输入/图片/资源篡改、路径逃逸、BOM、累计 usage 转 Attempt 增量、离线恢复零用量。
- `smoke_native_run_wiring`：任意本地双 Skill，无 GitHub/character.png 限制；旧输入协议不变；新 Revision 显式前稿与重试上下文冻结。
- `smoke_native_artifact_web`：最终与部分产物、Prompt/内容、坏 checksum、Skill 篡改、ZIP 摘要、只读 version 稳定。
- 回归通过：local/Git producer skills、production_sessions、production_progress、content_run_service、pair_production、receipt_recovery、series_guards、studio_executor、studio_partial_cards、studio_production_progress、studio_run_api、visual_checkpoint、studio_api、topic CRUD。历史受控 Producer 夹具明确选择 legacy，不把假图片当 native 真交付测试。
- Web typecheck/build、受控 Playwright `production-progress.spec.ts + studio-workflow.spec.ts` 10/10 通过（34.8 秒），包括内容单字段/Prompt 单字段/均空回归；桌面与 390px 手机截图在 tmp/testresults。最小 UI 修复：不能因为 Skill 没交固定格式内容稿就把已保存 Prompt 隐藏。生产时 partial 仍仅图片，未暗中扩 DTO。
- 既存警告：Starlette/httpx 兼容层弃用提示、Vite 大 chunk 提示。没有为本轮更改依赖；自动回归无调研、生图或发布费用。

## 2026-10-06：生产预览不等于最终冻结

- 真实失败根因：同页修图或共享内容稿补写时，宿主将先前预览误判为不可变交付，拒绝最后的完整产物。现在允许生产期间更新图片、Prompt、引用和内容；全套新索引验证通过才替换 checkpoint，图片按摘要保存，旧预览字节保留。SDK 完成且交付完整后才冻结，最终审批摘要约束不变。
- 恢复保留 work 中最新 delivery，不用旧 checkpoint 覆盖它。新增宿主 SDK receipt，将 turn 的成功/失败与交付校验区分；已有成功执行证据时可离线重验，不重新生图。旧任务可运行 `python -m creatoros.integrations.native_recovery --directory <Attempt路径> --record`：只读核验原 SDK thread/turn/cwd/最终回复，再补执行证据，不改 Run 状态；之后使用原执行/恢复 API。
- 最小通信协议见 `docs/codex-worker/SPEC.md`。新增 `smoke_worker_delivery` 覆盖补稿、修图、坏索引不破坏预览、冻结后拒绝变更、SDK 最后失败不能冒充成功。
- 真实历史回放：两个原失败 Attempt 均通过 SDK 历史只读核验，并在隔离目录组装通过完整 artifact 验收。`tmp/native-delivery-replay-9dnu081t/report.json`、`tmp/native-delivery-replay-t6unfftc/report.json`；各 1 张真实图片，模型调用 0、生图调用 0。第二条业务记录现为 cancelled，只验证可恢复性，不将其复活。
- 自动回归通过：native_production、worker_delivery、native_run_wiring、native_artifact_web、production_progress、studio_production_progress、topic_research_sdk、content_run_service、studio_executor、studio_run_api。故障注入与执行器测试为隔离受控测试，不声称做了新的真实生图。
