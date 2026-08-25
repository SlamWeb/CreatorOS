# Creator Routing Models SPEC

## 本轮目标

- 将 PersonClone `GET /api/personas/{author}/routing-profile` 的真实 JSON 响应解析为严格的 Pydantic 模型。
- 保留 domain prototype、perspective prototype、evidence 和不透明 vector_ref 的边界；不读取 PersonClone 本地文件或 Qdrant。
- 只实现数据合同和 ready/domain_ready/perspective_pending 的能力判断，不实现向量索引、召回或 LLM 重排。

## 当前假设

- `profile.status` 是业务状态，`envelope.status` 是接口包装状态，两者不能混用。
- evidence 的 `field`、`claim_id`、`excerpt` 在 domain prototype 中可能为 null。
- `corpus_version` 是后续 CreatorOS 路由缓存和索引刷新的版本键。

## 验收

- Mock smoke 能验证真实字段、可空 evidence 字段、严格额外字段拒绝和能力属性。
- 真实 PersonClone GET 能直接返回 `RoutingProfileEnvelope`，不再让调用方手动解析嵌套 dict。

## 最近验证

- `routing_models_smoke=passed`：ready/domain_ready 能力判断、可空 evidence 和额外字段拒绝通过。
- `personclone_smoke=passed`：HTTP Client mock 返回 `RoutingProfileEnvelope`，Cookie、路径和既有 PersonClone 行为未改变。
- `real_routing_models=passed`：真实登录态下 7 位作者全部解析成功，均为 ready，合计 83 个 domain 与 37 个 perspective prototypes。
