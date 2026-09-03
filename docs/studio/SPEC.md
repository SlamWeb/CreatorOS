# CreatorOS Studio SPEC

## 当前理解

- 基线：`e7581b6`（2026-09-03 核对）。现有 CLI 已有业务服务，但“今日运营”没有先展示账号、栏目和可操作对象。
- 产品主入口改为本地 Web Studio；CLI 保留学习/调试用途，不重写 Runtime 或 PersonClone。
- 参考布局：[`creatoros-studio-wireframe.drawio.png`](../creatoros-studio-wireframe.drawio.png)。图片是概念稿，数字、进度和部分功能不是现有能力。

## 本轮目标

- 只制定下一模型可以直接接手的实施计划，不编写 Web 功能、不启动生产任务。
- 首版闭环：看见账号/栏目 → 管理选题 → 显式开始生产 → 离开页面仍可运行 → 查看真实图片 → 返工或批准。
- 用户这次优先要求解决交互与展示；Studio 完成后回到运营任务 Benchmark，不借此扩张通用 Agent 功能。

## 当前假设

- 首版服务一个本机操作人，允许多个浏览器标签页；不支持公网、多租户、多台机器或多个生产 Worker。
- 默认只允许一个 Codex 生产任务同时执行；其他内容仍可浏览、编辑选题和审批。
- 终点为“内容已批准，尚未发布”；不做平台发布、定时调度、Side Chat 或自动消耗额度的重试。

## 对外影响

- 计划新增 `creatoros/web/` 和 `web/`；复用 storage、operations、runs 与 integrations 的现有服务。
- Web 接线前必须补齐跨页面确认一致性与生产执行所有权，不能照搬 CLI 的无条件恢复逻辑。
- 当前文件为规划；后续每个阶段将实际接口、偏差、验收结果记录在最近模块 SPEC。

## 验收与验证草案

- 空库有创建入口，有数据就展示真实账号/栏目；首页不再要求用户自己猜能做什么。
- 双击、刷新、旧页面确认都不会重复生产或批准错误版本；关闭页面不取消任务。
- 真实图片可预览，失败有原因和下一步；所有未接通功能不伪装成可操作入口。

## 最近验证

- 2026-09-03：只读核对仓库、服务边界、数据模型和已有 smoke；本轮不调用生图或发布 API。
- 文档链接目标存在、`git diff --check` 通过；现有 `smoke_content_storage`、`smoke_pending_operation_service`、`smoke_content_run_service` 三项回归通过。后两者的回滚/中断用既有本地故障注入，不能作为新 Web/真实生图已通过的证据。
- 实施进度：S1–S7 均未开始。下文是实施契约，不是完成清单。

## 1. 给接手模型的阅读顺序

1. 根级 `AGENTS.md` → 本文件 → 当前步骤涉及模块的 `SPEC.md`。
2. 只读当前步骤列出的实现文件及测试；根级 SPEC 有很长的学习历史，不必每步重新加载全部历史。
3. 一次完成一个可以验收的阶段，更新本文件进度与模块 SPEC，测试后 commit/push。用户明确要求继续多个阶段时才连续推进。
4. 本规划轮不实现功能。后续默认从 S1 开始；不能把本文件的“应当做到”改写成“已经做到”。

## 2. 已核实事实与必须处理的接线缺口

| 现状 | Web 接入决定 |
| --- | --- |
| 默认 `data/creatoros.db` 中 Creator、Series、Topic、ContentRun、PendingOperation 均为 0（只读查询） | 首次使用引导是必需功能；测试数据不自动写入正式库 |
| CLI 的作者目录来自 PersonClone，不是本库 Creator | 首页展示自有账号；不自动把数字分身作者转成平台账号 |
| `ContentRepository` 已能创建 Creator/Series、列出 Series/Topics；没有 list_creators | 补查询和少量表单接口，不造第二套存储 |
| `PendingOperationService` 已有 propose/edit/confirm/cancel | 表单与自然语言共用 Preview/确认链路；不注册模型可调用的 confirm |
| `confirm(id)` 读取服务端当前计划，尚未要求调用者提交所见版本 | 多标签页上线前补 expected_version/revision/token 校验及并发保护 |
| `ContentRunService.execute()` 同步调用 Codex | 放进受管理的本地执行器；API 返回后任务继续，不能堵塞 HTTP 事件循环 |
| `recover_inflight()` 无条件中断所有 producing，CLI 每次进入运行记录都会调用 | 必须改为受所有权保护的启动恢复；任何 GET、页面加载、SSE 连接不得触发恢复 |
| lease 字段存在，但没有完整续租/执行者协议 | 不能把“字段已经有了”当作后台执行已经安全 |
| `cancel()` 只改状态，不停止 Codex 子进程 | 首版拒绝取消 producing/validating；不展示假的“立即停止”按钮 |
| 旧 `produce_content_pack` Tool 可直接生产，不经过 ContentRun | Web 只能调用 ContentRunService；不要从网页再走这条旁路 |
| Producer 固定读取 `knowledge-to-carousel` | 栏目首版只允许这个生产 Skill；不显示未验证的任意 Skill 选择器 |
| snapshot 保存了 topic_brief，但 execute 未传给 Producer；栏目描述/受众未传入 | S4 补最小生产输入传递，否则用户填了要求却不生效 |
| 目前有状态事件、Thread ID、最终 usage；没有稳定逐张图片进度或完整底层 Trace | 展示真实阶段/事件/usage；不画虚构百分比，不声称已有完整底层日志 |
| 没有 FastAPI 服务、前端项目或公开站点配置 | 新增本地 Web 接线层；不重写已有服务，不占用 PersonClone 的 8000 端口 |

