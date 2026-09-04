# S6 — 自然语言选题指令：Luna 实施契约

## 当前理解

- 编写日期：2026-09-04；核对基线：`e17a68b`。本文件是计划，**S6 功能尚未实现**。
- 用户已授权下一模型代写本阶段，不需要重新采访、不要求用户手敲代码。只完成 S6，不顺带实施 S7。
- 总产品契约见 [Studio SPEC](../SPEC.md)。本文件细化 S6，并纠正只读核对发现的实现缺口；历史“阶段已完成”不代表以下缺口已修复。
- Python 继续承载解析、校验、事务和状态；React/TypeScript 只负责展示与用户输入。不是重写 Agent，也不是新增第二套 Workflow。

### 必读与复用顺序

1. 根级 `AGENTS.md`、本文件、`docs/studio/SPEC.md` 的 S6 与接口/确认边界。
2. `creatoros/operations/SPEC.md`、`creatoros/web/SPEC.md`、`web/SPEC.md`、`creatoros/storage/SPEC.md`。
3. 下表指向的实现，再读相关 smoke。无需重新加载根级 SPEC 的全部学习历史。

| 已有实现 | 当前真实缺口 / S6 决定 |
| --- | --- |
| `operations/parser.py`：OperationPlanParser + 三态决策，复用 StructuredModelProvider | 仅接收 user_request；没有栏目范围、父账号名称/active 信息 |
| `operations/service.py`：propose/edit/persist_edit/confirm/cancel | 保留同一业务服务；模型调用在事务外，结果写回必须防晚到覆盖 |
| `web/writes.py`：PendingOperationService(parser=None) | Web edit 虽有路由，实际不能自然语言解析；缺 POST /propose |
| `storage/models.py`：PendingOperation 仅有 revision | 尚无独立 ORM version；Service 把 version 当 revision，queries 却用 getattr(..., 1)，修改后的 HTTP 确认不一致 |
| `operations/executor.py`：计划校验、状态指纹、整份事务执行 | 保留；现有 Preview 只有 ID 顺序，不能直接作为可读页面；补 active 校验 |
| `web/queries.py`：GET operations/详情、overview 待办 | 恢复入口可复用；需返回真正 version、范围、可读变更，不直接透传任意 JSON |
| `SeriesPage.tsx`：表单 → Preview → 确认 | 标题存在前端局部 state，刷新丢失；共用服务端 Preview，不再维护第二份展示真相 |
| `TodayPage.tsx`：待确认计划投影 | 当前 href=null 无法打开；有计划时还会遮掉失败 Run，S6 要同时保留两类待办 |
| `tests/live_pending_operation_workflow.py` | 已有真实 DeepSeek + 临时库流程；本阶段加 HTTP、范围、修改和恢复验收 |

注意：现有 `smoke_studio_operations` 只覆盖基础表单、错误版本和串行重复确认，**不能证明双请求竞争、修改后确认或 Web 模型接线已通过**。

## 本轮目标

用户打开某栏目，说：“加 MCP 和 Tool Calling，把 MCP 放第一条。”页面显示新增内容和前后顺序；用户可以补一句修改要求，确认后才写入队列。刷新后仍能找回同一计划。

### 成功示例（验收时固定此初始状态）

- 测试账号「面试知识实验室」，栏目「Agent 每日一题」，初始队列：AgentState、AgentContext。
- 验收用明确指令：“在队尾加 MCP 和 Tool Calling，再把 MCP 放第一条，其余保持顺序。”Preview 为 MCP、AgentState、AgentContext、Tool Calling；数据库仍只有原来两条。不要用未明确顺序的句子配死板断言。
- 再说：“把 Tool Calling 放第二条，其他保持顺序。”：同一 operation_id 的下一 revision，Preview 为 MCP、Tool Calling、AgentState、AgentContext。
- 用户点“确认写入队列”：数据库与所见顺序一致；不创建 ContentRun、不调用 Codex、不发布。
- 用户之后想生产，沿现有栏目页显式“开始生产”；S6 不把自然语言确认偷换为生产确认。

