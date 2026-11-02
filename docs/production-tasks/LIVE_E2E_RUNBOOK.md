# 制作任务 E2E 的当前隔离入口

2026-11-03：本入口只创建新夹具，不读取旧数据库、历史 Run、图片或 session。2026-10-27 的真实结果仍在 [VALIDATION.md](VALIDATION.md)，不能当成本轮真实模型验收。

## 只准备与验证 HTTP（零模型）

从仓库根目录启动。以下例子使用已有的 deepcode Python，不安装依赖。每轮都生成新目录；`tmp` 本身不得指向仓库外。

```powershell
$taskRun = Join-Path (Get-Location) ('tmp\production-tasks-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskRun | Out-Null
$env:TEMP = $env:TMP = $env:TMPDIR = Join-Path $taskRun 'build-temp'
New-Item -ItemType Directory -Path $env:TEMP | Out-Null
$env:CREATOROS_BUILD_OUT_DIR = Join-Path $taskRun 'dist'
Push-Location web
npm run build
Pop-Location
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.live_production_tasks_server --root (Join-Path $taskRun 'server') --dist $env:CREATOROS_BUILD_OUT_DIR --port 18990 --prepare-only
```

端口必须空闲且不能是 8765。root 必须不存在；dist 必须在本工作树 `tmp` 内、包含 `index.html`，并与 root 相互独立。既有 root、正式 `web/dist`、仓库外目录和占用端口都会拒绝。

默认即 `--prepare-only`：创建新 SQLite、两份本地 Skill、一个测试账号、两个栏目和一条选题；不会创建 Run/Attempt，不启动后台服务，所有 HTTP 写请求返回 403。它仍提供静态页面和查询，不能据此声称真实模型或完整 UI 验收通过。

服务保持运行时，在另一终端设置这轮的实际 `$taskRun` 绝对路径，然后运行：

```powershell
$env:CREATOROS_TASKS_LIVE_ROOT = Join-Path $taskRun 'server'
$env:CREATOROS_TASKS_LIVE_URL = 'http://127.0.0.1:18990'
$env:CREATOROS_TASKS_LIVE_MODELS = '0'
$env:CREATOROS_TASKS_LIVE_OUTPUT = Join-Path $taskRun ('results-' + [guid]::NewGuid().ToString('N'))
Push-Location web
node node_modules/@playwright/test/cli.js test --config playwright.production-tasks-live.config.ts
Pop-Location
```

`MODELS=0` 仅选择 API 准备测试，不打开浏览器、不发 Agent 消息。fixture 标识必须匹配本地 `setup.json`；不能只凭 `/api/health` 成功就认定接对实例。后端数据、输出、聊天、评测、Codex 用户目录和临时文件都在这轮 server root 内；构建及浏览器结果各用独立目录。

## 真正调用模型（需要单独的明确授权）

只有已获本轮真实模型调用授权时，另建一轮新 root，用 `--live-models` 替代 `--prepare-only`。服务不会自动读取 `.env`；事先在进程环境设置 `DEEPSEEK_API_KEY`，并明确提供 `CODEX_API_KEY` / `OPENAI_API_KEY` 或 `--codex-auth-file <已授权 auth.json 的绝对路径>`。后者只将指定认证文件复制到本轮新 `CODEX_HOME`，不复制用户原有配置、记忆、会话或其他数据。不要把凭证写进命令、文档或 Git。

浏览器设置 `CREATOROS_TASKS_LIVE_MODELS=1`，其余变量指向这一轮的新 fixture、URL 和新结果目录，然后运行相同 Playwright 命令。它才会打开 Chrome，通过真实 DeepSeek 提交两个 Codex 产图任务并检查素材集和 ZIP。凭证存在与配置解析成功不代表真实模型链路已经验证；外部模型账户和计费仍共享，这不是操作系统网络或文件沙箱。

不启动或重启正式服务，不自动批准、发布或重试。fixture 禁用发布凭证，只接受聊天会话/消息，以及带账号会话范围的 Agent 向当前测试选题提交制作任务的 HTTP 写入；保留正式账号归属检查。测试指令限制两张图和一次 `start_content_run`，但真实模型行为仍需检查实际 Trace，不能把提示词当作硬预算限制。

## 失败与证据保护

- Playwright 必须明确提供本轮 URL，限制为非 8765 的 `127.0.0.1` HTTP 端口，且与 `setup.json` 相同。测试开始时再核对服务返回的 fixture 标识、root 和运行模式。
- 每次运行或 `--list` 都选择全新的 `CREATOROS_TASKS_LIVE_OUTPUT`。旁边的 `.owner.json` 以排他写入保留这次配置所有权，仅允许同一 Playwright 调用的 worker 重载；已有结果不会被 Playwright 清空覆盖。
- 真模型测试在首次 UI 写入前排他创建 `server/live-submission.json`。同 root 再运行会拒绝提交。网络失败可能表示结果未知，不能删除标记后盲目重试，也不能换一个新 root 来掩盖同一已提交任务的结果未知。
- 新入口拒绝 `CREATOROS_TASKS_EXISTING_SESSION`。旧只读续验的历史记录保留；当前需要续验时，应针对原 session/Run 先核对，再使用单独只读检查，不能通过这个 fresh-only 入口重发消息。
- conversation、accepted、completed、session 提交回执、截图、Trace 和 ZIP 写入这次 Playwright 结果目录；生产数据库和图片留在 server root。失败目录保留，不自动清理。

## 轻量本地回归

```powershell
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m unittest tests.test_live_production_tasks_isolation -v
```

验证参数拒绝、真实 prepare-only HTTP/SQLite、继承凭证和正式地址覆盖保护、Playwright 配置解析/worker 重载及旧结果保护。不发付费 API，不跑真实生产，不验证模型画质或真实 Chrome 操作。
