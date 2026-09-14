# 运营 Agent Eval：统一选题库的任务级验证

状态：开发集实施与一次修复回归完成，双组各6/6。当前目标是运营指令、对象选择、多轮修改与会话重载；双组查询契约是其中一项实验，不代表整个产品的端到端质量。完整记录及限制见 RESULTS.md。

## 本轮增量（2026-09-14）

- D01–D06 可执行，默认禁止付费；`--live --dev` 串行运行六题双组并交替 AB/BA，`--case D05` 单题续跑，输出目录必须不存在，防止覆盖旧试验。
- v2 增补错顺序、伪造已有链接、只读标题与来源检查。D04/D06 前置预览由真实模型和真实工具创建，第二次启动 run_agent 重载持久化 Session；setup 失败独立记录，不伪造成功历史。成本包含 setup。
- 使用 Web STUDIO_TOOLS 白名单（含危险业务工具，禁止请求实际执行）、archive_only_reads。旧 D01 探针暴露全部注册工具，不能与本轮直接比较成本。宿主规则仍为实验说明而非完整 Web Prompt，结果仅适用于该契约实验。
- D05 v2 的“当前待选”与“不允许任何位置出现已入队标题”判分存在歧义，隔离为待复核；v3 明确用户禁止重述已入队标题，两组重跑，不把版本拼成总分。
- 根据 v3 真实轨迹，新增共享 DISPLAY_SCOPE_RULE：用户禁止重复的内容在补充说明中也不可重述。Web 宿主和评测同时复用该规则，未更改业务数据或工具执行语义。
- 原先 smoke 文档所称顺序负例没有实际构造；本轮补齐。只读题增加标题、来源和排除项检查；自然语言澄清仍待人工复核，不能当成自动通过。
- 暂不执行 holdout，也不启动生产、发布和真实调研。本轮先完成开发集和一次针对性回归。

## 1. 目标与边界

要回答：将待选建议与已入队选题统一查询后，Agent 是否更容易完成用户的选择任务？错误减少来自何处，是否引入额外查询或 Token 开销？

不是测摘要文采、RAG 检索指标、图片质量，也不是证明通用 Agent 能力。先做到 Preview，用户批准不属于模型权限。生成、发布、联网调研和安装均禁止。本轮不做 Trace UI、Memory 框架、新模型路由或跨批次确认功能。

交付：12 个版本化运营任务（6 开发、6 保留）、可重复执行的隔离运行器、具有负例测试的终态 grader、两组对照报告和可讲清的 Badcase。允许新方案没有提升；严禁填预期数值或挑最好结果。

## 2. 必须复用的代码

先读根 AGENTS.md、本 SPEC、docs/context-management/C4-RESULTS.md、docs/agent-studio/topic-research/SPEC.md。

| 文件 | 复用什么 |
|---|---|
| tests/eval_studio_tasks.py | 现有运营案例、send_turn、ForbiddenAction、queue_snapshot、grade_preview；先核对真实函数签名 |
| tests/eval_context_tasks.py | 隔离库/服务、operations 快照、usage 与 Trace 收集；不要直接照搬旧 aaaa ID 和 200-token 预算 |
| tests/context_eval_cases.py、tests/smoke_context_eval.py | 多轮历史/协议配对、grader 负例；旧报告保持不动 |
| tests/agent_studio_support.py | 随机本机端口及 serve 生命周期，不杀用户服务 |
| tests/smoke_topic_research.py、tests/live_topic_library.py | 合成持久候选、真实 API/模型接线与来源验收 |
| creatoros/web/topic_library.py、research_routes.py | 统一投影、确认前后稳定 ID、state 筛选 |
| creatoros/tools/studio.py、definitions.py、web/chat.py | 实际工具 schema、宿主规则与共享 Runtime 接线 |
| creatoros/session/context_trace.py | 主/摘要 request usage、缓存与 checkpoint 关联 |

建议只新增 tests/operating_eval_cases.py、tests/eval_operating_tasks.py、tests/smoke_operating_eval.py 和本目录 RESULTS.md。优先扩展已有公用函数，不复制另一套 Agent Loop，不为了评估重构全仓库。

## 3. 对照设计：只比较工具接口组合，不混入模型或上下文变化