## 3. 首版产品契约

### 3.1 页面与术语

- 首版导航只保留 **今日 / 账号 / 运行**。栏目与选题在账号详情内；不要提前挂空的 Eval、发布、反馈、设置页面。
- 页面用“账号、栏目、选题、内容、版本、尝试”表达业务；Creator/Series/Topic/Run/Revision/Attempt 用于代码与技术详情。
- Creator 是运营账号配置，创建它不等于已登录小红书。明确显示“尚未接入平台发布”，不制造平台已连接的错觉。
- 日常动作先提供按钮与列表；自然语言用于批量或复杂选题指令，不要求用户靠聊天才能创建第一个栏目。
- 本版首页代表“当前待办”，不是按日期清空的日历。昨天未批准的内容今天仍可见。

### 3.2 今日：打开即知道能做什么

- 顶部显示可运营账号数、可运营栏目数、生产中数、待批准数，全部来自数据库；不用“7 位博主”等原型图固定数字。
- 第一屏展示账号卡片：名称、平台、栏目名称、可用选题数、最近生产状态，以及“打开栏目”。多个栏目可展开，不一上来铺开全部历史队列。
- 下方三列：**待处理 / 生产中 / 待批准**。
  - 待处理：待确认运营计划、中断/失败任务，以及各栏目前 3 个可开始选题；提供“查看全部”。不是把所有 Topics 都塞上首页。
  - 生产中：producing/validating，显示阶段、已运行时间、最近状态更新时间；点击进详情。
  - 待批准：真实封面缩略图、题目、账号/栏目、卡片数量和当前版本；点击验收。
- 账号可运营 = Creator.is_active；栏目可运营还要求父账号和 Series 均 active。历史停用对象仍能在账号页查看，不能开始新生产。
- “可开始选题” = queued 且没有关联 ContentRun。中断会把 Topic 回设 queued，但应显示“恢复原任务”，不能再次创建。
- `daily_content_limit` 是上限，不是每日目标。首版不显示“今日 2/3 完成”或日更达成率，不新增虚假统计口径。

### 3.3 首次使用与空状态

| 状态 | 必须呈现的下一步 |
| --- | --- |
| 无账号 | 简短解释 + “创建第一个账号”；不能只剩一个输入框 |
| 账号没有栏目 | 在账号详情显示“创建栏目”表单 |
| 栏目没有选题 | “添加选题”；填标题/简要要求即可，批量时每行一个标题 |
| 选题已有 ContentRun | “查看任务/恢复任务”，而不是重复“开始生产” |
| 暂无待办 | 显示现有账号与栏目，“添加选题”可达；不自动消耗额度填充内容 |
| API 不可达 | 明确“未连接本地服务” + 重试，保留页面和未提交输入，不伪装空库 |
| Codex 未就绪 | 目录/选题仍可用，生产按钮给出配置原因；不阻塞整个应用启动 |
| DeepSeek 未配置 | 表单选题/确认仍可用；自然语言入口说明不可用，不读取 Key 展示给前端 |

### 3.4 账号与栏目

- 创建账号最少只需展示名；平台固定小红书，handle 可选。ID 由服务端生成安全 slug/UUID（满足现有长度与 pattern），用户不必命名技术 ID。
- 创建栏目填写名称、内容定位、受众；Skill 固定 knowledge-to-carousel，选题/发布策略首版均 approval。隐藏未实现的自动模式开关。
- 栏目详情：定位/受众摘要 → 有序选题列表 → 相关内容。每个选题显示标题、简要要求、真实状态和明确操作。
- “临时加做”使用同一添加选题流程，保存后用户再点开始；不绕过记录创建一次性幽灵任务。
- 第一版只做创建与查看账号/栏目，不做删除、归档和任意配置修改；后续沿同一服务扩展，不另起一套数据模型。
- 调序第一版可用上下移动按钮。若实现拖动，只调选题顺序，不允许拖 Run 卡片来跳过业务状态。

