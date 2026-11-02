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

## 15 图批量提炼：2026-11-03 独立入口

15 图场景现在单独使用 `tests.live_batch_images_server` 与 `web/live-images/playwright.batch.config.ts`。旧 `CREATOROS_BATCH_IMAGES` 开关在聊天配置中会明确拒绝，其他聊天图片和旧证据复核入口保持原行为；不要把新 batch 配置视作已经修复旧聊天服务器的隔离问题。

目标：真实网页多选 15 张现场生成的色块图 → 15 个真实上传回执 → 一次真实 Codex 草稿提炼 → 核对 15 图摘要及草稿中的原始字节 → 刷新同一 Job 不重提。只检查链路和素材保留，不据此评价复杂作品的提炼质量。此文描述新契约，本轮只做零模型本地验证；真实浏览器与 Codex 留待集成后运行。

### 新建隔离构建与服务器

从目标工作树根目录运行，示例端口须确认空闲；服务拒绝 8765 及已有监听。root、dist 都必须属于本工作树 `tmp`，相互独立；server root 必须不存在。不会打开旧 SQLite、Skill/Run 或图片。

```powershell
$batchRun = Join-Path (Get-Location) ('tmp\batch-images-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $batchRun | Out-Null
$env:TEMP = $env:TMP = $env:TMPDIR = Join-Path $batchRun 'build-temp'
New-Item -ItemType Directory -Path $env:TEMP | Out-Null
$env:CREATOROS_BUILD_OUT_DIR = Join-Path $batchRun 'dist'
Push-Location web
npm run build
Pop-Location
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.live_batch_images_server --root (Join-Path $batchRun 'server') --dist $env:CREATOROS_BUILD_OUT_DIR --port 18995
```

这条默认命令仅准备：SQLite、Skill/附件/草稿、产物、聊天、评测、临时目录和 `CODEX_HOME` 均在 server root；不加载 `.env`，不启动生产/聊天/调研/发布后台服务，不允许创建提炼 Job。允许预留后做本地图片上传，上传本身不调用模型。

### 显式真实调用授权

已有本轮 Codex 授权后，另建一套新的 batchRun，服务器命令加 `--live-models`，并明确提供进程环境 `CODEX_API_KEY` / `OPENAI_API_KEY`，或加 `--codex-auth-file <已授权 auth.json 绝对路径>`。后者只复制指定认证文件进本轮 `CODEX_HOME`，不复制个人配置、历史会话、记忆或插件目录。认证值不要出现在文档、命令参数或 Git。

服务器不会在启动时提炼；只有匹配下面浏览器模式的第一笔提炼请求才可触发 Codex。不调用 DeepSeek；入库、试产、重写、生产、审批和发布 HTTP 写入口均禁用。外部账户和费用仍共享，这些隔离规则不是操作系统沙箱。

在另一终端给 `$batchRun` 设置这轮实际绝对路径，然后：

```powershell
$env:CREATOROS_BATCH_IMAGES_ROOT = Join-Path $batchRun 'server'
$env:CREATOROS_BATCH_IMAGES_URL = 'http://127.0.0.1:18995'
$env:CREATOROS_BATCH_IMAGES_LIVE = '1'
$env:CREATOROS_BATCH_IMAGES_OUTPUT = Join-Path $batchRun ('results-' + [guid]::NewGuid().ToString('N'))
Remove-Item Env:CREATOROS_BATCH_IMAGES -ErrorAction SilentlyContinue
Push-Location web
node node_modules/@playwright/test/cli.js test --config live-images/playwright.batch.config.ts
Pop-Location
```

`LIVE=1` 必须匹配服务器的 `--live-models`；根目录、数据库、构建、URL、nonce、临时目录均校验，不能只看到健康检查成功就提交。每次测试或 `--list` 都需新结果目录；`.owner.json` 排他预留本次输出，仅允许同一次调用的 Playwright worker 重载。脚本不启动、接管或停止已有服务。

### 失败、未知结果与保留证据

- 浏览器先用 fixture nonce 校验实例，再在当前 root 排他创建 `batch-reservation.json`，之后才开始页面上传。第二次浏览器运行同 root 会拒绝，不会因为换输出目录就重发。
- 服务器在调用真实提炼路由前排他写入 `batch-submission.json`，固定 request_id、可直接查询的 job_id 和请求内容。只允许一笔包含 15 个不同图片摘要的 single-mode 请求；相同或不同 request_id 再发都拒绝。
- 预留后网络失败，先检查上述文件及 `/api/skill-extractions/{job_id}`。有提交标记时默认结果未知，即使 HTTP 报错也不能删除标记、盲目重试或换 root 再提同一任务。Job 失败/中断照实保留；不自动恢复、重提或重跑。
- 服务器拒绝复用/重启既有 root，也不扫描旧队列。继续读取原任务应使用仍运行的服务的 GET 或检查原始文件；需要重新启动旧任务是另一项明确恢复工作。
- `batch-evidence.json`、reservation 回执、Trace、截图写本次结果目录；SQLite、上传原图、Job、SDK 原始记录和草稿保留在 server root。旧历史报告不回写。显示失败与模型/业务失败需分开报告。

### 零模型回归与验收边界

```powershell
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m unittest tests.test_batch_images_isolation -v
```

本地测试检查新 root/正式路径/端口/凭证拒绝、15 个真实本地 HTTP 上传的原始字节、prepare 模式绝不创建模型任务、排他提交与未知结果、防覆盖结果和旧聊天配置兼容；另运行 Playwright API-only worker（不启动浏览器）。如需手动运行准备检查，对默认准备服务器设置 `CREATOROS_BATCH_IMAGES_LIVE=0` 并提供全新输出目录，运行同一配置，只会选择 `batch-preparation.spec.ts`。

Given 全新隔离 root/build/port，When 只准备，Then 只有本地文件与上传、不调用模型。Given 双端明确 live 授权，When 真实上传 15 图并首次提交，Then 一份 Job 和一套保留全部原图的草稿。Given 刷新或网络结果未知，When 再次观察，Then 读取同一 Job、拒绝第二次派发。Given 旧 root/结果目录、正式端口或错误实例，When 启动或提交，Then 拒绝且保留旧字节。
