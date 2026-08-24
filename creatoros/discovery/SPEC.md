# Trend Discovery SPEC

Progressive SPEC, not a form.

## 当前理解

- CreatorOS 已有可用的 Provider、Tool Registry、RuntimeContext、Session、Streaming、Compaction 和 PersonClone Service 接入，足够承载第一版运营工作流。
- 热点发现的第一层是稳定获取结构化候选数据；是否值得追、适合哪个作者属于后续评分与路由层。
- 知乎官方开放平台提供 `GET /api/v1/content/hot_list`，使用 Bearer Access Secret 和秒级 `X-Request-Timestamp` 鉴权。

## 本轮目标

- 迈一个 small step：把知乎官方热榜封装成薄 HTTP Client 和只读 Agent Tool，返回标题、链接、摘要和缩略图。
- 不加入 LLM 评分、Creator Routing、定时任务、缓存、数据库或发布能力。

## 当前假设

- 官方接口参数使用 `Limit`，第一版限制为 1～30 条。
- Access Secret 只从 `ZHIHU_ACCESS_SECRET` 读取，不进入代码、日志、ToolResult 或 Git。
- 当前本机没有该凭证；允许用 `MockTransport` 验证纯适配逻辑，但真实成功链路必须在配置凭证后补验收。

## 对外影响

- 新增 `get_zhihu_hot_list(limit)` Tool，并增加知乎开放平台相关环境变量配置。
- 只进行外部只读 GET 请求，不修改知乎、PersonClone、Session 或本地业务数据。

暂未确认：

- 官方试用额度、调用频率和生产限流策略。
- 热榜结果是否足以支撑选题；需观察真实返回后再设计 TopicCandidate 和评分字段。

## 验收与验证草案

- 请求包含 Bearer 鉴权、秒级时间戳和 `Limit`，但测试输出不包含 Secret。
- 官方 `Code == 0` 时解析 `Data.Total/Items`；鉴权、协议、超时和网络错误转换成稳定错误类型。
- Agent Tool schema 限制 `limit` 为 1～30，结果只暴露业务需要的公开字段。

优先运行：

```powershell
conda run --no-capture-output -n deepcode python tests/smoke_zhihu_hot_list.py
conda run --no-capture-output -n deepcode python -m compileall -q main.py creatoros
git diff --check
```

## 最近验证

- 日期：2026-08-25
- 真实边界探测：未带官方 Access Secret 请求 `/api/v1/content/hot_list`，HTTP 200 内返回 `Code=20001, Message=Authorization failed`。
- 公开网页端点探测：终端直接访问热榜 API/页面分别返回 401/403，不能作为稳定的无凭证后端。
- 结论：第一版坚持官方 OpenAPI；配置凭证前不伪造“真实热榜成功”。
