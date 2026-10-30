# 选题交付验证（2026-10-30）

## 交付契约

- Codex 返回 `ResearchReceipt` JSON：`candidates[]` 含 title、angle、rationale、sources；另有批次 note。不是文件路径交付。
- SDK 完成后，宿主校验 JSON、数量与已完成 search 事件；成功候选由宿主分配 c1/c2，保存数据库旁的批次 JSON。此时 SQLite Topic 数仍为 0。
- 显式选择后才写入 SQLite Topic，按选择顺序保留标题、切入点、推荐理由和来源。候选确定性 Topic ID 防止重复入队。
- 重复选择同一候选会拒绝且不重复写入；不同候选文字相似不是本协议的硬去重范围。字段有效不等于事实质量或来源内容已被人工核实。

## 验证

- `tests/test_topic_delivery.py`：6 个隔离测试通过，使用真实 SQLite/文件与受控 researcher 输出。覆盖两候选保存不自动入库、逆序入库、来源和切入点保留、缺字段/错误类型/非法来源/空来源导致整份失败且零 Topic、重复/未知候选拒绝、重复提交不新增、空结果不凑数。
- 首次测试运行有夹具问题：旧 seed_batch 缺 progress、重复批次目录以及将空 tuple 与 list 比较；已修正测试，不是产品失败。
- 既有 `tests.smoke_topic_research_sdk` 通过：真实适配器上的 SDK 事件故障注入，覆盖无搜索、只有打开页面、坏 JSON、超数、失败/取消/超时；不调用真实模型。
- `tests/live_topic_delivery.py` 已真实调用 gpt-6-luna/xhigh + live web search，一次提交、两个候选。报告 `tmp/topic-delivery-live-20261030-164305/report.json`，3 个完成的 web 事件；选择前 SQLite Topic=0，选择 c2/c1 后 Topic=2 且顺序、来源、切入点正确。
- 真实 thread `01a18bfa-3bd8-7b22-b2da-df66678563c9`；累计 input 113609、cached input 77440、output 1337、reasoning output 794。不是单次上下文大小，不作为质量评分。
- 未生成图片、未发布、未修改正式数据库或正式产物、未重启服务。仅增加测试和文档，没有修改业务逻辑。