### 明确不做

- 新操作类型（删除、创建栏目、选题研究、生成、批量生产、发布、定时任务）。首版仅 add_topics / reorder_topics。
- 聊天窗口、Side Chat、Agent Loop 改造、自动重规划、长期记忆、MCP Server、Agent Benchmark 或新 Eval 页面。
- 新前端框架、设计系统、消息队列、后台解析任务、Redis、多 Worker、公网部署。
- 自动重试模型、自动修正后执行、自动确认；不把 confirm 注册为 Agent Tool。

## 当前假设与设计决定

### A. 页面：一个共用抽屉，不再造一个聊天页

1. `Layout` 常驻一个命令抽屉和顶部“运营指令”按钮，Ctrl/Cmd+K 打开；移动端靠按钮可达。
2. 栏目页提供“用一句话调整”，打开时显示「账号 / 栏目」范围 chip。首页默认“未限定栏目”，用户可从真实目录选择，选项需包含账号名以区分同名栏目。
3. 仅新草稿允许改范围；已有计划的范围由服务端记录决定，不能跟随页面导航悄悄改变。需改范围时新建草稿，保留旧计划。
4. 新草稿用 `?command=new` 打开；保存后替换为 `?operation=<id>`。已有计划从该参数 GET 恢复；首页待办链接 `/?operation=<id>`。关闭移除对应参数，不改数据库。保留无关 URL 参数。
5. 草稿只要求当前页面会话关闭/重开不丢；已持久化计划要求刷新/重启可恢复。不承诺未提交草稿跨浏览器恢复，不把计划真相写 localStorage。
6. 输入区域示例一句即可；主按钮“生成预览”，明确“只生成选题计划，确认后才写入”。Enter 提交、Shift+Enter 换行；中文输入法 composition 期间 Enter 不提交。
7. 请求中显示“正在整理选题计划”，禁用重复提交。可关闭抽屉继续浏览，关闭不是取消请求；Layout 持有请求状态，晚到结果归原草稿，不覆盖别的计划、不强行弹窗。显示“计划已就绪 / 查看”提示即可。
8. Esc 关闭、焦点回到触发按钮；抽屉约桌面 520–640px、移动端全宽，延用现有灰紫配色。使用现有 React/CSS；可用原生 dialog，不新增 UI 依赖。

### B. 决策与页面状态

| 服务端状态 | 页面行为 |
| --- | --- |
| awaiting_approval + decision ready | 展示账号/栏目、每步新增/调序、完整最终顺序；允许确认、修改要求、取消 |
| needs_clarification | 显示需要补充的问题，输入补充后走 edit；没有确认按钮 |
| unsupported | 显示只支持加选题/调序，可改写或取消；禁止把支持部分偷偷执行 |
| stale | 提示队列已变化；显式“重新生成预览”走 edit，不自动调用模型、不继续旧确认 |
| succeeded | “已写入队列 · 尚未生产”，链接所有受影响栏目；不出现生产成功提示 |
| cancelled / failed | 展示真实原因、返回/新建草稿入口；不改终态成可确认 |
| 404 / API 不可达 | 错误与重试，不伪装成空预览 |

- 展示业务名称、标题、顺序和简要要求。UUID、token、version/usage 收进“技术详情”；不能只显示模型 message 就让用户确认。
- 分开显示“新增”和“调序”，另有最终队列，避免 add→reorder 让用户误以为新增了两遍。
- 表单 Preview 也打开此抽屉；保留无模型添加选题路径。成功时刷新相关 topics/series、overview、operations 查询。
- 后台 refetch 发现版本不同应显示“计划已更新，重新查看”，禁用旧确认；不能在用户没看到新内容时替换确认载荷。
- 网络错误保留输入。propose 响应丢失先提供“查看待处理计划”，不得自动重发；首版不承诺首次解析网络层 exactly-once。

### C. 范围与解析：提示词约束 + 确定性检查

