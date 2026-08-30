# CreatorOS Planning SPEC

## 本轮目标（作者侧内容队列）

- 将 domain-only 路由结果反转为“每个作者有哪些热点候选”。
- 用不可变 `ContentOpportunity` 表示一张候选卡片，用 `DailyPlan` 表示一个作者的日计划。
- 当前只填充 `hot` 队列；`evergreen` 与 `experiment` 预留给后续业务切片。

## 当前边界

- 复用 `creatoros.routing.rank_domain_matches`，不重新实现相似度计算。
- 每个作者保留自己的 Top-N，允许同一热点同时出现在多个作者队列。
- 不在本轮调用 PersonClone 生成、质量评审、发布或持久化计划。

## 验收

- 本地 smoke 验证作者侧排序、Top-N、空队列和输入校验。
- 真实验证使用知乎热榜、PersonClone 路由画像和本地缓存 BGE-M3，输出每位作者的热点候选队列。

## 本轮目标（SelectionPlan）

- 将用户对作者候选的不同选择说法收敛为严格的 Pydantic `SelectionPlan`，供后续 LLM 意图解析和确定性展开共用。
- 一份计划可包含多个 `SelectionGroup`，从而表达不同作者选择不同候选；候选只允许按作者队列位置、全局热榜名次、Top-N 或全部四种方式之一寻址。
- `execution_mode` 区分仅预览、用户已确认和明确授权自动执行；当前只定义 `answer` 动作。

## 当前边界（SelectionPlan）

- 本轮只定义和验证数据契约，不调用 LLM、不展开候选、不调用 `ask_author`。
- `route_snapshot_id` 先作为可选关联字段；候选快照持久化和强制绑定留到执行器切片。
- 不把每种自然语言说法写成独立工作流；后续由 LLM 解析为同一模型，再由普通 Python 规则执行。

## 验收（SelectionPlan）

- 本地 smoke 覆盖单作者单候选、多作者同热点、全体作者 Top-N、不同作者不同位置，以及空选择、混用寻址参数和额外字段拒绝。
- `selection_plan_smoke=passed`、`content_planning_smoke=passed`，全包 `compileall` 通过；本轮是纯本地结构化数据契约，没有调用或伪造外部 API。

## 本轮目标（SelectionPlan 展开器）

- 将 `SelectionPlan` 与同一次 `route_hotspots` 结果确定性展开为不可变的 `SelectionAssignment`，每项对应一个待执行的“作者 + 候选热点”。
- 显式作者保持用户顺序；`all` 保持路由结果中的作者顺序；位置和热榜名次保持用户指定顺序，Top-N/全部保持队列顺序。
- 多个选择组命中同一作者、同一队列、同一热点时只保留一次，避免重复调用 `ask_author`。

## 当前边界（SelectionPlan 展开器）

- 本轮不调用 `ask_author`，不实现并发生成；先让选择展开成为可独立评测的确定性层。
- 未知作者、未知候选、空队列和畸形路由结果直接失败，不静默生成部分任务。
- `route_snapshot_id` 尚未与持久化快照强绑定，后续执行器接线时处理。

## 验收（SelectionPlan 展开器）

- 本地 smoke 覆盖队列位置、全局热榜名次、全体作者 Top-N、排除作者、跨组去重和主要失败分支。
- 真实验证调用知乎热榜与 PersonClone 路由画像，使用本地缓存 BGE-M3 产生队列，再展开为每位作者一个 Top-1 任务；不触发内容生成或发布。
- `selection_expansion_smoke=passed`，并回归通过 SelectionPlan、内容队列、route_hotspots Tool 和 route-and-answer Skill smoke。
- `live_selection_expansion=passed assignments=7`：在 `deepcode` 环境复用已有 BGE-M3，真实读取 5 条知乎热榜和 7 位 PersonClone 作者画像，为每位作者展开一个 Top-1 任务；没有下载模型、调用内容生成或发布。
- 当前系统 Python 缺少本地 embedding 运行依赖，真实测试必须使用现有 `deepcode` 环境；这不是 PersonClone 服务故障。