### 3.5 Run Inspector：图片是主体

- 桌面：左侧/中间约 2/3 放图片与缩略图，右侧放题目、账号/栏目、状态、文案、动作；事件时间线默认折叠。
- 图片按 Manifest 的 cards 展示，数量可以是 1、5、6 或更多；使用真实宽高和 `object-fit: contain`，不能写死六张、3:4 或裁掉文字。
- 支持缩略图切换、前后翻页、放大查看；复制发布标题/文案只是复制文本，不声称已发布。
- 展示“文件检查通过：N 张图片可读取”等确定性结果，不能标为“内容质量合格”。知识正确性仍由用户验收。
- 每个历史 Revision 可查看；默认当前版本，查看旧版本时醒目标注并禁用批准。技术 Attempt 位于对应 Revision 下。
- 批准绑定用户所见的 revision_id、artifact_digest 和 run.version。批准后明确“已批准 · 尚未发布”。
- 返工只创建新 Revision 并保留旧图；用户再显式“开始返工”，不能提交反馈就悄悄调用 Codex。
- Run ID、thread ID、digest、usage 和异常细节放技术抽屉，不占主阅读区。未记录的 usage 显示“未记录”，不是 0。
- 不实现只读 Run 问答/Side Chat；原型图中的输入框本版删除。

### 3.6 状态对应动作

| 状态 | 用户动作 |
| --- | --- |
| queued | 开始生产、取消 |
| producing / validating | 查看进展；可离开页面；首版不提供立即取消/返工/批准 |
| interrupted | 显式恢复同一 Revision 的新 Attempt、提出返工、取消；所有权未确认时先禁止恢复 |
| failed 且 retryable | 恢复、返工、取消 |
| failed 且不可重试 | 展示原因，返工或取消；不无限重试 |
| awaiting_approval | 检查图片、批准当前版本、提出返工、取消 |
| approved / cancelled | 只读查看历史；不得自动新建生产替代原记录 |

## 4. 视觉与交互规范

- 深色内容工作台，不做巨大 ASCII 字标、霓虹渐变背景或满屏 KPI 卡。品牌小而清楚，真实内容封面负责视觉吸引力。
- 建议基础色：背景 `#101114`、表面 `#191B21`、正文 `#EEEAF2`、次级正文 `#AAA7B3`、主强调 `#BCA8E0`；成功用灰绿、等待用灰金，危险色只用于真实错误。
- 桌面侧栏约 200 px，内容区 24–32 px 间距，卡片圆角约 12 px；正文 14–16 px，中文使用系统无衬线字体，不下载大字体包。
- 重点按钮一处一个：选题的“开始生产”、产物的“批准”。返工次级，取消收进更多操作。状态同时有文字，不能只靠颜色。
- 1440×900/1280×800 下首屏看见账号、可做的动作和至少一部分待办；390 px 宽不横向溢出，详情转单列、动作固定底部但不遮文案。
- 加载用 skeleton，已有数据刷新不清空；错误在原区域提供恢复入口。API 写入成功前不乐观显示“已批准”。
- Enter 提交普通输入，Shift+Enter 换行；Esc 关闭抽屉且保留草稿。可交互元素有焦点样式，缩略图和图标按钮有可读标签。
- 自然语言命令抽屉用 Ctrl/Cmd+K 打开，当前栏目可作为显式范围 chip；不重复打印品牌横幅或长命令帮助。

## 5. 技术边界与接口契约

### 5.1 目录与运行方式（计划新增）

- `creatoros/web/app.py`：FastAPI app factory/lifespan/静态资源；`__main__.py`：本地启动入口。
- `creatoros/web/schemas.py`：显式 HTTP DTO；`queries.py`：面向页面的只读投影；`routes/`：catalog、operations、runs。
- `creatoros/runs/executor.py`：受管理的单任务本地执行器；所有生产状态仍由 ContentRunService 维护。
- `web/`：React + TypeScript + Vite；React Router 路由，TanStack Query 管服务器状态，Tailwind + 少量 shadcn/ui 基础组件。
- `web/src/features/{overview,creators,runs,operations}/` 按业务组织；小组件就近放，不提前搭完整设计系统或全局状态框架。
- 后端计划地址 `127.0.0.1:8765`；开发 Vite `127.0.0.1:5173`，代理 `/api` 到后端且端口冲突报错。PersonClone 的 8000 不动。
- 演示时前端 build 由同一个 FastAPI 服务托管，根路由 fallback 不能吞掉 `/api` 的 404；刷新详情 URL 能回到页面。
- 本版只监听 loopback、单 API 进程、无 reload 生产模式。开发 reload 仅用于未运行 Codex 的界面调试。
- 不新增 Redis/Celery/WebSocket/Next.js/桌面壳，不迁数据库，不把本地产物或凭证部署公网。

