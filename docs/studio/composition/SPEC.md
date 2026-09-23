# 创作空间：Web / Agent 共用业务能力

状态：设计已确认，待实施。2026-09-21。

## 用户确认

- Web 手工组合和 Agent 自然语言操作使用同一接口、同一数据库和同一后台任务。
- 栏目组合内容 Skill（mind）与制作 Skill（production）；制作不限于视觉，但当前执行/验收器只支持图片轮播。不把 PageSpec 强加给所有内容 Skill。
- 栏目可先未分配，再归属一个账号；真正生产前必须有账号。
- A：用户明确要求的动作可执行，无须重复人工 Preview；查看、建议、讨论不写入。歧义先澄清。删除、覆盖既有产物、发布单独确认，当前不开放发布。
- 双 Skill 默认绑定只影响未来任务。开始时冻结输入，旧任务与旧产物不被修改。
- 拖动动画、点击整框取消组合后续优化，本阶段不换视觉设计。

## 已核实基线

- `creatoros/tools/studio.py` 已通过 StudioClient 调用与 Web 相同的调研、选题和 ContentRun 接口；不新增第二套执行器。
- `Series.creator_id` 当前非空；`skill_name` 当前必填单 Skill。`StudioWriteService.create_series` 固定写入 knowledge-to-carousel。
- `ProducerSkillCatalog.resolve` 当前拒绝 carousel_compatible=false。内容 Skill / 制作 Skill 的安装与可单独生产必须解耦，否则内容 Skill 无法进入可组合目录。
- `prepare_topic_selection` 目前只返回 Preview 并要求人工确认。A 尚未接线，不能通过只改 prompt 宣称完成。
- `/studio-preview` 为 localStorage 示例，不得把示例静默导入正式数据库。
- Web 会话已有 context-trace，生产已有 Run/Attempt/Revision；这些不同轨迹需要关联，不应混为同一个 Trace。

## 目标链路

```text
Web 表单/拖拽 ──→ HTTP API ──→ 共用业务服务 ──→ 数据库/后台任务
Agent → Tool ──→ 同一 HTTP API ──────┘
                                      ↓
                                  Codex 生产
                                      ↓
                          Run / 中间产物 / 最终产物
```

Web 明确操作不需要再调用运营 LLM。Agent 负责解析、查找、选工具、观察结果并决定下一步。Codex 是生产执行者；提交返回任务 ID 后允许继续聊天，页面观察同一任务，不为展示进度新启动推理。

## P1 数据与安装契约

1. 安装目录区分 Skill 的可用性、角色和产物能力。文件校验与版本 digest 复用现实现；不能因为不独立生成轮播就拒绝安装。
2. 角色使用 mind / production / legacy_end_to_end；未分类允许展示，但不能静默当作已验证组合。角色由明确安装/配置请求声明，不能仅靠名称推断。
3. 目录保留稳定 ID、名字、描述、来源、版本；文件路径和秘密不发送浏览器。旧注册记录缺角色时映射 legacy_end_to_end，不修改其历史文件或 digest。
4. 为 Series 增加可空 mind_skill_id / production_skill_id 与 revision；creator_id 改为可空。保留旧 skill_name 兼容旧栏目；旧单 Skill 栏目不伪造内容/制作组合。
5. 一次请求只能是 legacy 或完整 pair，禁止只有半套绑定。组合可以保存为未验证；真正生产时必须完成当前产物契约检查，不把保存成功等同于可生产。
6. Alembic 迁移先在带 Creator/Series/Topic/Run 的隔离旧库执行，验证外键、行数、快照不变；检查 SQLite nullable 唯一约束，明确处理未分配同名栏目。
7. 未分配栏目在共用列表中可查，旧按账号列举入口兼容；查询、调研、生产必须逐一处理空 creator，不靠捕获 AttributeError。

P1 验收：空库和旧库升级；旧栏目可查；pair 完整性；未知 Skill 拒绝；未分配可读但不可生产；旧 Run 输入 JSON 不变。此阶段不迁移正式数据。