- 为 `parse`、`propose` 增加可选 keyword 参数 `series_id=None`；旧 CLI 无范围调用仍可用。
- 给 PendingOperation 持久化 `scope_series_id: str | None`，仅在创建时设置；edit 从记录读，不信前端临时指定范围。旧记录为 None，不能按当前打开栏目猜范围。
- 有范围：在请求模型前验证栏目及父账号存在且 active。不存在 404，停用 409；均零模型调用。
- 解析 payload 明确区分 user_request、scope、current_state。带范围时给目标栏目的完整有序队列，以及其他栏目的名称/账号摘要用于识别指代冲突，不发送其他栏目的全部队列。
- 无范围：使用真实目录；补 creator display_name、series name、active，防同名栏目误选。初版不加 embedding 或另一模型做意图分类。
- “这个栏目”只有在有明确范围时可直接解析；无范围且有多个可选栏目必须 needs_clarification。同名栏目缺账号限定也必须询问。
- 用户在 A 范围里明确提 B：询问范围冲突，让用户新建 B 范围草稿；不要把 B 的要求执行到 A，也不要静默突破 A。
- 模型返回 ready 后，逐项检查目标是否存在、active、是否全在 scope；越界结果不得持久化成可确认计划，返回可解释的 needs_clarification（plan=None）。未知 ID/非法结构不得编造补齐。
- 无范围的明确多栏目加选题/调序仍可沿用现有 OperationPlan 多操作能力；所有栏目都需校验并在预览展示，不只展示第一项。
- 带“不用确认，直接执行/生产/发布”等要求不能改变宿主授权；包含不支持的业务动作时整项 unsupported，不局部成功。
- 编辑需要模型看见当前完整请求、**当前 plan**、补充指令和最新目录，以便“把第二条放第一条”等指代稳定；旧 PendingOperation 的 plan 为空也要能补充澄清。
- 调序使用全部且仅属于该栏目的 Topic ID，不能只把页面前 100 条给模型，不能把 omitted 项当删除；继续用 executor 校验。
- 不实现复杂长对话压缩：请求长度沿用 HTTP 上限；本阶段的 Parser 不是已有 Agent Session。测试与文案不得混称 Agent Memory。

### D. 版本与事务：先修已暴露的确认缺口

这不是另开可靠执行项目；它是允许用户修改计划、刷新和多标签确认的前置条件。

1. PendingOperation 新增真实 `version`，复用 ContentRun 的 SQLAlchemy `version_id_col` 模式；`revision` 仅代表草稿改版，`version` 代表持久化状态变化。不要继续 version=revision 或返回常量 1。
2. 新 Alembic migration（按当前 head 递增；核对时为 `20260902_0003`），加 version 与 scope_series_id；不改旧 migration。旧数据 version 初始化为合法值、scope=None，保留原 plan/events/revision。
3. 所有 edit/confirm/cancel 校验用户所见 expected_version + expected_revision；edit 调用模型前检查 editable 和版本，模型返回后事务内再检查。
4. 每个请求用自己的 Session；加载上下文后关闭事务再调模型。ORM StaleDataError / 条件写入失败转 409，不进入通用异常路径把最新记录改 failed。
5. confirm 的业务写入、状态迁移、事件保持同一事务。确认与取消竞争只能有一个获胜；出现 DB busy 可明确冲突重查，不自动确认。
6. `_mark_terminal` 必须带原 version/revision/status 条件。旧请求失败不能覆盖另一请求已经 edited/succeeded/cancelled 的计划；失败时整份业务写入回滚。
7. 成功确认后 version 会增加。重复发送**同一成功请求**仍返回原成功回执，不重复写 Topics；在 CONFIRMED 事件中记录该次 expected_version/revision/token，用于匹配原确认。不同凭证/草稿重放返回 409。旧成功记录缺此证据时保守拒绝未知重放，不伪造历史。
8. unsupported 当前可 edit 但不能 cancel，且列表口径不一致；本阶段统一为可取消的未执行计划，与首页/operations 活动列表一致。关闭抽屉≠取消计划。
9. CLI 宿主也提交所见 version/revision/token，别在共享服务里保留无条件确认旁路；同步更新 CLI smoke 和直接服务调用测试。
10. executor 的 Preview/执行均检查栏目和父账号 active；停用后旧计划不能继续确认。保留现有状态指纹校验，不再另造一套哈希。

