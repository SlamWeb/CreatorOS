# 图片文件引用交付

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
