# 图片文件引用交付

## 2026-10-30 提交工具：实现与验收

- 保留现有派活入口。新图片/图文及视频任务向 Codex 注册 `submit_artifacts`，由宿主在同一执行回合内验证并反馈；旧协议和旧任务继续读取，不修改正式历史记录或自动恢复任务。
- 图片继续复用 SDK 事件收图和当前任务库存；模型提交路径、顺序、正文，内部 asset_id 仍由宿主管理。视频复用已有路径/媒体解码校验。
- 验收成功须先保存接收回执，再返回成功；相同交付重复提交返回同一结果。失败反馈具体原因，限定失败尝试次数。修清单优先复用已有文件，不把提示词当成禁止生图的硬权限。
- 本轮不统一全部委派工具，不改调研协议，不改人工验收/发布，不重启正式服务。
- 验证：本地真实文件故障注入覆盖错误路径→修正、重复提交、恢复、旧协议；真实隔离 Codex 验证工具注册和同回合错误修复，并记录实际费用/用量。视觉对比图单独保存，不作为实施证明。

### 本轮结果（不覆盖前轮记录）

- SDK 适配集中在 `codex.py`；项目已固定 openai-codex 0.157.1。该版本高层 API 未暴露动态工具注册，本次通过 app-server RPC 注册、私有回调适配接收调用。升级 SDK 时必须重跑传输测试；不修改已安装 SDK。真实注册、拒绝→修正和跨 client 恢复已验证。
- 图片 `tool-v1`：沿用 SDK 事件接收库存，提交时验证路径/顺序/文案；宿主保存 `artifact-submission.json` 和 checkpoint 后回成功。视频 `submit-tool-v1`：提交时验证 work 内 MP4、编码与解码，保存接收副本和 checkpoint 后回成功。普通返工继承原线程协议，新版本不继承旧图片回执。
- 图文真实验证：`tmp/submit-artifacts-live-image-20261030/report.json`，gpt-6-luna/xhigh。故意第一次提交错误路径，同一 production turn 修正成功；收到 1 张、采用 1 张，文案/段落/标签保留。隔离 SQLite 为 awaiting_approval，HTTP 详情及图片均 200，图片摘要一致。输入 97133（缓存 70784），输出 1453 tokens。未批准、未发布。
- 视频真实验证：`tmp/submit-artifacts-live-video-20261030/report.json`，gpt-6-luna/xhigh。本地 PNG/WAV 生成 2 秒 160×240 MP4，第一次 missing.mp4 被拒，第二次提交成功，音轨和实际解码通过。输入 194965（缓存 144640），输出 3572 tokens。未下载网络媒体、未写正式数据。
- 传输验证：`tmp/dynamic-submission-6mbbrms0/report.json`。真实模型/SDK 验证动态工具和线程恢复；其文件校验回调为注入夹具，不冒充真实媒体生产。媒体真实性由上面两项另行验证。
- 回归使用临时文件及故障注入，验证重复提交、存储失败不误报成功、回执防篡改、中断恢复和旧协议。旧 final-JSON 视频坏路径恢复的 expectedFailure 原样保留，不将它记作通过。
- 最终定向回归 127 项：126 通过、1 项上述已知 expectedFailure。命令为 `python -m unittest tests.test_codex_dynamic_tools tests.test_artifact_submission tests.test_video_submit_tool tests.test_file_delivery tests.test_graphic_delivery_contract tests.test_video_delivery_contract tests.test_topic_delivery tests.test_task_video_execution tests.test_structured_handoff tests.test_native_image_assets tests.test_native_delivery_recovery`；约 95 秒。移除了新测试模块导入旧 TestCase 导致的重复发现，未修改旧断言或结果。
- 没有执行浏览器点击验收，也没有重启正式服务、恢复历史失败或修改旧图片/分数。本轮“不要重新生图/渲染”仍是提示约束；提交工具保证验证及反馈，不保证审美质量，不宣称完全消除生成重试。
- 对比图：`assets/submit-artifacts-comparison.png`；图意与生成说明在同名 Markdown。主题为“结束后核验 → 结束前拿到回执”。

## 2026-10-30 实现与验证

- 工程问题：模型为最终交付再次读取 generated-assets.json 并抄写内部长编号，工具读取失败会阻断已经收图的任务。
- 新模型输出使用 order + source_image_path，保留 image_prompt/reference_ids/content。SDK 完成事件仍为收图凭证，路径须对应本任务库存；宿主验收后映射内部 asset_id，既有图片展示和存储保持。
- 旧 structured-v1 asset_id 响应继续接受；新模型 schema 只暴露路径。两种字段不得混填。不改正式旧记录、不恢复历史失败、不重启正在运行的服务。
- 修复提示直接携带已接收图片路径和生成说明，避免再次读取图片目录。提示禁止重画不等于工具级禁止，本步不宣称已建立硬性禁生图机制。
- 验证：隔离文件/SDK 事件故障注入覆盖顺序、路径归属和旧格式兼容；真实 Codex + 隔离 SQLite/HTTP 试产确认路径交付。验证结果待补。

## 验证结果

- 原有 structured handoff / native image assets / native delivery recovery 共 61 项通过；新文件引用回归 10 项通过。故障注入使用隔离 PNG/typed SDK 事件，不冒充模型验证。
- `tmp/file-delivery-offline-20261030/report.json`：两张真实旧失败 PNG 的隔离离线回放通过，反接收顺序交付，SDK/新生图均为 0，原记录及图片摘要不变。清单和执行回执是测试构造，未恢复正式任务。
- `tmp/file-handoff-live-20261030-a/report.json`：真实 gpt-6-luna/xhigh 单图交付通过；模型返回 source_image_path，宿主转换内部 ID，隔离数据库状态 awaiting_approval，HTTP 图片字节核对通过。输入 40542（缓存18432），输出794 tokens；未批准/发布。
- `tmp/file-handoff-live-20261030-b/report.json`：真实两图通过，先猫后狗生成、按狗猫逆序交付；两张收到两张采用，没有重复生成，进入 awaiting_approval。输入62766（缓存18432），输出1315 tokens。正式服务未重启。历史 asset_id-only 验证脚本仍代表旧协议，新验证入口 `python -m tests.live_file_handoff --images 2 --directory tmp/<新目录>`。
