# 栏目选题调研与选择

## 待选候选长期保留 · 2026-10-09

- UI 待选卡片不再展示“已过期”；当前栏目/Skill 不可用时给出配置错误而不是要求重新调研。浏览器候选筛选、选择、编辑、Preview 零写与直接确认通过；桌面/手机真实渲染截图已检查，截图不代替状态断言。

- 实际旧行为没有日期/TTL 限制；`stale` 来自调研时的栏目字段或 Skill digest 与当前配置不一致，Web 将其显示为“已过期”。用户要求待选题长期可选，本节替代下文“配置变化后旧候选不可选/需重新调研”的历史约定。
- `ready` 候选不会因创建日期、栏目定位/受众或 Skill 改动自动失效。历史快照、来源、切入点和 JSON 工件保留；选择/入队时读取当前栏目配置，新建 Run 沿用既有服务读取当前栏目与 Skill，不回写旧调研证据。
- 选题入队 Preview 的 `expected_series` 绑定准备时的当前配置；准备后再次修改仍在确认事务中返回冲突，提示重新预览。版本/revision/confirmation token、确定性 Topic ID、重复选中/入队拒绝均保留。栏目/账号停用或当前 Skill 不可用仍明确拒绝选择；兼容响应 `stale` 仅表达当前不可用，不表达年龄或历史配置变化。
- 运行中的配置变化有限重做、failed/interrupted 历史和重启不自动调用 Codex 均保持原契约；Run 执行 lease/heartbeat/期限未改。
- 验证仅使用隔离 SQLite、保存的合成候选和真实 loopback HTTP；不触发正式调研、模型生产、生图或发布。配置竞争受控 researcher 属于本地故障注入，不代替真实联网调研质量验收。
- 既有 `/topic-research/{id}/queue` 在重复请求到达 WriteReceipt 幂等读取前会拒绝已入队候选（422）；本轮保留该行为与零重复写入，不扩大到此原有重放接缝。通用 `queue_topics(source=research)` 不读取候选 stale，仍保留已有请求幂等。
- 后端验证通过：`smoke_topic_research`（真实 loopback HTTP）、`smoke_topic_research_sdk`、`smoke_topic_research_observation`、`smoke_series_composition_service`、`smoke_studio_composition_tools`（真实 loopback HTTP）、`smoke_series_guards`、`smoke_pending_operation_service`，共 7 项。覆盖长久保存/改绑 Skill/编辑 Skill 后的选择、当前 Run 输入、原 Preview 竞争、重复确认与通用入队幂等、历史原件不改和运行中刷新/重启。未运行正式调研或图片生产。

## Python SDK 统一执行器 · 2026-10-06（完成）

- 用户决定调研迁移 Python SDK，不做 CLI/SDK 对照实验。保留候选、查询、幂等、配置变更有限重做和人工入队契约，不迁移历史批次，不自动重试旧失败。
- CodexTopicResearcher 不再继承 CLI 生产器；复用生产/提炼的 SDK client、启动与流式等待 deadline、turn collector、用量和取消处理。默认使用 SDK 捆绑 runtime，显式 executable 覆盖仍验证后传给公开 CodexConfig。
- 每次独立 thread；显式 gpt-6-luna/xhigh、live web_search、read_only/deny_all，禁用全局记忆和项目指令。只把 Skill 作为栏目分析资料，不原生执行生产 Skill。
- SDK 可见消息/工具/搜索通知转换为现有聊天进度契约；本地记录 thread、有限安全事件、最终回执与用量，不向网页输出 reasoning、凭证或原始通知。只有观察到完成的 search 动作才允许 ready，仅打开网页不算搜索成功。
- 验收：隔离真实 SDK 联网调研，经真实 HTTP/聊天显示活动与最终候选，正式队列/生产/发布零写入；故障注入覆盖取消、超时、无搜索、坏回执和失败终态。无需 CLI 对照，不冒充取消测试已真实消耗模型验证。
- 本地回归通过：`smoke_topic_research_sdk`、`smoke_topic_research_observation`、`smoke_topic_research`、`smoke_research_chat`、`smoke_production_progress`、`smoke_native_production`、`smoke_skill_extraction`、`smoke_codex_producer` 共 8 项；SDK 通知使用真实 collector，Mock 仅用于无费用故障注入。compileall / diff --check 通过。
- 真实聊天探针 `python -m tests.live_research_chat --run`：188.9 秒，SDK thread `01a10d37-3d19-7e21-a27b-c868bf2447c9`，调研 ready、聊天 idle、公开活动可见、返回 5 个有 Oxford 来源的英语候选，正式队列零写入。证据 `tmp/research-chat-live-iuvw4x_4/`。**探针整体未通过**：用户要求 1 组，外层 DeepSeek 的工具参数却为 count=10、instructions 写 1 组；执行器按 count 上限校验正常，探针的一条候选断言失败。这是外层工具参数一致性 badcase，不计为 SDK 失败或完整聊天验收通过；本轮不通过放宽断言掩盖。
- 直接指定数量的真实服务探针 `python -m tests.live_topic_research` 通过：请求 2 条，返回 2 条有来源的 Agent 工程选题，SDK thread `01a10d3b-f80d-71d1-8d8a-c9053ca2fddb`；实际搜索与公开事件存在，SQLite 正式 Topic 零写入。证据 `tmp/topic-research-live-20261006-020313/`。调研 SDK 通道验收完成，外层参数 badcase 留待下一步，不声称模型所有工具选择正确。
- 升级需重启实际 `python -m creatoros.web` 进程，再显式发起新调研；历史失败不会自动变为成功，也不自动付费重跑。本轮未停止用户正在运行的正式服务，未修改登录态/全局配置/正式运营库。

