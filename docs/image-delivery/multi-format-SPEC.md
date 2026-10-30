# 多类型交付验证（2026-10-30）

## 范围

验证现有图片/图文、视频、选题交付，不为统一格式新增一层框架。独立 tmp、SQLite、真实媒体文件；正式产物与业务行不修改，不重启服务。

- 图文：图片顺序与逐页正文对应，标题/发布文案/标签保真，错误结果不能交付。
- 视频：MP4 文件路径及发布文案，真实解码/音轨/编码校验，失败恢复与原文件保护。
- 选题：结构化候选列表、字段校验、重复与持久化结果。
- 自动回归允许本地媒体及 SDK 故障注入；真实 Codex 低成本调用单列，不把注入回执称为真实模型验证。
- 已发现或新发现的失败如实记录，不修改已有评测分数。

## 验证结果

### 交付形式

| 类型 | Codex返回 | 宿主动作 |
| --- | --- | --- |
| 图片/图文 | order、source_image_path、逐页content，以及title/text/hashtags | 对照SDK收图事件核验、保存正文，组装SocialContentPack |
| 视频 | complete、work相对video_path、title/text或error | 核验路径、实际解码H.264/AAC MP4，保存video_artifact.json |
| 选题 | candidates中的title/angle/rationale/sources及note | 校验并保存候选批次JSON；显式选择后写入SQLite Topic |

### 隔离回归

`python -m unittest tests.test_file_delivery tests.test_graphic_delivery_contract tests.test_video_delivery_contract tests.test_topic_delivery`

- 图片文件引用10项，图文7项，视频9项，选题6项。视频中的错误路径恢复为expectedFailure，不计入通过。合并运行结果见 `tmp/multi-format-delivery-20261030/fixtures.json`。
- 图文验证中文、换行、标签、长正文、空逐页正文、双图反序和正文对应、缺字段/不完整/坏页序拒绝。
- 视频使用真实本地ffmpeg媒体，验证正常/中文路径、假MP4、缺音轨、越界/绝对/不存在路径、离线恢复保护旧文件。SDK响应为显式故障注入。
- 选题使用真实隔离SQLite，验证候选与入库分离、字段/来源失败、重复选择、未知候选和空结果。候选语义相近不等于硬性重复，不声称语义去重已验证。

### 真实Codex

均为gpt-6-luna/xhigh，独立临时目录，正式数据库/旧产物未修改，服务未重启。

1. 图文：`tmp/graphic-delivery-live-20261030-a/report.json`。真实生成1张图，发布标题、两段正文、标签和逐页正文逐字通过；SQLite awaiting_approval，真实HTTP图片字节通过。input61482/cached18560/output478。
2. 视频：`tmp/video-delivery-live-20261030-a/report.json`。本地PNG+WAV真实交给Codex，经ffmpeg生成2秒160x240 H.264/AAC MP4；produce_video及完整解码验收通过。input239751/cached189568/output4386。只验证Producer/文件交付，不是前端E2E或剪辑质量评测；未验证抖音BGM获取。
3. 选题：`tmp/topic-delivery-live-20261030-164305/report.json`。真实联网访问Python官方文档，3个完成web事件、2候选；候选阶段Topic=0，逆序确认后Topic=2，切入点与来源保留。input113609/cached77440/output1337。额外smoke_topic_research_sdk通过。

用量为各任务累计请求用量，不是单次上下文长度。上述探针均未批准或发布。

### 已知失败（未修复）

`test_invalid_completed_receipt_can_be_repaired_on_explicit_retry`：视频SDK回合completed先写入turn_completed=True，随后发现回执指向missing.mp4；下一Attempt仍据该标志跳过SDK并重新验同一个错误路径，无法修复。真实成片保留，但恢复不能完成。标记expectedFailure只为显式记录已知缺口，不能说“全部通过”。下一步应区分回合结束、产物有效与可离线收尾，单独补有界技术修复，不能重新渲染代替修交货单。

视频Trace另有一次多余的全局Skill路径读取后回退冻结路径，本轮记录为效率观察，不与交付失败混淆。