## P2 共用业务操作与 Agent Tools

复用现有创建、安装、调研、队列、Run 服务；服务必须是唯一写入点，Tool 不直接写 ORM 或注册文件。

| 操作 | Web | Agent |
| --- | --- | --- |
| 查询账号/栏目/Skill | 页面加载、筛选 | 复用/扩展目录 Tool |
| 添加 Skill | 链接、角色 → 后台安装任务 | 相同安装参数及 job ID |
| 创建栏目 | 名称、定位、受众、pair、可选账号 | 同一结构化请求 |
| 修改组合/分配账号 | 提交用户所见 revision | 相同 expected_revision |
| 调研选题 | 栏目页提交任务 | research_series_topics |
| 加入队列 | 勾选/编辑后明确提交 | 明确入队指令生成相同选择请求 |
| 生产 | 选题页开始 | start_content_run |
| 查看进度 | 运行页观察 | get_content_run |

- 创建/写请求带 request_id，同一请求重复提交不生成两份记录；未知网络结果先查询，不自动换 ID 重发。
- 配置修改使用所见 revision，冲突返回409，不静默刷新后再写。
- 所有入口返回真实资源/任务 ID、状态、详情链接；工具失败不返回成功文案。
- CLI 注册、Web allowed_tools、工具中文展示名一起更新，避免“注册了但宿主看不到”。

### A 的执行边界

- 保留 Preview 作为查看影响和消歧能力，但不把人工点击作为所有明确指令的前置条件。
- 新的显式执行操作仍在服务端验证对象、版本、来源、状态并记录事件；复用既有事务/幂等机制，不绕过校验直接写队列。
- 不给模型任意旧 operation_id 的无条件确认权限。Agent 直接执行必须关联当前明确请求和这次结构化动作；历史 Preview 不因一句“继续”自动获得更宽授权。
- “看看十个选题”只调研；“前两条入队”只入队；“前两条入队并生产”才允许后续生产。指令缺对象/数量且无法唯一解析时澄清。
- 请求上下文/来源由宿主提供，不把模型输出的 authorized=true 当成用户授权证明。确定性约束由服务校验；自然语言是否明确通过专项 eval 验证，不能声称完全形式化保证。
- 删除、覆盖、发布不新增通用写文件/代码执行捷径；本阶段没有自动发布。

P2 验收：同一对象分别经 HTTP 和 Tool 操作后读取一致；双击/重复 request_id 幂等；旧 revision 拒绝且零写；Web/CLI schema 都可见；查看/建议不写。真实 DeepSeek 只在隔离库测试低成本管理操作，不启动付费生产。

## P3 真实创作空间

- 保留 `/studio-preview` 示例入口；正式创作空间使用服务数据，不混用 localStorage。
- 空库显示真实空态和创建入口，不用示例填充。加载/失败/保存冲突分别展示；保存未成功不能显示已完成。
- 左侧真实 Skill 库；中间组合与未分配栏目；右侧真实账号及其栏目。旧单 Skill 栏目清楚标明，不伪造第二个 Skill。
- 组合创建先填写名称/定位/受众，保存才形成栏目；拖入账号提交归属变更。支持点击与下拉等价操作，失败恢复原位置。
- 栏目打开既有真实选题库，保留调研候选和正式队列状态区别；页面手动选择与 Agent 选择写入相同记录，保留切入点/来源。
- 提交后台调研/生产后显示任务链接，可离开、重进、刷新；不隐式重提任务。
- Agent 和运行入口继续复用既有页面。Trace/Eval 完整新面板不阻塞首次真实组合闭环。

P3 验收：真实隔离 HTTP/SQLite；创建/组合/归属/撤回/选题查询/刷新；API失败、409、重复点击；1440×900与390×844；浏览器与Agent跨入口核对同一ID。遵循 web/FRONTEND_WORKFLOW.md，不仅截图。

## P4 双 Skill 生产与溯源

