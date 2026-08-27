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