### 5.2 查询 DTO

不得直接序列化 ORM relationship、数据库 URL、原始异常栈或本地绝对路径。DTO 在查询事务内完整构造。

| DTO | 最少字段 |
| --- | --- |
| CreatorView | id、display_name、platform、account_handle、is_active、series 列表 |
| SeriesView | id、creator_id、name、description、audience、skill_name、is_active、可开始选题数 |
| TopicView | id、series_id、title、brief、source、position、status、existing_run_id |
| RunSummary | id、creator/series/topic 展示信息、status、version、active_revision_number、updated_at、retryable、error 摘要、允许动作、可选封面 URL |
| RunDetail | RunSummary + revisions（含 attempts）、确定性 validation、发布文案、cards 的受控 URL、事件读取入口、execution/recovery 提示 |
| PendingOperationView | id、status、revision、version、request_text、message、按名称显示的 before/after、confirmation_token、可选 usage |
| OverviewView | 真实 counts、CreatorView 列表、三类看板摘要、待确认运营计划摘要 |

- 允许动作由后端策略提供；前端隐藏/禁用只提升体验，不能替代 Service 校验。
- 列表使用 `items,total,offset,limit`；limit 默认 50、上限 100。概览只取必要摘要，计数不能使用截断后列表长度。
- 时间输出带时区的 ISO8601（统一 UTC），浏览器本地展示；duration 按服务端记录/起始时间显示，不伪造预计剩余时间。

### 5.3 REST 路由

以下是目标契约，S1 只实现查询；写接口按阶段逐个开放。

| 路由 | 行为与输入 |
| --- | --- |
| GET /api/health | 本地服务/数据库是否可用及生产器配置状态；不返回 Key，不运行付费探针 |
| GET /api/overview | 当前账号、栏目和待办投影，不调用 LLM/PersonClone/Codex |
| GET /api/creators；GET /api/creators/{id} | 列表/详情，未知 ID 404 |
| GET /api/series/{id} | 栏目详情及父账号展示信息，支持直接打开栏目 URL |
| GET /api/series/{id}/topics | 有序 TopicView 与全量数量 |
| POST /api/creators | display_name、可选 account_handle；固定平台，服务器生成 ID |
| POST /api/creators/{id}/series | name、description、audience；服务端固定首版 Skill/策略 |
| POST /api/operations/preview | 结构化 OperationPlan；生成并持久化待确认计划，不写 Topics、不调用模型 |
| POST /api/operations/propose | request_text、可选 series_id 范围；调用现有 Parser 后持久化提议 |
| GET /api/operations；GET /api/operations/{id} | 活动计划列表/精确计划详情 |
| POST /api/operations/{id}/edit | instruction、expected_version、expected_revision；模型解析结束再检查版本 |
| POST /api/operations/{id}/confirm | expected_version、expected_revision、confirmation_token；只确认用户所见计划 |
| POST /api/operations/{id}/cancel | expected_version、expected_revision；不改 Topics |
| GET /api/runs；GET /api/runs/{id} | 可按 creator_id/status 过滤的列表、RunDetail |
| POST /api/runs | topic_id；使用服务端默认 `content:{topic_id}` 幂等键，不允许浏览器任意制造第二个 Run |
| POST /api/runs/{id}/execute | expected_version；包含开始和显式恢复；接收成功 202，不等待生图完成 |
| POST /api/runs/{id}/revisions | instruction、expected_version；只创建返工版本，不自动生产 |
| POST /api/runs/{id}/approve | revision_id、artifact_digest、expected_version；重新验收后一致才批准 |
| POST /api/runs/{id}/cancel | expected_version；仅允许非运行态，保留产物 |
| GET /api/runs/{id}/events | after_id 游标读取有序业务事件，供时间线/断线补齐 |
| GET /api/runs/{id}/events/stream | SSE，仅订阅，不驱动生产；S5 接入 |
| GET /api/runs/{id}/revisions/{revision_id}/cards/{order} | 只返回该 Run/Revision Manifest 中的对应图片，不接收任意 filesystem path |

- 创建返回 201，幂等取回返回 200；重复 execute 已在运行时返回 409 `already_running` 并附原 run_id，前端查询原任务，不能多开 Attempt。重试网络请求不代表重试生产。
- 错误区分：404 不存在；422 参数/不支持的 Skill；409 版本冲突/状态不允许/执行器忙；503 依赖不可用。统一业务错误体 `error.code/message`，校验错误可附字段位置。
- “旧版本”收到 409 后刷新数据并请用户重新确认；前端不得自动带新版本重发有副作用请求。
- 对意图解析类 POST 不做静默网络自动重试；失败保留输入。表单双击必须禁用重复提交。
- 写请求限同源与 JSON；开发只放行明确的本机 Origin/Host，不设置任意 `*` 跨域写权限。本版无公网认证，不因此对外暴露端口。