- Run 输入冻结账号、栏目定位、受众、topic及来源、两个Skill ID/版本/digest。本次临时覆盖不反写栏目。
- 一次 Codex 委托依序读取内容 Skill 与制作 Skill，输出可追溯中间内容、逐页最终 prompt、引用资产与最终包；不另起两个运营 Agent。
- 兼容现有 legacy producer；pair 路径显式分支，不能把两个 Skill 拼成一个 skill_name。
- 文件验收检查真实存在及页/图/prompt对应关系。拿不到模型内部最终 prompt 时标记缺失，不能事后编造。
- 已有 SocialContentPack 为轮播契约；新文本/视频制作 Skill 可入目录，但执行适配与验收器未实现前明确不可生产。不声称已支持任意媒体。
- 失败恢复复用 ContentRun；未知外部执行结果先查原任务。保留旧 revision，不覆盖已验收产物。

P4 先用受控生产器测试快照/中断/重复提交/缺工件，明确不是内容质量验证；真实 Codex 生图单独按用户授权测试，不在管理链路测试中自动触发。

## P5 Trace / Eval 展示

- 关联入口来源（web/agent）、会话/调用、operation、research batch、run、attempt、revision；Web操作没有LLM调用就不伪造 trajectory。
- 运行页先显示真实状态、产物及工具事件；内部 Codex 轨迹不可得就标注覆盖边界。
- Eval 面板展示隔离评测任务与已保存报告：最终状态成功率、越权写入、工具路径、Token；不把生产日志条数当评测成绩。
- 最小对照场景：查看不写；明确创建；只入队不生产；跨入口读写一致；配置冲突；未知结果查询；恢复不重复生产。失败用例进入回归。

## 实施记录

### P3 决策（2026-09-23，用户确认）

1. 创作空间作为新导航项（"创作"），不替换现有页面。
2. pair 栏目生产入口：灰色禁用+说明"双 Skill 生产 P4 接入"；调研可用（mind Skill 上下文）。用户指出：双 Skill 组合会有接口适配问题（mind 输出的 PageSpec 与 production 的输入约定要对上，可能需要显式契约或额外的对接工具/Agent）——记入 P4 风险：P4 设计时先定义两 Skill 之间的中间契约与失败反馈，不假设天然兼容。
3. 视觉方向先行：P3-S1 出骨架+真实数据截图，用户确认后再扩散全站。理由（用户原话）：太丑的页面没有兴趣自己迭代测试，就发现不了更多工程问题。

### P2 共用服务与 Tools（2026-09-23，完成）

