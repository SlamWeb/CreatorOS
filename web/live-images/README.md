# 图片聊天真实验收

此目录不属于普通 `web/e2e`，也不计入历史 Eval 成绩。测试不会启动服务；必须先准备独立账号、数据库、Skill 库和附件目录，并启动隔离 Studio。不得指向正式运营数据。

`CREATOROS_IMAGES_ROOT` 指向隔离夹具目录，内含 `setup.json`（`creator_id`、`series_id`、`skill_id`）、`reference.png` 和 `result.png`。当前夹具图片为白底红色方块 `ALPHA 73` 与白底蓝色方块 `BETA 29`，答案只用于断言，不发送给模型。

首次完整路径会付费调用真实 DeepSeek：真实剪贴板 Ctrl+V 两图 → 看图比较 → 明确要求仅增加一句 Skill 规则 → 导入一张成品为待验收。导入不批准、不发布、不重新生图。

```powershell
$env:CREATOROS_IMAGES_ROOT = 'D:\CreatorOS\tmp\live-chat-images-20261015'
$env:CREATOROS_IMAGES_URL = 'http://127.0.0.1:8905'
npx --prefix web playwright test --config web/live-images/playwright.config.ts
```

已有完整业务证据的后续查看只用 GET，不重发任务：

```powershell
$env:CREATOROS_IMAGES_REVIEW_REPORT = '<绝对路径 browser-evidence-时间戳.json>'
npx --prefix web playwright test --config web/live-images/playwright.config.ts
```

图片提炼是另一个显式付费入口：设置 `CREATOROS_IMAGES_EXTRACT_SESSION` 为本隔离账号已上传两图的会话 ID，清除 `CREATOROS_IMAGES_REVIEW_REPORT`，再运行同一命令。只创建草稿，不入库、不试产、不生图。等待最多 10 分钟；超时或失败保留原 Job，不能自动重提。要检查同一提炼任务，额外设置 `CREATOROS_IMAGES_EXTRACTION_JOB`，测试会只读取原会话链接而不再发送用户指令。

浏览器证据每次写入独立 `web/tmp/live-images-browser-时间戳/`，业务记录保存真实 Session、Turn、Job、Run ID。断言脚本错误与模型/业务错误需分别报告，不能通过覆盖旧报告抹去失败。