## 对外影响与实施顺序

### S6.1 先完成确认基础与可读 Preview

- 改动：`storage/models.py`、新增 migration、`operations/service.py/repository.py/executor.py/models.py`、CLI 与关联测试。
- 在 `OperationChange` 补可缺省的展示字段：creator_name、series_name、before_topics、after_topics；每个 Topic 展示快照包含 topic_id/title/brief。既有 before_order/after_order 保留，仍是执行校验依据。
- 构建 Preview 时把原有 Topic 与新增 Topic 合并成暂存投影，后续调序引用同一映射；快照随 preview_json 保存，刷新不再依赖前端 previewTitles。
- 兼容旧 preview_json：新字段有默认值；详情读取可用旧 plan 的新增标题和现有目录补显示，不写回、不自动重做 token；确实找不到时显示“历史选题 + ID”，不能编造标题。
- HTTP DTO 明确输出允许的字段，确认凭证只在需要它的详情中提供；不要把任意 JSON/路径直接给页面。
- 验收后可独立提交 `fix: make operation revisions safe to review and confirm`；不要先提交一个无法确认的自然语言 UI。

### S6.2 接模型与 HTTP

- 改动：`operations/parser.py/service.py`、`web/writes.py/schemas.py/queries.py/app.py`；必要时只为模型请求配置补最小兼容参数，不重构整个 Provider。
- 默认复用 `creatoros.ai.DeepSeekProvider` 的结构化输出方式与现有环境配置，不硬编码 Key、不更换模型协议。Parser 按需初始化，未配置时目录/表单/确认仍可用。
- 同步 Parser 放同步 FastAPI handler 的线程执行；不可在 async handler 里直接阻塞 event loop；不共用生产 executor，解析期间 GET 与生产观察仍可用。
- 模型调用使用有界超时（建议 60 秒）和零隐式重试；只对本阶段解析请求生效，不意外改变 Agent/Codex 的超时。测试记录实际请求次数；异常文案脱敏。
- `create_app` 支持注入 parser/provider factory 便于故障测试；不在每个普通 GET 上初始化外部服务。`health` 加布尔 `operation_parser_configured`，只判断配置、不是宣称 Key 可用。

| 接口 | 本阶段约定 |
| --- | --- |
| POST /api/operations/propose（新增） | request_text 1–5000 字符、series_id 可空；JSON extra=forbid；成功持久化三态决策均返回 201 PendingOperationView |
| POST /api/operations/{id}/edit | 沿用 instruction、expected_version、expected_revision；不允许传新范围；成功 200，operation_id 不变 |
| POST /api/operations/preview | 保留无模型表单路径，同样产出可读 Preview；单栏目表单显式携带可选 series_id 用于记录范围 |
| GET /api/operations 与 /{id} | 返回真实 version、scope_series_id、可读 Preview；缺少单条记录 404；无模型调用 |
| POST /confirm、/cancel | 沿用接口并补真实版本校验；只对合法状态成功返回 200；冲突 409，前端不可无条件当成功 |

- 模型不可用/未配置/超时返回 503（稳定 error.code）；JSON/Schema 无效返回明确 502 parser_invalid_output；输入错误 422、资源不存在 404、版本/状态冲突 409。
- 沿用 usage_json 展示最新解析用量，并在已有 proposed/edited 事件 payload 记录该次 usage，避免修改计划后覆盖掉前一次成本；无 usage 保留 null，不另外建立 Trace 系统。
- needs_clarification/unsupported 是**已保存的业务决策**，不是 500；坏 JSON/上游失败不创建假的 ready 记录，编辑失败保留原计划。
- 若 confirm Service 返回 stale/failed 而非抛异常，HTTP 层必须映射冲突/执行错误或让 UI 明确分支处理；本阶段选择非 succeeded 不返回“写入成功”。失败后 GET 可查看已落库诊断。
- CLI、表单与 Web 自然语言共用 Service；不复制 apply-plan 逻辑、不注册 approve Tool。