## 启动与公开进度修复 · 2026-10-05（完成）

- 批次 `3df6505325ec457490f931df1734c075` 及另两次真实调研均在启动阶段报“未找到 codex CLI”；当前网页服务 `/api/health` 也报告 unavailable。电脑已有可执行文件，但服务进程 PATH 不含它，不能归因为模型额度或调研内容问题。
- 默认 CLI 解析使用已安装 Codex SDK 的捆绑 runtime，允许显式执行路径配置；不改全局 PATH/登录态，不静默忽略无效配置。预检失败立即记录明确 failed 批次，不伪称已联网。
- 每个批次持久化有限公开活动/最近真实活动；GET 返回安全错误分类与进度，原始 JSONL/错误仍在本地。不保存 reasoning，不向网页透传凭证或绝对路径。旧记录只读兼容。
- 活跃任务复用包含当前配置、数量和归一化要求；不是语义去重，不永久缓存已完成批次，不自动重试失败。现有配置变化有限重做、来源、Preview/人工确认与正式队列零写边界不变。
- Web 原会话等待/并发观察与 UI 验收见 web-chat SPEC；执行器启动、秘密过滤、终态/重复与旧批次负例在隔离库验证，真实联网另作低频探针。
- `smoke_topic_research_observation` 通过：空 PATH 捆绑 CLI 真实 --version、预检无 worker、公开窗口/脱敏、不暴露 reasoning、活跃任务复用/不同任务 busy、完成后新批次、旧证据字节未变、真实 Python JSONL 子进程仍正确读回执/用量。既有 `smoke_topic_research` / `smoke_codex_producer` 通过。
- 真实低频原聊天链路通过：DeepSeek → Codex Luna/xhigh，74.7 秒、1 个英语近义词候选、实际搜索事件、终态返回；正式队列/产物零写入。证据和复查命令见 web-chat SPEC，未验证十候选质量或重复同一句话的语义幂等。


## 统一选题库切片（2026-09-14，API/Agent/Web 已接）

- 自动保存的调研建议作为待选选题；人工确认后成为正式队列，绝不因调研自动生产。本轮统一 API 和 Agent 查询，Web 列表合并随后实施。
- 不迁移底层 JSON/SQLite，新增只读 topic-library 投影；同一确定性 topic ID 在确认前后保持一致，正式 Topic 的编辑和执行状态优先，不重复展示。
- 查询支持 all/pending/queued（queued 表示已入队集合，不等于 Run 排队状态），统一分页；待选保留 batch_id/candidate_id、来源、切入点及 stale 状态。
- 继续通过 prepare_topic_selection 创建 Preview，用户确认；过期候选不可选。旧 /topics API 保持正式队列语义，生产 API 不放开待选项。
- 验收：隔离库待选可见且零 Topic 写入；确认前后 ID 一致、只显示一项、改标题/来源保留；筛选分页、过期、未知栏目及直接生产待选拒绝。不重新调用付费调研验证纯投影。
- 实现 GET /api/series/{id}/topic-library；list_series_topics 默认查 all，可筛 pending/queued。已入队按原队列顺序，待选按批次新到旧、批次内原顺序；序号只指当前返回列表，不是持久 ID。正式项保留当前 brief，research_angle 明确只是调研原始角度。
- smoke_topic_research、smoke_web_agent、smoke_agent_studio 通过。真实 DeepSeek 经隔离 loopback HTTP 调用 list_series_topics → prepare_topic_selection，正确选第二条并保留来源，正式 Topic 仍为零。报告 tmp/topic-library-20260914-182135/report.json；这是单次接线验证，不是成功率提升证明。
- 真实模型测试使用合成已保存候选（不伪称联网调研），宿主仅开放查询/预览三个工具防误启动付费生产；模型本身与 API 均真实。没有改正式库或运行生图/发布。
- 当前是请求时只读合并，批次多时会扫描工件，并非跨存储事务快照；最终确认仍重新校验。不迁移库。Web 后续切片已接统一列表/筛选/分页，批次选择复用原编辑面板；仍不支持跨批次一次确认。浏览器验收见 web/SPEC.md，下一步补不同自然语言表达的回归。

## 已确认的边界（2026-09-09）