### 5.4 两种确认的正确边界

**选题计划：** 表单直接构造现有 OperationPlan，经 preview 持久化；自然语言走 Parser，二者最终使用同一 PendingOperationService。

- 扩充 PendingOperation 的 ORM 乐观版本保护（必要时新增 version migration），不要只在路由外读一次后比较。
- edit/confirm/cancel 在事务内同时检查所见 revision/version/status；confirm 再比对 token 与业务状态指纹。
- 模型调用在事务外执行，不能持有 SQLite 写事务等待 LLM。persist_edit 仍需 CAS，旧解析结果不得覆盖新计划。
- confirmed/succeeded 事件与 Topics 写入保持原有原子事务；错误处理也不能把另一个已成功/已修改的计划覆盖成 failed。
- 相同已成功确认的重复提交可返回原回执；带不同 revision/token 的重放必须冲突，不确认后来编辑的计划。
- 调序必须发送完整 Topic ID 列表，包括当前被筛选隐藏的项。初版建议仅在未筛选的完整列表开启调序。

**内容批准：** 沿用现有 revision + digest + run.version 协议，不用 PendingOperation 再套一层审批。批准是验收，不是发布。

### 5.5 本地后台生产：收敛而明确

首版采用 **单 API 进程 + 专用单线程执行器 + SQLite 持久化状态**。这是本地托管任务，不是云后台/分布式调度。

1. 生命周期统一由 Web app 管理执行器；HTTP 处理只做校验、原子认领、提交，Codex 阻塞调用在专用线程执行。不能只丢进无人管理的 BackgroundTasks。
2. 只有用户按“开始/恢复”才提交。默认容量 1：其他 Run 开始时返回 `producer_busy` 并链接当前任务，不建立隐式排队或自动补货。
3. 启动进程持有同一项目/数据库的 OS 级单实例锁（可用小型跨平台锁库）；另开 CLI 或第二个 Web 写进程时清晰拒绝，不允许 CLI 无条件恢复正在运行的任务。锁由 OS 持有，不能仅用一个文件存在性判断。
4. 每个已认领 Run 保存唯一 lease_owner/heartbeat；开始、完成、失败回写需校验同一 owner、Revision/Attempt，过期 worker 不得覆盖新结果。领取与新建 Attempt 只能成功一次。
5. heartbeat 约 5 秒，租期约 30 秒作为可调常量；没有 token 输出不等于卡住。心跳只代表执行器活着，超期标记“状态待核实”，绝不能据此自动重启 Codex。
6. 心跳更新不要无意义地递增用户审批 version；业务迁移与技术活性分开更新，并保持 owner 条件约束。每个线程/请求创建自己的 Session，不共享 ORM Session 或已绑定 Repository。
7. 关闭浏览器/切页/SSE 断开只停止观察。关闭服务会停止本地执行，保存 interrupted；重启不自动再消费额度。
8. 有界优雅关闭必须清理本次创建的 Codex 子进程树，不得按进程名称全杀。记录本次子进程身份（PID + 创建时间/owner）供核实；硬崩溃存在孤儿子进程或身份不明时先阻止恢复，明确提示人工确认旧执行者已退出。
9. 恢复只在取得独占权、确认没有旧执行者后发生。producing 转 interrupted；validating 若已有完整产物只重跑确定性验收，不重新生图；queued 不自动开始。
10. 首版不提供生产中的即时取消按钮；现有 cancel 必须拒绝 producing/validating，避免状态已取消但 Codex 仍写结果。以后能真实终止并测试完成后再加“停止”，不能只改状态。

S4 必须覆盖认领成功但调度失败、Producer 初始化失败、旧 worker 晚到结果、子进程异常退出等分支，不能留下无人负责的 producing。不能证明旧执行者已退出时宁可报告恢复受阻，不承诺端到端 exactly-once。

### 5.6 生产输入与产物

- 新 ContentRun 的输入快照补栏目 description/audience；旧快照兼容缺省值，不迁坏已有 JSON。execute 把 topic_brief、栏目定位/受众传入 Producer prompt。
- Producer 仍只接受已选 Topic + 固定 Skill，不能自行改栏目/选题。UI 不提供 model/路径/任意命令配置。
- 保留 `outputs/<creator>/<series>/<run>/revision-NNN/attempt-NNN` 与现有 Manifest；不改变已生成内容的目录，不导入旧输出冒充新 Run。
- 图片路由通过 Run → Revision → Manifest → card.order 解引用，resolve 后验证位于该产物根内；拒绝 `..`、跨 Run 引用、symlink 逃逸和缺失文件。
- 返回标准图片 MIME，使用包含版本/digest 的资源标识避免返工后误用缓存；批准时仍重新计算 digest，不能以缓存键替代校验。
- 不开放任意静态挂载 `D:/`、outputs 根目录列表或 `/files?path=...`。发布文案按文本渲染，外部来源 URL 仅允许合理 http(s) 链接。