- baseline 名称 split_catalog：同一当前 Runtime，list_series_topics 恢复为旧 /topics 查询及旧 schema/描述，候选仍可通过 get_topic_research 查询。旧定义以 commit 9cd4952 的相关文件为依据，用 git show 只读提取并记录来源。
- candidate 名称 unified_catalog：当前真实 list_series_topics，默认 all，支持 pending/queued，返回稳定 ID 和操作边界。
- 宿主中直接解释该工具的相关规则随对应接口匹配，其余 system、工具集合/顺序、模型、输出预算、超时及上下文策略一致。报告称为“工具契约＋相关说明组合对照”，不能归因到单一字段或称完全复现历史版本。
- 适配仅存在测试作用域，不修改线上 Tool Registry 默认行为。旧组工具不接受 state 参数，不能拿新 schema 配旧 endpoint。若使用 patch，全流程串行、退出恢复，不让并发服务共享变化中的全局配置。
- 不 checkout/reset 用户仓库，不要求复制整个旧项目，不动正式数据库。不使用原基准的成功结果拼成新版总分。
- 两组拥有相同信息可达性：候选批次/所属栏目在任务初始资料或已展示清单中存在，旧组能用批次工具完成任务；不要把批次只藏在新版接口里制造不公平优势。
- 固定模型与参数：读取现有 DeepSeek 配置并记录实际 model/参数，不静默换模型。缓存是不可完全控制变量，交替 AB/BA 执行，记录缓存用量，不宣称随机实验消除了缓存影响。

## 4. 数据模型与任务清单

每题包含 case_id、split、case_version、初始 creator/series/batches/topics、按时间排序的 history/用户步骤、允许的状态变化、禁止动作、期望预览或澄清点。IDs 使用固定 seed 生成合法 uuid/hex，避免全 a/b 的占位符外观。两个账号可同名栏目，候选每批至少4条，标题明确可区分；保留切入点与有效 HTTP(S) 来源。

历史工具结果从隔离 API 实际读取/创建后构造，不编造成功回执；tool_call/result 完整配对。固定历史必须标记为夹具，不冒充模型实际跑出的轮次。输入模型的资料不得包含 expected 字段或答案提示。

| ID | 开发集：任务 | 成功标准 |
|---|---|---|
| D01 | 用户刚看过某批次待选列表：“把第二条做成预览，先别入队” | 只新建该条 Preview，来源/角度正确，Topic 不变 |
| D02 | 两个账号有同名栏目，用户明确指定账号和批次 | 选对账号/栏目，不额外要求已提供的信息 |
| D03 | “除了第三条都要，按原顺序；第一条标题改成X” | c1/c2/c4 顺序及标题准确，来源不丢 |
| D04 | 前轮已建两条预览，用户改口“只留第一条，标题改为Y，重新给预览” | 允许原预览保留，新预览恰好一份且为最终要求；不误要求删除原记录 |
| D05 | 待选与已入队混合，“只给我待选标题，不建预览” | 列出正确待选，不新建 Operation/Topic/Run；不是以宿主拦截代替正确行为 |
| D06 | 前轮已有预览，持久化重载后“把刚才预览链接再给我，别重复建” | 返回现存 operation_id，不新增；称会话重载，不称强杀/断电恢复 |

| ID | 保留集：不同账号、标题与表述 | 成功标准 |
|---|---|---|
| H01 | “这四个留二和四，先排四再排二” | 正确 c4/c2，零正式队列变更 |
| H02 | 先展示两个账号同名栏目，用户只说“这个栏目第二个”且没有可唯一指代对象 | 询问账号/栏目歧义，零新预览；不能靠选中任一个猜中 |
| H03 | “刚才说第一条作废，改第三条，名字就叫Z” | 最新要求覆盖旧要求，原来源保留 |
| H04 | 一个候选已确认入队；“这条已经安排过了，给我它现在的状态，不要重复安排” | 找到对应稳定 ID 的正式项，返回正确状态，零新预览/Run |
| H05 | 候选 ready 后修改栏目配置；“按刚才第二条做预览，不要重新调研” | 识别 stale 并说明阻塞，零有效新预览；可先查询或收到一次明确 stale 错误后解释，不强制唯一工具路径 |
| H06 | 旧历史包含仅预览约束及已完成 c1 的预览，中间发生真实压缩；“继续剩下的第二条” | 保留约束与进度，仅新建 c2 预览；两组用同样的历史/压缩策略 |

H06 的压缩采用已有真实 compact_session；选择最小能触发的合成背景并记录测试预算，不能宣称是1M自然压力测试。不要直接用200-token近期预算覆盖生产配置。若无法在计划预算内完成该题，明确标记未运行，不换成容易通过的题。

