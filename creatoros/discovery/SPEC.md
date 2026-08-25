# Trend Discovery SPEC

Progressive SPEC, not a form.

## 当前理解

- CreatorOS 已有可用的 Provider、Tool Registry、RuntimeContext、Session、Streaming、Compaction 和 PersonClone Service 接入，足够承载第一版运营工作流。
- 热点发现的第一层是稳定获取结构化候选数据；是否值得追、适合哪个作者属于后续评分与路由层。
- 知乎官方开放平台提供 `GET /api/v1/content/hot_list`，使用 Bearer Access Secret 和秒级 `X-Request-Timestamp` 鉴权。
- 官方 `GET /api/v1/content/zhihu_search` 可以用关键词补充问题、回答、文章、作者、互动量和原文链接。

## 本轮目标

- 保留已经完成的 `get_zhihu_hot_list(limit)`，新增最小 `search_zhihu(query, count)` Tool。
- 搜索只投影选题需要的内容、作者、互动量、权威等级、排序分数和原文链接，不把完整官方响应直接塞进模型上下文。
- 不加入 LLM 评分、Creator Routing、定时任务、缓存、数据库或发布能力。

## 当前假设

- 官方接口参数使用 `Limit`，第一版限制为 1～30 条。
- 搜索接口使用 `Query/Count`，query 限制 1～100 字符，count 限制 1～10 条。
- Access Secret 只从 `ZHIHU_ACCESS_SECRET` 读取，不进入代码、日志、ToolResult 或 Git。
- 当前本机没有该凭证；允许用 `MockTransport` 验证纯适配逻辑，但真实成功链路必须在配置凭证后补验收。

## 对外影响

- Agent Registry 现在暴露 `get_zhihu_hot_list(limit)` 和 `search_zhihu(query, count)` 两个官方只读 Tool。
- 只进行外部只读 GET 请求，不修改知乎、PersonClone、Session 或本地业务数据。

暂未确认：

- 官方试用额度、调用频率和生产限流策略。
- 热榜结果是否足以支撑选题；需观察真实返回后再设计 TopicCandidate 和评分字段。

## 验收与验证草案

- 请求包含 Bearer 鉴权、秒级时间戳和 `Limit`，但测试输出不包含 Secret。
- 搜索请求包含 `Query/Count`，并把 `Data.Items` 映射为内部不可变领域对象。
- 官方 `Code == 0` 时解析 `Data.Total/Items`；鉴权、协议、超时和网络错误转换成稳定错误类型。
- Agent Tool schema 限制 `limit` 为 1～30，结果只暴露业务需要的公开字段。

优先运行：

```powershell
conda run --no-capture-output -n deepcode python -m tests.smoke_zhihu_hot_list
conda run --no-capture-output -n deepcode python -m tests.smoke_zhihu_search
conda run --no-capture-output -n deepcode python -m compileall -q main.py creatoros
git diff --check
```

## 最近验证

- 日期：2026-08-25
- 真实边界探测：未带官方 Access Secret 请求 `/api/v1/content/hot_list`，HTTP 200 内返回 `Code=20001, Message=Authorization failed`。
- 公开网页端点探测：终端直接访问热榜 API/页面分别返回 401/403，不能作为稳定的无凭证后端。
- 结论：第一版坚持官方 OpenAPI；配置凭证前不伪造“真实热榜成功”。
- `zhihu_hot_list_smoke=passed`：验证官方字段映射、Bearer/时间戳/Limit 请求、ToolResult、Pydantic 1～30 限制、缺少凭证和官方 `Code=20001` 错误转换。
- `zhihu_search_smoke=passed`：验证 Query/Count、搜索字段映射、ToolResult、Pydantic query/count 限制和空查询拒绝。
- `model_context_smoke=passed`、`agent_events_smoke=passed`：新增 Tool 没有破坏模型上下文投影或 Agent 事件闭环。
- `python -m compileall -q main.py creatoros tests/smoke_zhihu_hot_list.py tests/smoke_zhihu_search.py` 通过。
- 真实搜索端点探测：未带 Access Secret 请求 `/api/v1/content/zhihu_search`，HTTP 200 内返回 `Code=20001, Message=Authorization failed`，与客户端错误映射一致。
- 尚缺真实成功结果：本机仍没有 `ZHIHU_ACCESS_SECRET`；配置后应立即各运行一次低成本热榜和搜索验收。