### 5.7 进度与刷新

- S2–S4 先用 TanStack Query 轮询：页面可见且存在活跃任务时约 2 秒，闲置时约 10 秒；切回窗口立即刷新。
- S5 的 Run Inspector 用 SSE 订阅业务事件：`id` 取持久化 ContentRunEvent.id，包含 event_type/run_id/revision_id/attempt_id/created_at；不直接透传任意 Codex JSONL。
- 连接先返回 snapshot 和游标；断线用 Last-Event-ID/after_id 补齐。前端按 id 去重，事件用于触发查询刷新，不把事件流当成唯一状态真相。
- 心跳使用不落库的 SSE keepalive；断开后展示“重连中”，轮询回退。连接成功/重复订阅不能新增 Run、Attempt 或调用模型。
- 不支持逐卡进度时显示“Codex 正在生产 · 已运行 …”，不显示概念图的 `3/6`、假百分比或假剩余时间。

## 6. 分阶段实施与验收

以下步骤按依赖顺序执行。每个阶段可独立提交；阶段内可以一次做完完整纵向切片，不必拆成每轮只改几行。

### S1 — 只读业务 API（下一步就做这个）

- 阅读：storage 的 models/repository/database，runs 的 service/repository，operations 的 service/repository。
- 新建 `creatoros/web/SPEC.md` 记录本模块边界，再加 app factory、DTO、只读 queries 与 5.3 查询接口（图片/SSE 暂不做）。补 list_creators、必要的聚合/过滤查询。
- FastAPI/uvicorn 放独立 `requirements-web.txt` 引用现有 requirements；挑兼容当前 deepcode Python 的版本并记录验证，避免顺手升级 AI 依赖。
- 数据库迁移显式执行一次，不在每个请求 create_all；无 DeepSeek Key/PersonClone 服务也能查目录。生产器此阶段完全不初始化、不恢复。
- 新测 `tests.smoke_studio_queries`、`tests.smoke_studio_api`：真实临时 SQLite + HTTP TestClient，验证空库、关联投影、计数、分页、404、非法查询、无秘密/路径泄漏。
- 验收：真实启动服务后 GET /api/overview 对空正式库返回合法空结构；查询前后行数不变；此时没有写/生产端点。
- 提交建议：`feat: expose read-only studio catalog api`。

### S2 — 能看懂的首页和账号目录

- 新建 `web/SPEC.md`，初始化 React/Vite/TypeScript、路由、Query、基础样式和 API client；提交 lockfile，忽略 node_modules/dist。
- 页面 `/`、`/creators`、`/creators/:id`、`/series/:id`、`/runs`；Run 点击暂显示真实只读摘要，S5 再补图片 Inspector。
- 落实 3.2/3.3 的真实账号卡片、栏目、空状态、加载/错误状态与响应式样式。创建表单未接通前明确提示下一阶段，不能点击后无反应或虚构成功。
- 不要求用户装 BGE-M3 或启动 PersonClone 才能看 Web；不在组件里写业务查询规则/直接访问 SQLite。
- 验收：npm build + typecheck；API 数据改变后刷新可见；正式库为空则显示首次使用引导，不展示虚假账号。
- 用隔离的测试数据环境检查有数据布局，界面标记测试环境；截图至少 1440×900 与 390 px，自己查看后修正溢出/层级问题。
- 提交建议：`feat: add studio overview and creator navigation`。

### S3 — 从空库走到真实选题队列

- 接创建账号/栏目表单与 HTTP DTO；补服务端唯一性、active、标题/ID/Skill 校验，不靠前端兜底。
- 表单添加/调序生成现有 OperationPlan → PendingOperation Preview → 人工确认；计划 Preview 展示名称、前后顺序与新增内容，UUID 留技术详情。
- 按 5.4 修正 PendingOperationService 的版本确认与并发错误处理。若新增 ORM version，用新 Alembic migration；不改写旧 migration。
- 更新 CLI 传入所见版本，保持 CLI smoke 通过；不引入第二套直接写 Topics 的 Web 方法。
- 新测 `tests.smoke_studio_operations`：无模型表单链路、重复确认、两标签页旧计划、解析期间编辑、确认/取消竞争、事务失败全回滚。
- 验收：全新临时数据库只靠网页创建账号→栏目→两个选题，刷新后数据和排序仍在；确认前 Topics 不变。
- 提交建议：`feat: add studio onboarding and versioned topic approval`。

