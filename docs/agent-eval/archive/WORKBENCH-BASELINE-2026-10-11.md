# 工作台四场景真实 GUI 基线：独立阅读

这是 Codex 对原始证据的独立阅读，不是用户签署，也不替代原程序成绩。读取发生在整批执行结束后；没有修改冻结代码、题目、manifest、首次 claim 或原报告，没有重试模型、恢复任务或新增生图。

## 执行口径

- 冻结：`workbench-gui-v1-20261011`；批次：`workbench-v1-20261011`；执行源码：`cb53c22a6594608ea5140fe8b4a03e4a675887f4`。
- 原前端、真实隔离服务与 SQLite。A14/P01/P02 从账号聊天发送真实 query，外层为 `deepseek-v4-flash`；S13 从原 Skill 组合框直接委派真实 Codex。没有 Mock LLM 或受控 Producer。
- 四个首尝试均保留原 `failed` 报告。不能把后续在 fixture 读到的完成状态补写成原 GUI 验收通过。
- 每例报告根：`data/agent-eval-workbench/<run_id>/`；浏览器 Trace：`web/tmp/live-workbench/workbench-v1-20261011/*-<case_id>/trace.zip`。
- 下文时间全部为 **UTC**。本地日期 2026-10-11 与原文件中的 UTC 2026-10-10 不矛盾。

| 场景 | 首次 Eval Run | 原报告结果 | 独立事实 |
| --- | --- | --- | --- |
| A14 | `33b43c97a87b465cbb1d3f4c1414d322` | failed | 栏目准确创建，但宿主没有交付可点击栏目入口；真实产品缺口 |
| S13 | `17a092e5451140fe8b226dbdbd8879ee` | failed | 观察请求断开后提前采集；融合随后真实完成，但草稿查看、编辑与刷新没有验收 |
| P01 | `5752da893b684d0eb15b2d348fa3c59d` | failed | 入队、委派、Run 创建成功；观察中断，未完成交付，没有可验收新图 |
| P02 | `1c29014664934a66a3fc6e1a35877c9a` | failed | 正确双绑定、入队、委派；观察中断，组合协调随后完成，生产仍未结束，没有可验收新图 |

## A14：单完整 Skill 创建栏目

### 输入与执行链

Query：

> 用「四格辨词」这个完整 Skill 建个栏目「每日辨词」，面向高中英语学习者。

原账号 GUI 一次发送 → DeepSeek `list_producer_skills` → DeepSeek `compose_series` → SQLite 写入 → DeepSeek 正常 `stop`，回复已创建、选题库为空。三次真实模型请求，两个工具调用，没有 Codex 生产实例或生图。

### 数据库预期与实际

预期只新增当前账号栏目、准确完整 Skill 绑定及创建回执，不新增选题、生产或发布。实际符合：

- 新栏目：`series-d4e59dc325714465b9ab`，账号 `creator-workbench-a`，名称「每日辨词」，受众「高中英语学习者」。
- 单完整 Skill：`four-panel-vocabulary--6c2629ece6a238ec`；没有误写 Mind/Production 双绑定。
- `series` 1 → 2；`write_receipts` 0 → 1，操作 `create_series`，资源与新栏目对应。
- SQLite 的只读实库与保存的 `after.json` 栏目行一致；其余业务表、源 Skill 与业务文件未发生非预期变化。

### GUI 失败与原因

完整回复、复制、同会话刷新、Trace 与 Eval 全文读取均完成。之后账号聊天没有栏目交付链接：`assistant.links=[]`，对应工具 entry 的 links 也是空。测试没有进入栏目和绑定 Skill 的查看流程。

这是实际链接契约缺口，而非模型没有创建栏目或测试选择器找错：`compose_series` 的成功 DTO 有嵌套 `series.id`，但没有顶层 `url`；宿主 `chat_links._receipt()` 先校验 `data.url`，为空即返回，尚未读取栏目 ID。只在内存中给同一 DTO 增补真实 `/series/<id>`，原投影函数才产生「查看栏目」链接；该诊断没有写库或改源。

原四维程序结果中，边界、状态、协议通过，任务交付失败；这个区分合理。最终答复没有把栏目创建虚报为已调研、已生产或已发布。