- **幂等锚点**：迁移 `20260923_0006` 新增 `write_receipts`（request_id 主键）；只记录成功写入，失败不留回执可用同 ID 安全重试；同一 request_id 跨操作复用返回 409。
- **组合写服务** `SeriesCompositionService`（唯一写入点）：创建栏目（legacy 或完整 pair，账号可选）、改组合（仅 pair 栏目，legacy 不转换）、分配/撤回账号；全部 expected_revision CAS + request_id 幂等；pair 槽位校验角色（mind/production）且内置端到端 Skill 不能进槽位；组合可保存为未验证，生产门禁仍在 Run 创建时。
- **A 策略直入队**：`PendingOperationService.execute_direct` 一次事务完成 校验→PendingOperation(proposed/confirmed/succeeded)→写入→write_receipt；确认凭证生成即消费，模型接触不到可复用 token；历史 Preview 不因"继续"获得授权。选题操作对未分配栏目放行（P1 执行器过严已修正为"只有生产要求账号"）。
- **HTTP**：`POST /api/series`、`/api/series/{id}/composition`、`/api/series/{id}/assignment`、`/api/series/{id}/queue`、`/api/topic-research/{batch}/queue`（调研候选走确定性 topic_id + 服务端来源拼接，不经客户端）。origin 由宿主 `x-creatoros-origin` 头提供，不信客户端 body。
- **Agent 工具**：compose_series / update_series_composition / assign_series / queue_topics 注册进 tool_registry + Web STUDIO_TOOLS + 中文展示名；install_producer_skill 支持 role；宿主指令写入 A 边界。
- **Web**：候选选择栏主按钮改为"确认入队"直接写入（request_id 每次操作生成，重试安全），"预览"降级为可选次级按钮；自然语言抽屉仍走 Preview。
- **验证**（全部隔离环境）：
  - `series_composition_service_smoke=passed`：创建/幂等重放/跨操作 request_id 复用 409/半套与全空 422/角色错槽/未装 Skill/未知账号 404/分配与撤回/revision CAS 冲突 409 零写/legacy 拒转/agent origin 记录/直入队。
  - `studio_composition_tools_smoke=passed`：真实本地 HTTP 服务，Agent 工具写入 + HTTP 读取跨入口一致；旧 revision 拒绝；四个新工具已暴露。
  - `live_composition_skills=passed`：真实 GitHub 安装 mind=`knowledge-to-storyboard-deep--039c5af915d13784`、production=`xiaobai--901ca2275bec0b70`（均不声明轮播契约 → 可组合、当前不可生产），组成 pair 栏目；未分配先拦"尚未分配账号"，分配后拦"双 Skill 尚未接入"（P4）。
  - `live_a_boundary_eval`（真实 DeepSeek，6 题）：A1 明确入队、A2 查看不写、A3 歧义澄清、A4 删除拒绝、A5 历史 Preview 不授权均通过；**A6 初跑失败**——空库+明确标题下模型过度澄清未执行，宿主指令补"明确标题直接新建入队"后复跑通过（queue→生产链路，受控 Producer），按惯例不宣称单次通过即稳定消除。
  - Playwright e2e 4 passed（topic-research 用例更新为 Preview 零写入 + 直接入队断言）；既有 smoke 回归与 compileall/typecheck/build 全绿。
- 已知语义：同值重写不推进 revision（ORM 无变化不触发 version_id_col）；系列同名冲突与并发同 request_id 竞态由 IntegrityError 归一为 409；正式库由启动迁移升至 0006（只加表，无数据变更）。

### P2 决策（2026-09-23，用户逐条确认）

1. **Web 显性结构化操作跳过强制 Preview**：勾选候选+点确认本身就是明确指令，直接写入；Preview 降级为"查看影响"的可选能力。自然语言入口（Ctrl+K 抽屉）仍走 Preview，因为模型解析可能出错，Preview 是消歧面。
2. **不允许空白草稿栏目**：创建栏目必须当场选定 legacy 单 Skill 或完整 pair；P1 的 `skill_binding_shape` 约束保持不变。
3. **pair 验证使用真实 Skill**：mind = `SlamWeb/knowledge-to-storyboard`（skills/knowledge-to-storyboard-deep），production = `SlamWeb/creatorOS-ip-skills`（xiaobai）。注意 xiaobai 只交付生图 Prompt、不声明轮播契约，该 pair 在 P2 为"可保存、未验证、不可生产"，生产链路属 P4。
4. **legacy → pair 转换不做**：旧栏目永远 legacy，组合只能新建。
5. **范围**：P2 不做组合 UI（P3）；A 边界专项 eval 做最小集（5–8 题）；撤回分配不限制（进行中的 Run 有输入快照不受影响，未分配后新生产被守卫拒绝）；request_id 由宿主生成（Web 每次动作一个 uuid，Agent 由 Runtime 按调用生成），服务端按 request_id 幂等。
6. **生产恢复已有 checkpoint 机制**（用户提问确认）：ContentRun/Revision/Attempt 分离 + `producer_thread_id` 在 thread.started 时持久化 + lease/heartbeat + 重启后显式恢复；P4 双 Skill 生产复用同一 ContentRun 机器，不另造恢复逻辑。

### P1 数据与安装契约（2026-09-23，完成）