- Codex 根据栏目定位、受众、绑定 Skill 与已有选题联网调研；默认 10 条，可调整，允许不足。不生成图片、不发布、不直接入队。
- 独立候选批次持久化，用户可再打开；新的调研不覆盖旧批次。选择标题、切入点及顺序后，复用 PendingOperation Preview 和人工确认。
- Agent 暴露 research_series_topics、get_topic_research、prepare_topic_selection；不暴露确认权。GUI 复用相同服务。
- 候选带标题、切入点、推荐理由与来源；选中后完整来源和切入点进入 Topic.brief，再由既有 ContentRun 快照传给生产。
- 运行中栏目/Skill 改变时重新读取最新配置并重新调研；连续变化最多重做两次，之后提示重新发起，避免无限付费循环。就绪后改变配置的旧批次不可选，需按新配置调研。
- Preview 确认时再次校验栏目配置；旧批次不会静默套用新定位。候选使用确定性 Topic ID，重复选中不会重复入队。
- 本步候选修改在候选面板或选择 Tool 内完成；研究型 Preview 不交给通用自由编辑 Parser，以免丢失来源和版本约束。
- Codex-as-Tool 的新建/恢复均显式 gpt-5.6-luna / xhigh，不修改全局配置。

## 存储与复用

- 候选是数据库旁隔离目录里的 JSON 工件；真实生产队列仍是 SQLite Topic，不做重复业务库。
- 复用 Codex JSONL、输出 Schema、进程树回收和用量；新建独立研究 workspace，避免继承仓库开发指令。
- 复用 PendingOperation 的事务、版本冲突和人工确认入口，不另造确认引擎。

## 验收计划

- 隔离 SQLite：候选零入队、修改和顺序、来源传递、重复选择、配置变更、重启和故障状态。
- 低频真实 Luna 联网调研、DeepSeek 调用选择工具；不使用正式运营数据库。
- 浏览器检查候选 → Preview → 确认及移动布局；不调用生图或发布。

## 状态

已完成本切片：研究候选、选择 Preview、宿主确认入队；没有执行内容生产或发布。

## 验证记录（2026-09-09）

- `smoke_topic_research`：隔离 SQLite 的零写入预览、改标题/切入点和顺序、来源进入生产快照与 Prompt、重复选择/确认、栏目变化阻断、运行中按新配置重做、重启不自动重新付费，全部通过。受控 researcher 仅用于配置竞争故障注入，不代替真实调研。
- 真实 Luna / xhigh 联网调研 2 条候选通过；JSONL 观察到 web_search。thread `01a08237-47a9-7871-9c63-c9b10ad89663`，input 182260 / cached input 125952 / output 1935；这是整次多步骤调研的累计用量，不是一次请求上下文长度。来源包括官方 Tool use 和 Context/Compaction 文档。输出有来源不等于每个事实已自动验证。
- 真实 DeepSeek 会话 `11cf4751-e0b4-4c40-abc5-f119838a1708` 调用 get_topic_research → prepare_topic_selection，正确只选 c2 并改标题，返回人工确认链接；未调用生产或重新调研。测试最初把内部 ToolCall 当成供应商 function 格式，修正断言后复核同一次真实会话，没有重复请求模型。
- 浏览器在隔离 `tmp/topic-research-live-20260909-021053/test.db` 走通全选、改标题、c2 上移、Preview、确认、刷新及已入队标记。来源保留，队列为 2 项、尚未生产。390px 无横向溢出；实际截图发现并修复 checkbox 继承全局宽度导致标题挤成一列的问题。浏览器 error 日志为空。
- 关联 smoke：pending_operation_service、codex_producer、producer_skills、web_agent、studio_operations 通过；前端 typecheck/build 通过。
- 正式数据库、用户全局 Codex 配置均未改；本步调用显式覆盖 Luna/xhigh。新建与 resume 命令均有回归断言；没有为验证模型参数再启动图片生产。

## 有意保留的限制 / 下一步

- 同时一项调研，不自动排队；失败/中断需显式重新发起。候选批次持久化，未确认勾选/输入仍是页面本地草稿，刷新会清空。
- 运行中配置改变会最多重新研究两次；已就绪后再改配置，需显式按最新配置重新研究，不能把旧候选重新贴标签。正式队列确认检查栏目字段；外部 Skill 使用不可变版本 ID。
- 不做跨批次语义级硬去重；同一候选确定性 ID 防重复，跨批次避免重复靠已有选题输入。未选不等于永久拒绝。
- 本步不支持从通用 Preview Parser 修改调研型计划；应在候选面板或选择 Tool 中修改，再生成 Preview，防止丢失来源和配置约束。普通手动选题计划的编辑不变。
- 下一步用一个真实自有栏目走通：绑定 Skill → 调研 → 选中入队 → 显式 ContentRun 生产 → 人工验收；随后把真实选择/歧义/冲突样例加入 Agent Eval。暂不引入更多调度和记忆抽象。