### S6.3 前端共用抽屉和恢复入口

- 改动：`api/types.ts/client.ts/hooks.ts`、`Layout.tsx`、`SeriesPage.tsx`、`TodayPage.tsx`、`styles.css`；新建 `features/operations/` 下的小型 Drawer/Preview 组件即可。
- TypeScript 为 decision/status/Preview 做明确类型；OperationPlanInput 增加 reorder 的 discriminated union，不让所有计划变 any。
- 以 URL operation 参数 + TanStack Query 管已保存计划；少量 React state 管新草稿、编辑文本、用户已查看的版本。不要加 Redux 或另造持久化 store。
- 补 list/get/propose/edit/cancel client；mutation 显式 retry:false；查询允许重试但不能触发解析。保留表单添加、Run 操作、SSE。
- 顶部按钮、栏目入口、首页待办打开**同一个组件**；多个未确认计划可以逐个打开，不用“取最新一条”冒充全部。
- 计划列表按现有分页读；首页限制摘要数量可保留，但提供“更多待处理计划”，不能让重启前较早的计划永远不可达。
- 409 显示重新查看入口，保留修改文本；用户重新看到新 Preview 后才可确认。成功只刷新受影响栏目与待办，不自动开始生产。

### S6.4 验证、记录、交付

- 新增 `tests.smoke_studio_operation_workflow` 和 `tests.live_studio_operation_workflow`；前者确定性/故障/并发，后者真实模型 + HTTP + 临时库。
- 更新四个实现模块 SPEC、本文件状态与父 Studio SPEC；记录命令、请求数、真实 usage、尚未通过项。失败不能靠缩弱断言掩盖。
- 最终提交建议 `feat: add natural-language studio operation preview` 并 push。允许以上确认基础与最终功能分为两个可运行提交，不把半完成标 S6 完成。

## 验收与验证草案

### 自动化必须覆盖的结果

| 场景 | 判定依据 |
| --- | --- |
| scope 明确的新增 + 调序、后续自由编辑 | Preview 内容/顺序正确，id 相同，revision 增长，确认前 Topics 不变 |
| 关闭 DB 重开、HTTP GET 恢复 | 计划、范围、可读标题、版本保留；不再次解析 |
| 同名栏目/无范围指代/范围冲突 | needs_clarification，不能出现可确认越界 plan |
| 删除/发布/生产与支持动作混合 | unsupported，不部分加选题、不创建 Run |
| 模型故意输出外栏目 ID / 漏项调序 | 服务端拒绝；不能只靠 prompt 测一次“恰好没越界” |
| 停用/未知栏目与父账号 | 拒绝或澄清；显式非法 scope 在调用模型前拒绝 |
| 缺 Key、超时、坏 JSON | 可读错误，Topics 不变，已有计划不毁损；表单和查询仍可用 |
| 两个 edit 同版本、edit 期间 confirm/cancel | 精确同步的并发测试，一方获胜；晚到结果 409，不覆写新状态 |
| 同一确认重放 vs 旧/不同 token | 前者无重复写，后者 409；version 改版后依旧正确 |
| Preview 后队列变更、执行第二步失败 | 旧 token 拒绝；事务回滚，原队列保留，事件与终态正确 |
| 新旧数据库升级 | Alembic upgrade、旧记录可读、schema drift 检查；不重建正式库 |
| 关闭抽屉/页面刷新/普通 GET | 无确认、无生产调用；持久化计划可重新找到 |

- 故障注入、并发竞态、JSON 校验用确定性替身，原因是可重复制造故障而非替代真实模型验收；真实数据库用独立 TemporaryDirectory SQLite。
- 竞态用 Barrier/Event 控制时序，不靠随机 sleep；覆盖 `_mark_terminal` 晚到也不能覆盖成功记录。
- 真实验证默认 **4 次 DeepSeek**：①明确范围新增调序；②同一计划修改；③两个账号下同名栏目歧义；④越界动作。确认/重启/查询不花模型调用。
- 每次记录模型名、input/output/total usage（不可用填 null）、决策和是否落库；不打印 Key/Cookie/原始 SDK headers。失败保留 badcase，最多针对问题人工重试，不循环烧额度直到“通过”。
- 测试全程不调用 PersonClone、Codex、生图或发布，不往正式库 seed。真实模型不可用时如实标记待验证，不能宣称 S6 完整通过。