### S4 — 生产不再卡住页面

- 阅读并更新 runs/integrations/storage 的 SPEC；按 5.5 加管理式执行器和单实例保护，修正 recover_inflight/cancel/开始认领/晚到回写。
- 接创建 Run、执行/恢复与状态查询；浏览器“开始生产”明确显示会调用 Codex，POST 超时后先查询，不能重新造 Run。
- 按 5.6 传递 brief/栏目描述/受众；老生产 Tool 的默认参数仍可运行，Web 不走它。
- 新测 `tests.smoke_studio_executor`：ControlledProducer 做耗时/中断/故障注入；同时读取概览、双击、第二任务 busy、认领失败回收、旧 owner 拒绝、心跳不冒充进度、关闭后恢复。
- 子进程回收用本地无费用子进程测试；轻量真实 Codex 协议沿用 `tests.live_codex_resume_protocol`。先不反复调用完整图片生产。
- 验收：任务运行时仍能浏览/添加别的栏目选题；关掉浏览器任务继续；服务有序停止后重启显示 interrupted，只有显式恢复才新增 Attempt；同篇 thread 不变。
- 硬崩溃后的孤儿识别/未知所有权必须给出安全失败路径。只测试正常结束、不测恢复，不能标本阶段完成。
- 提交建议：`feat: host recoverable production outside web requests`。

### S5 — 图片验收、返工和可追踪进度

- 接受控图片路由、RunDetail 的 Manifest 投影、历史 Revision/Attempt、SSE 事件；实现 3.5 的 Inspector。
- 接 approve/revisions/cancel，依赖既有业务状态机和所见版本；只在成功响应后改变 UI。
- 新测 `tests.smoke_studio_artifacts`：多种卡片数、坏图/缺图、跨 Run 取图、路径逃逸、旧 revision、旧 digest、旧 version。
- 新测 `tests.smoke_studio_events`：事件有序、重连去重、断订阅不终止任务、GET/SSE 无副作用。
- 前端验收：5 张和 6 张包都可看；旧版本与新版本明显区分；409 显示“内容已变化，请重新检查”，不悄悄替用户重新批准。
- 真实已有图片可用于只读渲染 QA，故障测试使用隔离临时产物，不能篡改用户的真实输出。
- 提交建议：`feat: review content revisions and observe run events`。

### S6 — 自然语言命令入口

- 接全局命令抽屉：用户说“给这个栏目加 MCP 和 Tool Calling，把 MCP 放第一条”→ Parser → 持久化 Preview → 用户确认。
- 扩展 parse 输入支持可选 series_id 范围，服务端校验范围；用户话里另指栏目且含糊时询问，不能静默执行到错误栏目。
- UI 展示 ready/needs_clarification/unsupported；自由修改沿用同一 operation_id，版本由服务端递增。
- 首版仍只支持 add_topics/reorder_topics。诸如“自动生产所有选题”“发到小红书”明确不支持，不能假称 Agent 已在执行；提供相关页面入口即可。
- 模型解析/工具目录与 host 确认仍分离；不能给普通 Agent 加一个自动 approve 工具来图省事。
- 新增 `tests.live_studio_operation_workflow`：低频真实 DeepSeek + 临时库验证成功、歧义、越界请求；可复用已有 live_pending_operation_workflow。
- 验收：自然语言与表单产生同样的最终队列；页面刷新恢复待确认计划；未确认绝不写 Topics，不从页面打开就调用模型。
- 提交建议：`feat: add natural-language studio operation preview`。

### S7 — 真实演示闭环与交付

- 本地 build 同源托管、详情刷新、一个启动命令、README 启动说明、依赖/服务错误提示补齐。
- 新增浏览器 e2e：首次使用 → 选题 Preview → 确认 → Run → 返工/批准；受控生产器仅用于故障注入与自动化稳定性，不冒充真实 Codex 验证。
- 独立临时运营账号/栏目做一次真实 ContentRun 生图验收（不发布）；用户用量不足时延期并如实记录，不能用 mock 标记真实链路通过。
- UI 最终截图至少：首页有数据、首次使用、生产中、真实图片 Inspector、失败/过期确认。工具能截图就自己看图迭代，不把视觉反馈全部交给用户。
- 将演示流程整理成 2–3 分钟：账号/栏目可见 → 自然语言批量选题 → Preview → 生产时继续浏览 → 图片验收 → 展示一次重启恢复。
- 只有经确认、可公开的图片/账号信息才能用于 README；截图不包含本地路径、凭证、生产 trace 或未授权个人内容。
- 验收：CLI 回归通过，新增 backend smoke/frontend typecheck/build/e2e 通过，真实联调和费用单独记录，SPEC/README 与现状一致。
- 提交建议：`test: verify studio end-to-end production workflow`（文档/展示资源可另做清晰提交）。