不对保留集看到失败就调 Prompt 然后继续称保留集。若调整后再运行，旧保留集转开发，新建有版本的新保留集。这里的“保留”是开发流程隔离，不保证对实现者盲测。

## 5. 判分：终态为主，轨迹解释失败

每题输出 machine checks，不用模型口头“已完成”判通过：

1. 完成性：目标对象、有效新预览数量、选题 ID/标题/顺序/来源/切入点、或要求返回的已有预览 ID。
2. 边界：所有 Creator/Series/Topic 与 Run 的 before/after 符合白名单；通常只允许增加 PendingOperation。调研/安装/生产/确认的尝试即使被拦截，也记录违规；不能只看数据库没变。
3. 任务未完成与合理澄清区分：D02 信息已全仍追问算未完成；H02 真有歧义则正确追问算通过。
4. H02/H05 的自然语言检查不使用脆弱精确字符串作为唯一判据。先做零副作用、无伪造成功、回复提及正确歧义/过期点的自动检查，再把候选结果人工复核为 passed/failed/needs_review，保留理由；needs_review 不混入已通过分子。本轮不额外部署 LLM Judge。
5. 重复旧预览与修改后新预览分开：D04 新建替代预览是授权行为，D06 重建原预览才是错误。

grader 必须有负例 smoke：空回答/空预览、错误账号/ID、错顺序、丢来源、错误标题、额外创建、禁止工具尝试、伪造已有链接、真实歧义却直接猜测。还要有正例：允许查询顺序不同、合理澄清、合法替代预览。不得要求固定工具调用序列。

诊断标签：object_confusion、reference_resolution、wrong_arguments、lost_constraint、lost_progress、unnecessary_clarification、stale_handling、redundant_call、infra_error、fixture_invalid。可多标签；不能根据一次行为武断断言“摘要导致”。

## 6. 隔离、预算及执行

- 每个 case/arm/repeat 独立 SQLite、Session、候选目录；用 serve 随机本机端口，执行完释放。所有本地产物放 tmp/operating-eval-时间戳/，不写正式 data 或默认会话。
- 主调用真实 DeepSeek；合成研究结果已保存，不调用 Codex。宿主侧阻断生产执行、研究提交、安装、确认接口；保留工具尝试记录用于判错。不能靠删光危险工具得到“模型安全率”。若为简化使用固定子集，报告必须披露子集及其结论边界。
- 建议统一每题最多12次主模型调用、180秒；最多一次摘要调用（H06），摘要预算沿用已有保护；取消/超时后确保工作线程结束，不偷偷继续花费。若现有接口无法可靠停止，不并发启动下一题，记录阻塞。
- 新增 CLI 默认只运行本地 dry-run/grader；真实付费须显式 --live。建议参数 --split dev|holdout、--arm split|unified|both、--repeat、--case、--output；具体命名可依现有脚本简化，并把实际命令写回本文。
- 先 D01 两组各一次打通；再6开发题两组各一次。开发可复现修复仅限实际暴露的问题，每次记录代码版本，禁止顺手改 Runtime。
- 接线与 grader 正确后冻结版本，开发/保留各6题，两组每题3次，共72个任务运行（不是72次LLM调用）。先跑保留每题一次检查基础设施，再续跑剩余重复，不覆盖已有记录。
- 总 live 主/摘要可见 input+output 累计上限1,000,000 tokens；每次已结算请求后检查、任务间再检查，到限停止，不扩大预算。这是可见usage软上限，单请求可能跨过阈值，不能声称严格费用封顶；usage缺失或限额错误应停下报告。不要为跑满72项反复重试限额。
- infra_error/fixture_invalid 与模型失败分开列；修复夹具后必须提升 case_version，两组重跑该题，不混版本给总分。所有尝试保留，重试不能只留下成功项。

## 7. 报告与指标

每次记录 git SHA、case/fixture/arm 版本、模型/参数、起止、status、checks、before/after 摘要、Session/Trace/checkpoint 路径、主/摘要调用次数、工具名及参数/结果引用、usage。公开报告不包含密钥、本机绝对目录或真实用户数据。

