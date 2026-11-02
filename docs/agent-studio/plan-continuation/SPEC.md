# 隔离计划检查点与失败续接验收

2026-11-03。这里只改变测试入口，不改变产品计划、账号、生产或发布规则。历史浏览器报告及生产记录保留；旧脚本命令可由 Git 历史追溯，不应继续指向旧 staged 生产实例运行。

## 保留的两个场景

- `web/e2e/live-plan-review.mjs`：真实 DeepSeek 创建一个含 `request_review` 的计划，原定后续步骤是查询选题。检查点触发后台续接后，Agent 将这个尚未执行的原步骤改为查询账号 BGM 材料，再恢复计划并汇报。
- `web/e2e/live-plan-recovery.mjs`：首轮只让真实 DeepSeek 理解恢复要求，测试随后注入一个确定会失败的只读 `get_producer_skill` 步骤及其依赖步骤。后台续接必须保留失败错误、显式 skip、把原依赖步骤改为查询 BGM 并去除失败依赖，再完成计划、续接汇报。

用确定失败注入替代原 `review ... failure` 的“请模型制造失败”：后者可能绕过失败，无法证明恢复路径执行。读取不存在的 Skill 不涉及生产、文件修改或发布。BGM 库是本轮账号的全新空库；此处检验查询工具、步骤调整与计划续接，不检验音频文件或分析质量。

通过条件不只看 completed：只创建一个 Plan、一次用户消息；初始 plan 标记中的同一 `manage_topics` 步骤实际变为成功的 `manage_material`；检查点成功或失败步骤带原错误被 skip；至少两个不同 event_id 的宿主续接均成功结束；刷新只有读取，没有再提交。真实 DeepSeek 是否按要求调整，必须等真正 live 运行判定。

## 隔离及调用契约

服务器 `tests.live_plan_continuation_server` 只接受本工作树 `tmp` 下不存在的新 root、与其分离的独立构建、显式空闲且非 8765 的端口。全新 SQLite、随机账号、栏目/选题、空资源库、单一预建账号会话、输出、评测、临时及 Codex 用户目录均隔离，不读取旧数据。不提供 resume/reseed 既有 root。

默认模式只准备且不启动后台服务；`--controlled` 使用确定性本地 provider，`--live-models` 才读取环境中的 `DEEPSEEK_API_KEY` 并授权真实 DeepSeek。`.env` 不加载。启用时只启动聊天与 Plan 调度器，不启动生产、提炼、调研、安装或发布 worker。Codex 可执行文件及其凭证禁用；外部执行适配器另设拒绝函数。

fixture middleware 只接受本会话的首次用户消息与计划创建/调整/安全动作；计划步骤限 `request_review`、`get_producer_skill`、`manage_topics(get/list)` 和 `manage_material(get/list)`。新建第二会话、第二计划、重发首消息、计划 retry、生产/发布/提炼/素材写入等均拒绝；所有原账号范围校验继续执行。此限制是测试夹具的用途约束，不是完整身份认证或操作系统沙箱。

浏览器入口只从绝对 fixture root 读取 URL/身份，必须再明确 `--live`，并核对服务器 nonce；`--check` 只本地检查参数/manifest，不启动浏览器或模型。证据目录必须全新且与构建/数据分离，不复制到共享评审目录。启动后先排他创建 root 下 `reservation.json`；首消息与计划创建在路由调用前各写排他标记 `initial-turn.json` / `plan-submission.json`，保留 request/session/plan 身份。响应丢失默认结果未知，换浏览器证据目录也不允许重放。

## 运行

从目标工作树根目录新建这一轮目录与构建（每个场景另建一轮）：

```powershell
$planRun = Join-Path (Get-Location) ('tmp\plan-review-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $planRun | Out-Null
$env:TEMP = $env:TMP = $env:TMPDIR = Join-Path $planRun 'build-temp'
New-Item -ItemType Directory -Path $env:TEMP | Out-Null
$env:CREATOROS_BUILD_OUT_DIR = Join-Path $planRun 'dist'
Push-Location web
npm run build
Pop-Location
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.live_plan_continuation_server --root (Join-Path $planRun 'server') --dist $env:CREATOROS_BUILD_OUT_DIR --port 18996 --scenario review --live-models
```

上条 live 命令须已有本轮授权，且进程环境显式提供 `DEEPSEEK_API_KEY`；不要把值写到命令参数、Git 或报告。只准备时去掉 `--live-models`；零模型调度验证使用 `--controlled`。端口18996只是例子，若占用直接拒绝，不接管或停止原服务。

在另一终端设置这轮实际 `$planRun` 绝对路径后：

```powershell
node web/e2e/live-plan-review.mjs (Join-Path $planRun 'server') (Join-Path $planRun 'browser-check') --check
node web/e2e/live-plan-review.mjs (Join-Path $planRun 'server') (Join-Path $planRun 'browser-results') --live
```

恢复场景另建 fresh root，服务器改 `--scenario recovery`，浏览器改 `live-plan-recovery.mjs`。两个场景都不能连接原 staged-production 生产夹具。`--check` 不创建结果目录，也不预留 root；`--live` 才实际创建独立结果并打开 Chrome。

失败时保留浏览器 `browser-report.json`、Trace、失败截图及 server root 中的 SQLite、会话/请求 Trace 和三个提交标记。标记存在时先通过原服务 GET session/plans 或检查原文件，不删除标记，不盲目重试，也不另建 root 重复同一项结果未知的任务。服务器不接受既有 root；重新启动原任务属于另一次明确恢复工作。

## 本地验证与限制

```powershell
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m unittest tests.test_plan_continuation_isolation -v
```

本轮该入口 4/4：纯隔离/排他声明、正式端口与旧目录拒绝、两条实际 HTTP/账号 Agent loop/Plan 调度链。控制 provider 仅在实际宿主续接请求中发安全调整工具；它不是 DeepSeek，也不证明真实模型会采取相同步骤。测试使用 driver 的 `--check` 与拒绝 `--live` 的受控 fixture，不启动浏览器。JS 语法检查通过。

尚未验证：真实 Chrome 点击、真实 DeepSeek 选择/调用/延迟与费用。真实模型可能误用工具或未按要求建立检查点，测试会失败并保留原结果，不自动补一轮。独立新目录保证可重复开展新实验，不表示可以重发同一个未知任务。
