# 并行开发与浏览器测试

在仓库根目录，给每个实例选择**不同且未被占用**的 API 端口、Vite 端口和不存在的新目录，例如两个 PowerShell 窗口分别执行：

```powershell
python -m scripts.isolated_dev --root "$env:TEMP\creatoros-dev-a" --api-port 18871 --vite-port 15171
python -m scripts.isolated_dev --root "$env:TEMP\creatoros-dev-b" --api-port 18872 --vite-port 15172
```

使用打印出的 Vite 地址访问；两个窗口要保持运行。Ctrl+C 只结束该启动器创建的 API 和 Vite 子进程，不会删除数据。启动器拒绝 8765、相同或占用的端口及已存在的根目录；每次启动必须选择新目录，不自动迁移或恢复已有队列。API 直接使用真实业务服务，Vite 请求代理到本实例的 API；数据库、输出、Web 会话及其附件、Skill/调研、导出、临时文件和 Vite 缓存位于根目录。默认提供真实生产功能，操作可能调用外部服务，测试不要启动未授权的生产或发布。发布暂存目录也被指向新根；若外部 MCP 容器未挂载此目录，发布不可用，不应为测试改接正式暂存目录。

直接运行 `npm run dev` 时必须显式设置 `CREATOROS_API_URL`，否则 Vite 会给出启动示例并退出，不再自动连接 8765。构建和 preview 不要求该变量。

需要确定性浏览器夹具时可附加 `--fixtures`；它使用 `tests.studio_e2e_server` 的受控生产者，禁用继承的模型凭证、Codex 可执行程序和发布入口，不代表真实 Codex/模型结果，也不是网络沙箱。

常规 Playwright 回归也可以同时运行，默认配置提供真实 API + Vite 的受控环境：

```powershell
$env:CREATOROS_PYTHON = 'D:\Anaconda4.7g\envs\deepcode\python.exe'
$env:CREATOROS_E2E_PORT = '18881'
$env:CREATOROS_E2E_VITE_PORT = '15181'
Set-Location web
node node_modules/@playwright/test/cli.js test --config playwright.config.ts e2e/home-page.spec.ts
```

第二个窗口设置另一个 API/Vite 端口。每次调用在系统临时目录生成独立运行根并打印其位置，包含 SQLite、会话、产物、Vite 缓存、Playwright 结果和子进程临时目录；失败证据不会被下一次运行覆盖。`playwright.agent-plan.config.ts`、`playwright.staged.config.ts`、`playwright.production-tasks.config.ts` 和 `playwright.compact-results.config.ts` 同样支持 `CREATOROS_E2E_PORT` 和独立结果，但使用它们原有的静态构建，而非 `CREATOROS_E2E_VITE_PORT`。原先的 `STAGED_UI_OUTPUT`、`PRODUCTION_TASKS_TEST_OUTPUT` 目录不再作为固定结果路径。未设置 `CREATOROS_E2E_VITE_PORT` 时，默认配置也使用原有静态构建。若端口被占用，测试失败，不复用或终止原服务。

并行构建或冻结 Benchmark 前，给每次 `npm run build` 指定不同的**不存在的绝对目录**，例如在 `web` 目录：

```powershell
$env:CREATOROS_BUILD_OUT_DIR = Join-Path $env:TEMP ('creatoros-dist-' + [guid]::NewGuid().ToString('N'))
npm run build
```

构建先完成类型检查，再使用独立临时缓存和指定目录；目录已存在时拒绝覆盖，失败时保留部分文件供检查。可以把构建目录传给相应测试的 `CREATOROS_E2E_DIST`、`STAGED_UI_DIST` 或 `PRODUCTION_TASKS_DIST`。每次构建结束打印目标路径。未设置变量时保留既有 `web/dist` 行为，因此不要在同一工作树同时运行多个默认构建。`npm run e2e` 会自行创建独立 dist，不写 `web/dist`；并行执行仍须分别设置 API 端口。不同输出目录避免互相覆盖，但如果构建期间源码变化，不构成同一冻结版本的证据。

`web/node_modules` 可能是指向另一工作树的 Junction。隔离启动器和默认浏览器配置通过 Vite 的 `runner` 配置加载器与实例内缓存避免写入该共享目录；`npm run typecheck` 为每次调用使用独立的临时 TypeScript 缓存。手工调用不带 `--configLoader runner` 的 Vite 仍可能写入共享 `node_modules/.vite-temp`，也不要并行修改或安装共享依赖。

本次不覆盖 `playwright.media.config.ts`（其夹具读取指定源库和现有产物）及各 `*-live` / `live-images` / `context-focus` / `media-agent` 配置；其中仍可能有固定端口、固定结果目录或外部调用，使用前须另行确认隔离，不能把上面的结论套用到它们。

这些隔离措施不改变系统凭证或外部服务：启动器报告 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`CODEX_API_KEY` 是否来自进程环境或仓库 `.env`，不输出值。`CODEX_HOME` 若未单独设置，默认指向用户 `.codex`，其中的配置、登录状态、会话和 `generated_images` 仍可能被共享；Codex 还可能使用系统凭证存储。外部模型账户、网络服务、XHS MCP 服务以及 standalone CLI 的默认 `sessions/latest.json` 也未按本启动器自动隔离。自定义 `CODEX_HOME` 的登录和生图能力须另行验证；不要复制或打印凭证。

验证入口：

```powershell
python -m unittest tests.test_isolated_dev -v
python -m unittest tests.test_isolated_build -v
```

该测试默认在同一 checkout 中同时启动两套 API/Vite 和两套 Chrome 回归；要验证两个实际工作树，请先分别安装各自的前端依赖，然后设置 `CREATOROS_SECOND_WORKTREE` 为第二个工作树的绝对路径再运行同一测试。它使用受控生产者，通过 HTTP 和界面检查各自账号、会话附件、图片产物及跨实例拒绝；不连接 8765，不调用真实模型。第三方工具、凭证或外部服务仍可能共享。