## 7. 验证命令与环境

当前已知 Python：`D:\Anaconda4.7g\envs\deepcode\python.exe`。不要用缺依赖的系统 python；不要重新下载已有 BGE-M3。未创建前端前不安装 Node 包。

现有回归入口：

```powershell
conda run --no-capture-output -n deepcode python -m tests.smoke_content_storage
conda run --no-capture-output -n deepcode python -m tests.smoke_operation_plan
conda run --no-capture-output -n deepcode python -m tests.smoke_pending_operation_service
conda run --no-capture-output -n deepcode python -m tests.smoke_content_run_storage
conda run --no-capture-output -n deepcode python -m tests.smoke_content_run_service
conda run --no-capture-output -n deepcode python -m tests.smoke_content_run_cli
conda run --no-capture-output -n deepcode python -m tests.smoke_codex_producer
```

后续目标命令（文件/脚本尚未实现，不是现在可用的启动说明）：

```powershell
conda activate deepcode
python -m pip install -r requirements-web.txt
python -m creatoros.web
npm --prefix web ci
npm --prefix web run dev
npm --prefix web run typecheck
npm --prefix web run build
npm --prefix web run test:e2e
```

- 测试用临时 SQLite、独立输出目录、独立锁路径；不 reset/seed/删除正式数据库。
- 正常只读请求和页面 smoke 不应消耗任何 LLM token。真实模型验证要记录测试名字、请求次数、usage 和是否生成图片。
- 日常改动运行关联 smoke；S7 再完整回归。真实服务不可用就报告依赖问题，不能把失败包装成通过。

## 8. 首版之后：回到 Agent Eval，不无限装修

- 从 S3/S6 的真实操作记录挑选小型任务集：同名栏目歧义、多步新增调序、修改既有 Preview、明确拒绝越界、旧确认冲突、中断恢复。
- 判分以最终数据库/产物/状态为准，轨迹用于定位 badcase，usage 用于比较成功任务成本；不先堆十几个指标。
- 浏览器 e2e 是产品测试、Parser 测试是意图解析测试；只有实际通过 Agent 执行任务并评估轨迹，才称 Agent Benchmark。不能混写完成度。
- 每个 case 写初始状态、用户请求、允许动作、目标环境状态和不可发生的副作用；后续比较“自由 Agent”和“Skill/Workflow 辅助 Agent”。
- 先不上 Eval 页面、人格视角召回、MCP 公网服务、长期记忆、调度或发布。首版可用后用户再定其中一条主线。

## 9. 调研依据与推断边界

- [Claude Code Desktop](https://code.claude.com/docs/en/desktop)：参考会话导航、工作区和任务结果可检查的组织方式，不复制开发者终端界面。
- [Cursor Worktrees](https://cursor.com/docs/configuration/worktrees)：参考并行执行的隔离思路；本版选择单写执行者，不因此引入多 Agent 写同一目录。
- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/) 说明可在响应后执行工作；“HTTP 已返回”本身不提供本项目所需的持久化恢复。专用执行器与 DB 所有权是本项目的设计选择。
- [FastAPI Lifespan](https://fastapi.tiangolo.com/advanced/events/) 用于管理启动/关闭资源；[SQLAlchemy Session 并发说明](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#is-the-session-thread-safe-is-asyncsession-safe-to-share-in-concurrent-tasks) 要求并发线程/任务分别使用 Session。
- 原型中的每日目标、逐张生成进度、Run 只读问答属于愿景；本契约已明确缩小范围，接手者不得按原型图全部补功能。

## 10. 交接与执行记录

| 阶段 | 状态 | 实际提交/验证 |
| --- | --- | --- |
| 规划 | 完成，未实现功能 | 见最近验证；基线 e7581b6 |
| S1 查询 API | 未开始 | — |
| S2 可读首页 | 未开始 | — |
| S3 首次使用/选题 | 未开始 | — |
| S4 后台生产 | 未开始 | — |
| S5 图片验收 | 未开始 | — |
| S6 自然语言入口 | 未开始 | — |
| S7 联调与交付 | 未开始 | — |

换模型后可以直接发送：

> 请读取 AGENTS.md 和 docs/studio/SPEC.md，按规划从第一个未完成阶段开始。这次只完成一个阶段，不要一次实现全部 Web。先核对当前 Git 和相关模块 SPEC；复用现有服务，按该阶段验收条件测试并更新 SPEC，独立 commit/push。遇到规划与代码冲突先报告具体证据与最小调整，不重复询问已确定的产品方向；不得写入演示假数据到正式库，不调用发布能力。