- 迁移 `20260923_0005`：`series.creator_id` 与 `skill_name` 可空；新增 `mind_skill_id`、`production_skill_id`、`revision`（ORM version_id_col 乐观并发）；`skill_binding_shape` CHECK 强制"旧单 Skill XOR 完整 pair"，禁止半套绑定；未分配栏目同名由部分唯一索引 `uq_series_unassigned_name`（`creator_id IS NULL`）兜底，已分配栏目沿用 `(creator_id, name)` 唯一约束。降级会明确删除未分配/组合栏目，不留脏数据。
- `ProducerSkillCatalog`：角色（mind/production/legacy_end_to_end）与产物能力（carousel_compatible）分离；`list()` 新增 `role` 与 `producible`；旧注册记录缺 `role` 键时读取映射为 `legacy_end_to_end`，不回写历史文件；新增 `locate()` 只做完整性校验（供调研等只读上下文），`resolve()` 保持生产门禁（mind、未分类、非轮播契约分别给出明确错误）。安装请求可显式声明 `role`（API/服务/注册贯通），省略即未分类——可展示不可生产；同一 URL 的既有任务保留首次声明的角色。
- 读路径：`SeriesView.creator_id/skill_name` 可空并新增 `mind_skill_id/production_skill_id/revision`；前端 types 同步，栏目页对未分配账号与双 Skill 组合做空值展示，不伪造单 Skill。
- 显式守卫：未分配栏目生产报"尚未分配账号"、双 Skill 栏目生产报"尚未接入"（ContentRunError，非 AttributeError）；调研允许未分配旧栏目（候选只是建议），组合栏目用 mind Skill 的 `locate` 做上下文；`validate_scope` 允许未分配栏目作计划范围，执行器明确拒绝并说明原因。
- 旧 Run 输入快照不变：`ContentRunInput` 字段集合未动，smoke 逐键比对通过。
- 验证（全部隔离临时库，不触碰正式 `data/creatoros.db`）：
  - `smoke_series_composition=passed`：0004 旧库（含 Creator/Series/Topic/ContentRun 与真实快照 JSON）升级后逐字段不变、新列 NULL/revision=1、`compare_metadata` 零漂移、降级再升级数据一致；半 pair、pair+skill_name、revision=0、未分配同名、已分配同名均被约束拒绝。
  - `smoke_skill_roles=passed`：内置/新装/未分类/旧记录映射、resolve 门禁三类拒绝、locate 只读、角色白名单、未知 ID。
  - `smoke_series_guards=passed`：未分配与双 Skill 的生产拒绝、调研双分支、API 空值投影、执行器守卫。
  - 回归：`smoke_content_storage`、`smoke_content_run_storage`（head 断言更新为 0005）、`smoke_producer_skills`（fixture 按新契约声明 role=production，bind/CAS/篡改流程不变）、`smoke_operation_*`、`smoke_pending_operation_*`、`smoke_studio_*`（8 个）、`smoke_topic_research`、`smoke_agent_studio`、`smoke_web_agent` 全部通过；`compileall` 与前端 `typecheck`/`build` 通过。
  - 正式库副本彩排：复制 `data/creatoros.db` 到临时文件升级 0005，真实"Agent"栏目 creator_id/skill_name 保留、ORM 读取与 creator 关系正常；原库保持 0004 未动。正式库将在用户下次启动 Web/CLI 时由既有启动迁移自动升级（应用自有行为，非本轮测试副作用）。
- 明确未做：未迁移正式数据；未实现 pair 栏目的创建/绑定 API（P2）；未实现双 Skill 生产（P4）；`prepare_topic_selection` 仍要求人工确认（A 未接线）；前端仅做类型兼容，未做组合 UI（P3）。
- 已知边界：SQLite 部分唯一索引依赖 SQLite ≥3.8；`bind_skill` 仍按 `expected_skill_name` CAS（revision CAS 留给 P2 的组合写接口）；调研对组合栏目只读 mind Skill，不读 production Skill。

### 设计核对（2026-09-21）

- 本轮仅完成代码核对与本 SPEC，未实现 P1–P5，未修改生产配置、Skill安装目录或正式数据库。