## S13：真实 Codex 融合草稿

### 输入与执行链

Query：

> 把双语辨词方法与浅蓝漫画融合，先给草稿，保留来源资源，不直接入库。

原 Skill 卡片查看两份完整原文 → 加入组合框 → 一次 POST `/api/skill-extractions/merge`（202）→ 真实 Codex 读取两源和两图 → 编写融合草稿、保留来源副本、检查格式。该场景没有 DeepSeek 请求，不能因此当成采集缺失。

- Job：`31f1b3415e36d40cfa26c99d2a9f0d914d8aaf45f83ca97e698db6ee1137780e`。
- Thread：`01a127ee-b5e1-77f0-8e43-2a0c97dfd775`。
- Turn：`01a127ee-ba9f-7af3-9620-00e2abe91530`。

### 预期与采集时点实际

预期草稿 ready、两份资源按命名空间保留，用户能查看和编辑、保存刷新；确认前不入库、不组栏目、不生产。

22:30:00.596 的测试端 GET 出现 `read ECONNRESET`。测试立刻进入 finally，22:30:01 左右采集：`jobs.json` 为 running，`sdk.json` 68 项，无公开 turn 完成。原报告因此没有完整草稿/编辑验收证据。业务 SQLite 没有增量，没有自动入库或生产。

### 后时点事实，不补算 GUI 成功

只读实际 fixture 的后时点记录显示融合确实随后完成：

- 22:30:10.196：同 thread/turn `turn/completed`，status completed。
- 22:30:10.204：`capture/finished` completed，`capture_write_failed=false`；完整公开记录实际共 81 项。
- 22:30:12.585：Job ready、无 error、`saved_skills=[]`；融合草稿与 v001 已落盘。
- 两来源各 `SOURCE-SKILL.md`、`assets/reference.png`、`scripts/example.py`，共六份安全文件，逐份与草稿命名空间副本字节一致；同名参考图互不覆盖。来源文件不含真实凭证。
- 独立阅读草稿保留了词义核实、英中对应、场景解释和浅蓝呈现方法，也明确来源脚本只是存档、不强行变成执行步骤。

中间有命令失败（尚无目标目录、PowerShell 管道语法、Skill frontmatter 校验），Codex继续修正并最终完成；单个 item.failed 不是任务 failed。

但测试已切到 Eval，不曾执行草稿资产查看、编辑、保存与刷新。后时点 ready **只能说明融合生成完成，不能补算这些 GUI 步骤通过，也不能改原报告**。

## P01：单 Skill 一张四格词汇图

### 输入与执行链

Query：

> 给「每日辨词」新增 bring/take/fetch/carry 选题并生产，只要一张四格中英双语漫画，不额外标题，不发布。

原账号 GUI 一次发送 → DeepSeek `queue_topics` → 实际入队 → DeepSeek `start_content_run` → 创建 Run/Revision/Attempt → 真实 Codex 读取完整 Skill 与参考图、核实词义 → 用户收到准确「生产中、未发布」答复 → 原 GUI 点击 Run 入口并显示生产中。

三次真实 DeepSeek 请求正常结束，工具结果完整配对。复制、同会话刷新和 Trace 完成。新 Topic 的四个词与中文 brief 准确，不是转义乱码。

### 数据库预期与实际

预期一个正确归属的 Topic/Run/Revision/Attempt、单完整 Skill 冻结，最终一张新图可解码且 awaiting_approval，未发布。

实际创建阶段符合，终态阶段尚未完成：

- Topic：`topic-9f100c47403e43b0bb0e`，栏目 `series-workbench-vocabulary`。
- Run：`228bbb95-f099-4013-86fa-fde1239ba633`。
- Revision：`40231e71-9b76-43a5-982e-8e6d0fb198ed`。
- Attempt：`7215b5aa-20e5-42d9-9496-4365027e1dd3`。
- Thread：`01a127ef-1f92-78b1-ac98-02d37d993e67`；production turn `01a127ef-236d-7032-aa19-a0b53352a30e`。
- 入队回执、pending operation 的 scope、父子关联准确；只新增一组业务对象，没有对照账号写入或发布记录。
- 采集时 Topic/Run 为 producing，Attempt running；冻结完整 Skill 摘要存在，worker receipt 仍 running，没有完成交付索引、native checkpoint 或可验收新图。