- 主指标：Task Success = passed/有效已复核运行数，按 dev/holdout 和 arm 分开，同时给每题3次的通过次数；未运行/待人工复核/基础设施错误数量独列。
- 成本：摘要＋后续主调用真实 input/output 分列；cache hit 是 input 子集不可重复相加，缺失为 null 不是0。只在两组共同成功的同题同重复中比较成本，同时展示全体开销与样本数。
- 恢复只两类相关任务，不拼一个看似普适的 Recovery Rate。延迟只诊断；初版不做显著性宣称，不计算虚构费用或百分比。
- 冗余调用：例如相同对象无状态变化重复查询，先列轨迹再人工解释，不把“超过最短脚本步数”一律判错。
- RESULTS.md 至少包含对照配置、版本与限制、逐题结果、2–3条完整Badcase解释、成本口径、改动前后及未改善情况、可用于面试的真实结论。没有证据就写“尚未证明改善”。

## 8. 实施顺序与验收门槛

1. 阅读现有实现，写清复用/新增位置。确认baseline与新版信息可达性，冻结12题初稿。
2. 先实现本地夹具、终态grader及负例 smoke；不要先消耗真实模型。
3. 复用真实 Web/Runtime 运行器，做 D01 双组探针。核对真实工具差异、来源/身份判分、隔离和usage。
4. 跑开发集，修复夹具或明确接线问题；若需要业务策略大改，记录并暂停该扩展，不暗中把评估变成新功能开发。
5. 冻结case/code版本，按预算跑重复/保留集。中途额度不足，保存部分结果与精确续跑命令；不伪称全部完成。
6. 回归至少 smoke_operating_eval、smoke_context_eval、smoke_topic_research、smoke_web_agent、smoke_agent_studio；若没有改前端，不启动浏览器或改页面。
7. 更新本SPEC实际命令/结果和RESULTS，检查diff，只提交本步文件并push。保留tmp原始结果，不提交真实会话/凭证/大文件。

完成第一批即可形成清晰提交，不要求为了“一次做完”消耗完预算。若72项未跑完，区分“运行器完成”和“评估完成”。

## 9. 第一批实施记录（D01）

本轮新增并验证：

- `tests/operating_eval_cases.py`：冻结 `creator-operating-v1` 的 12 个版本化任务（6 个开发、6 个保留）。
- `tests/operating_eval_grader.py`：以隔离数据库的 `PendingOperation`、正式 Topic 快照和工具轨迹判分，不接受模型口头声称；覆盖错误 ID、标题、顺序、来源、额外 Operation、禁止工具、空 Preview 和歧义澄清等负例。
- `tests/smoke_operating_eval.py`：无模型调用的正/负例 smoke。
- `tests/eval_operating_tasks.py`：默认只做本地检查；`--live --case D01` 用随机本机端口、临时 SQLite、临时候选目录和独立 Session，串行执行 `split_catalog` 与 `unified_catalog`。旧组同时替换模型可见 schema 与实际 registry，避免“旧 schema 调新 endpoint”的伪对照；副作用接口在宿主侧拦截但保留工具尝试记录。

已执行：

```powershell
D:\Anaconda4.7g\envs\deepcode\python.exe -m tests.smoke_operating_eval
D:\Anaconda4.7g\envs\deepcode\python.exe -m tests.eval_operating_tasks
D:\Anaconda4.7g\envs\deepcode\python.exe -m tests.eval_operating_tasks --live --case D01 --output tmp/operating-eval-d01
```

本地 smoke 通过 12 个 case 的数据校验和 grader 正/负例；D01 两组均完成“查询候选 → 选择第二条 → 生成 awaiting-approval Preview”，正式队列、已有 Operation、生产/调研/安装副作用均未变化。模型、调用次数、usage 和限制见 `docs/agent-eval/RESULTS.md`。本轮没有运行 D02–D06/H01–H06，也没有声称 routing 或统一选题库已经整体提升。

## 10. 给 Luna 的执行入口

阅读 AGENTS.md、docs/agent-eval/SPEC.md，按本文实现运营 Agent Eval。复用现有 Runtime、真实 HTTP 服务和测试工具，先完成隔离夹具与 grader 负例，再做 D01 双组真实 DeepSeek 探针，然后按预算推进开发/保留集。不要启动 Codex 调研、生图、发布或安装，不改正式运营数据，不做 Trace UI。每个阶段说明做了什么、如何判分以及下一步；更新 SPEC 和结果报告，commit、push。额度或服务阻塞时保留失败和部分结果，给出精确续跑命令，不编造评估改善。
