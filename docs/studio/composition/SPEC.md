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

- 本轮仅完成代码核对与本 SPEC，未实现 P1–P5，未修改生产配置、Skill安装目录或正式数据库。
- 实施次序 P1 → P2 → P3 → P4；P5 可在事件关联完成后追加。每步更新验收记录并独立提交，不把设计文档当作完成证明。
