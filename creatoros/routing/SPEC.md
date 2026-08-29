# Creator Routing Models SPEC

## 本轮目标

- 将 PersonClone `GET /api/personas/{author}/routing-profile` 的真实 JSON 响应解析为严格的 Pydantic 模型。
- 保留 domain prototype、perspective prototype、evidence 和不透明 vector_ref 的边界；不读取 PersonClone 本地文件或 Qdrant。
- 将两类 prototype 投影为 CreatorOS 自己的 `RoutePrototypeDoc`，并通过本地离线 BGE-M3 生成归一化向量；本轮不建立向量索引，不做召回或 LLM 重排。

## 当前假设

- `profile.status` 是业务状态，`envelope.status` 是接口包装状态，两者不能混用。
- evidence 的 `field`、`claim_id`、`excerpt` 在 domain prototype 中可能为 null。
- `corpus_version` 是后续 CreatorOS 路由缓存和索引刷新的版本键。
- domain 和 perspective 必须分别保留 prototype_type；不能把两类文本混成一个作者向量。
- embedding-ready 文本只来自 PersonClone API 暴露的画像字段和代表性证据，不读取原始语料。
- BGE-M3 权重必须从本机 Hugging Face cache 加载，Provider 使用 `local_files_only=True`，禁止运行时联网下载。
- 作者原型向量缓存必须记录 `corpus_version`、模型、维度和 embedding 文本指纹；画像更新或文本变化时自动失效并覆盖旧条目。

## 验收

- Mock smoke 能验证真实字段、可空 evidence 字段、严格额外字段拒绝和能力属性。
- 真实 PersonClone GET 能直接返回 `RoutingProfileEnvelope`，不再让调用方手动解析嵌套 dict。

## 最近验证

- `routing_models_smoke=passed`：ready/domain_ready 能力判断、可空 evidence 和额外字段拒绝通过。
- `personclone_smoke=passed`：HTTP Client mock 返回 `RoutingProfileEnvelope`，Cookie、路径和既有 PersonClone 行为未改变。
- `real_routing_models=passed`：真实登录态下 7 位作者全部解析成功，均为 ready，合计 83 个 domain 与 37 个 perspective prototypes。
- `real_routing_projection=passed`：真实登录态下 7 位作者投影出 120 个 `RoutePrototypeDoc`，domain/perspective 两类均存在，corpus_version 与画像一致；未调用 embedding 或 Qdrant。
- `live_routing_embedding=passed`：真实 7 位作者画像投影出的 120 个文档通过本地缓存 BGE-M3 生成 1024 维归一化向量；未下载模型、未连接 Qdrant。

## 本轮目标（作者原型向量缓存）

- 将 domain prototype 的 embedding 向量保存到 Git 忽略的本地缓存，避免每次读取热点都重复编码不变的作者画像。
- 缓存键保留作者、原型、画像版本、模型、维度和文本指纹；PersonClone rebuild 后 `corpus_version` 或文本变化即可触发重新计算并覆盖。
- 热点 query embedding 暂不持久化，因为热榜内容按请求变化；本轮只优化稳定的作者侧向量。

## 本轮验证（作者原型向量缓存）

- `routing_embedding_cache_smoke=passed`：冷启动 miss、跨实例读取、corpus_version 变化、文本变化、覆盖写入和损坏缓存回退均通过。
- `route_hotspots_tool_smoke=passed`：路由 Tool 记录 prototype cache hit/miss，测试使用临时缓存，不污染真实缓存。
- 真实缓存文件位于 `D:\CreatorOS\tmp\routing_embedding_cache.json`，已被 `.gitignore` 忽略，不提交 Git。

## 与 Planning 的边界

- `rank_domain_matches` 仍只负责单条热点的作者级候选召回。
- `creatoros.planning` 负责把多条热点的分数矩阵反转为作者侧队列，不在 routing 层引入发布策略或日计划状态。

## 本轮目标（domain-only 查询与排序）

- 增加 `build_domain_query(HotTopic)`，只保留热点标题和有上限的问题介绍，不让 LLM 改写领域语义。
- 增加 `BGEEmbeddingProvider.embed_texts/embed_text`，让热点查询和作者画像共享同一 embedding 模型。
- 增加 `rank_domain_matches`，只使用 domain prototypes，并以每个作者内部的最大 cosine similarity 作为作者分数。
- 暂不使用 perspective prototypes、回答证据、Qdrant 索引、阈值调参或最终 LLM 重排。

## 本轮验证（domain-only 查询与排序）

- `domain_routing_smoke=passed`：查询拼接、空摘要、作者内 Max Similarity、类型过滤、top-k 和维度校验均通过。
- `live_domain_routing=passed`：真实热榜查询与 83 个 domain prototypes 完成 5 条热点的 Top-3 排名；BGE-M3 由本机缓存离线加载。
- `live_routing_embedding=passed`：既有 120 个画像原型的 embedding 回归通过。