### 建议命令（新测试需先创建）

在 `D:\CreatorOS` 的 PowerShell 运行；Python 使用已有 deepcode，不重装 BGE-M3。

```powershell
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.smoke_operation_plan
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.smoke_operation_parser
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.smoke_pending_operation_storage
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.smoke_pending_operation_service
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.smoke_pending_operation_cli
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.smoke_studio_api
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.smoke_studio_operations
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.smoke_studio_operation_workflow
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.live_studio_operation_workflow
npm --prefix web run typecheck
npm --prefix web run build
git diff --check
```

共享 storage/app 改动后加跑 smoke_content_storage、smoke_content_run_service、smoke_studio_run_api、smoke_studio_artifacts、smoke_studio_events；不要因本轮是指令入口而漏掉 S4/S5 回归。compileall 只检查实际改动 Python 模块。

### 浏览器验收清单

- 隔离测试数据库启动真实 FastAPI 与 Vite；使用临时数据、独立输出与锁路径，不能改正式账号/选题/图片。
- 桌面 1440×900 与移动端 390px 看图：抽屉、长标题、多步 Preview、澄清和冲突无横向溢出；按钮/焦点/输入法行为正确。
- 完成示例流程，保存后刷新、首页重新打开、编辑后确认，队列与预览一致；等待模型时仍能浏览目录。
- 两个标签页打开同一计划，一边修改另一边确认旧版本，后者明确冲突、不可自动重试。
- 表单在未配置模型时仍可 Preview/确认；确认失败不能显示“已写入”。同时有失败 Run 与计划时，两者都能打开。
- 页面重开与 GET 不产生模型调用。UI 检查可以复用刚才真实模型生成的 PendingOperation，不重复解析只为截图。
- 工具可截图就亲自查看后修正；不要仅依据 DOM 存在便声称视觉验收通过。

## 完成门槛与下一步

- [ ] 真实范围校验、Parser 接线、三态持久化与同计划修改。
- [ ] 真正 version、并发保护、重复确认、失败回滚、旧数据兼容。
- [ ] 一个可恢复的共用抽屉，表单/自然语言入口共存，Preview 可读。
- [ ] 自动测试 + 4 次真实模型验证 + 桌面/移动浏览器检查，费用与异常如实记录。
- [ ] SPEC 更新，检查 staged/unstaged diff，仅提交本阶段文件，commit/push 成功。

S6 支撑简历里的“开放指令 → 结构化计划 → Preview/人工确认”，并提供状态和用量作为后续评估素材。它不是完整 Agent Eval、长期记忆或自主运营已完成的证据。完成后停下，下一步为 S7 联调交付，再回到 Agent 任务 Benchmark。

## 最近验证

- 2026-09-04（本计划）：只读核对源码、模型、路由、测试与 Git；发现并列明 Web parser 未接线、version/revision 不一致、Preview 不可恢复展示等缺口。
- 本轮仅新增/更新计划文档，不执行 S6、不调用真实 API、不跑生图；已核对所引用的既有文件与 Python 路径，`git diff --check` 通过。实施验收栏保持未勾选。

## 给 Luna 的启动指令

> 阅读 AGENTS.md、docs/studio/SPEC.md 和 docs/studio/s6/SPEC.md，按 S6 契约实施，本轮只完成 S6。先核对 Git 和相关模块 SPEC，优先修正文档标出的解析接线及确认版本缺口；复用现有 Parser、PendingOperationService 和前端，不重写 Runtime。按计划完成真实 DeepSeek + 隔离数据库 + 浏览器验收，更新 SPEC，commit 并 push。不要实施 S7，不启动生图或发布，不写演示数据进正式库；遇到确实阻塞报告具体证据，不重新询问已确定的方向。