### GUI 失败与后时点

22:30:03.502 测试端 GET `/api/runs/<id>` 出现 `socket hang up`，不是返回 failed 状态。原页面在 22:30:04.760 对同一 Run 的 fetch 仍为 200；finish/result/view 后续均成功。

原报告采集 46 项 SDK 记录；实际 fixture 后续达到 62 项，22:30:39.327 才开始 `imageGeneration`，未看到对应 completed、最终落盘图或 turn 完成。测试提前收尾及隔离服务生命周期结束截断了等待。不能据此说词义调研失败或 Codex 生成终态失败，也不能说已经完成一图生产。

## P02：双 Skill 三页连续图

### 输入与执行链

Query：

> 给「问题教学×小白图解」栏目新增选题：一次带幂等键的订单写入请求如何提交、重试和避免重复副作用。面向零基础读者，做三张连续图并生产，不发布。

原账号 GUI 一次发送 → DeepSeek `queue_topics` → 正确入队 → DeepSeek `start_content_run` → Run 创建与两源冻结 → 真实 Codex 单 thread 组合协调 → production。三次真实 DeepSeek 请求正常结束；最终说的是已提交、生产中、未发布，不是已产出。

### 数据库预期与实际

预期正确双绑定、两源冻结和协调；一个 Run，三张有序真实新图，逐图 HTTP/磁盘摘要一致、前端可翻页，最终待批准未发布。

- 栏目：`series-1bf021800cd8489faff3`，归属当前账号，Mind/Production 两绑定准确；只有合成配色种子参考，不冒充用户角色原图。
- Topic：`topic-7bd90ed0601b46a0a902`，标题与 brief 保留订单写入、幂等键、提交、重试、避免重复副作用、零基础和三页要求。
- Run：`1cc51923-baf1-4207-9346-3f9329db20f6`。
- Revision：`d232f4cc-c98f-4e41-90d1-5a4305427d13`；Attempt：`83ce47a3-c92d-4dd0-9398-c9b058054787`。
- Thread：`01a127f0-7bf7-7a83-a46b-6511f7e8fdb3`。
- 一组入队和生产记录、父子关联准确，两源冻结文件实际存在，未改对照账号或发布。
- 采集时 Topic/Run producing，Attempt running，没有最终新图或交付终态；不能把冻结资产当成新生产图。

### GUI 失败、独立误判与后时点

22:31:01.769 测试端 GET Run 出现 `socket hang up`；页面 fetch 在 22:31:00.796 仍返回 200，finish/result/view 随后成功。原 SDK 快照只有 10 项，仍处于 composition。

实际 fixture 后续 24 项：composition turn `01a127f0-834e-77e1-a817-b4c849ed99a6` 于 22:31:06 完成，协调说明 ready；同 thread 的 production turn `01a127f0-f919-7830-a798-a52da9268f8a` 开始后仍 running，未看到 imageGeneration 完成或新图。不能补算三图交付、翻页、Prompt 或封面通过。

此外原 `tool_chain` 有一项可确定的采集器假阴性：剪贴板全文为 Windows CRLF，保存 final 为 LF；仅统一换行后逐字一致，刷新显示也一致。原 grader直接比较全文因换行误报，并非 DeepSeek 工具链缺失。这个勘误不改原评分，也不抵消真实生产未完成。

## 跨例诊断与建议

### 真实产品缺口

A14 创建成功的 DTO 没有宿主权威栏目入口。应在后续独立版本统一业务回执与宿主链接投影，覆盖首次创建及幂等重放；不能让模型自行猜 URL，也不能只改测试跳过用户交付。

### 观察链路缺口

S13/P01/P02 都是测试端 `APIRequestContext` 的 GET 轮询抛出运输异常后，`expect.poll` 直接退出；不是等待 30 分钟到期，也没有业务终态 failed。finally 立即采集尚运行的任务并切入 Eval，随后测试结束/隔离服务生命周期结束，业务无法继续被验收。

