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