本地 Uvicorn 默认 `timeout_keep_alive=5`，测试最后档间隔为 5000ms；同一时段原页面 2 秒 fetch 一直 200，测试断开后控制器接口也能正常使用。这**支持**连接过期边界竞争的推断，但没有抓包和完整服务 socket 日志，不能断言底层网络原因已确诊，更不能归咎 Codex SDK。

建议另冻结一个小范围修订：

1. 只对当前任务的只读 GET 做有限运输重试；保留每次异常及恢复记录。绝不自动重发 query、merge POST、入队、生产或模型调用。
2. 读取到 failed/unknown/interrupted/cancelled 要立即留证据；运输未知不能伪造业务 failed，也不能无限等待。
3. 观察失败时将已持久化的 thread/turn 与仍运行状态分开呈现，不把过早快照称为生产终态。重新运行使用新冻结和新批次，原首次失败不覆盖。
4. 复制全文只归一化平台换行，不删词、改正文或放宽原文验证；SDK/Prompt/文件摘要继续按实际字节核对。
5. 测试收尾错误应保留原始异常为主因。目前 finally 的最终检查失败会在测试输出里遮盖首次运输异常，虽 `browser.json` 仍存原错误。

原 Eval 页面四例均实际打开完整证据，核对了原 query、请求/上下文、答案及全部 14 张表，不是仅显示非空文件。其可读性通过不能替代栏目导航、融合编辑或真实图片生产完成。

## 用量和指标边界

| 场景 | DeepSeek 请求 | 模型工具调用 | 输入 tokens | 输出 tokens | 总 tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| A14 | 3 | 2 | 21,335 | 198 | 21,533 |
| S13 | 0 | 0 | — | — | — |
| P01 | 3 | 2 | 21,760 | 278 | 22,038 |
| P02 | 3 | 2 | 22,203 | 369 | 22,572 |
| 合计 | 9 | 6 | 65,298 | 845 | 66,143 |

- 数字直接求和真实 `requests.json`/`report.json` usage；不是费用估算。S13 直调 Codex，0 次 DeepSeek 不表示没有付费任务。
- Codex 真实线程三条；P02 在同 thread 有 composition 与 production 两 turn，不把 turn 数算新增任务数。
- 本轮可验收真实新图为 **0**；P01 已有生图 started 但没有成功图可供验收，不计一张。
- 费用实账：`null`。未读取 DeepSeek/Codex 实际账单，不把 SDK 未完成或零交付当零消耗。
- 本轮没有完成的图像质量成绩、用户签署或严格成功率提升；原自动四例均 failed，后时点 S13 ready 单列事实，不重写基线。

## 原始证据入口

- [A14 原报告](../../data/agent-eval-workbench/33b43c97a87b465cbb1d3f4c1414d322/report.json)、[浏览器记录](../../data/agent-eval-workbench/33b43c97a87b465cbb1d3f4c1414d322/browser.json)。
- [S13 原报告](../../data/agent-eval-workbench/17a092e5451140fe8b226dbdbd8879ee/report.json)、[采集时 Job](../../data/agent-eval-workbench/17a092e5451140fe8b226dbdbd8879ee/jobs.json)、[后时点 Job](../../data/agent-eval-workbench/17a092e5451140fe8b226dbdbd8879ee/fixture/studio-producer-skills/extractions/jobs/31f1b3415e36d40cfa26c99d2a9f0d914d8aaf45f83ca97e698db6ee1137780e/job.json)。
- [P01 原报告](../../data/agent-eval-workbench/5752da893b684d0eb15b2d348fa3c59d/report.json)、[原 SDK 快照](../../data/agent-eval-workbench/5752da893b684d0eb15b2d348fa3c59d/sdk.json)。
- [P02 原报告](../../data/agent-eval-workbench/1c29014664934a66a3fc6e1a35877c9a/report.json)、[复制与浏览器证据](../../data/agent-eval-workbench/1c29014664934a66a3fc6e1a35877c9a/browser.json)。

以上为本地忽略目录中的原始证据，不是提交进 Git 的正式运营数据；仓库克隆若没有对应本地证据，链接自然不可用。
