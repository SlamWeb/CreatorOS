# 浏览器回归入口

在仓库根目录的 PowerShell 中设置项目 Python，并运行受控全量回归：

```powershell
$env:CREATOROS_PYTHON = 'D:\Anaconda4.7g\envs\deepcode\python.exe'
npm --prefix web run e2e:all
```

入口先核对 `web/e2e/*.spec.ts` 清单，新增或删除 spec 后需要人工分类，不能静默跳过。随后在独立目录构建前端，分组启动真实本机 HTTP API 和 Chrome：普通 Studio 夹具、Agent Plan 夹具、制作任务/阶段夹具和 compact 夹具。各组分别使用系统分配的空闲端口、独立 SQLite、文件、会话及 Playwright 结果。日志打印根目录，`summary.json` 记录每组的通过、失败、跳过、未发现的文件与原始 JSON 报告；部分失败时仍执行后续组。`--group compact` 等选项仅用于排查，汇总明确标为部分运行。

入口记录源码、spec 和前端资源的开始指纹，结束时若检测到变动会标出文件并将总结果标为失败；这不能发现运行期间被改动后又恢复的文件，也不冻结依赖或浏览器版本。需要同一版本的最终结果时，先等并行代码修改结束，再从稳定工作树执行。

受控回归清除 live 开关和 `AGENT_BASELINE`，不继承真实 DeepSeek 密钥或小红书地址；Codex 用户目录指向独立根，程序指向不存在的路径，Windows 下也阻止真实 SDK 启动。生产/提炼和参考图调研使用受控夹具，不能据此宣称真实模型通过。这些设置不是操作系统网络沙箱，其他外部服务和手工执行的脚本仍需独立审查。

`live-topic-references.spec.ts` 的 2 项真实模型测试不计入受控全量通过：一项调用 Codex 实际生成一张图，另一项经真实 DeepSeek 和 Codex 联网调研。普通配置不会发现它；下面是单独执行的入口。仅在已授权使用模型额度、确认独立数据和空闲端口后执行。下例的固定目录必须不存在，重复实验换新目录，不自动重跑或覆盖未知结果。

在仓库根目录的第一个 PowerShell 窗口先为本次测试构建独立前端，再启动服务：

```powershell
$env:CREATOROS_PYTHON = 'D:\Anaconda4.7g\envs\deepcode\python.exe'
$probe = Join-Path (Get-Location).Path 'tmp\reference-live-full-e2e'
if (Test-Path -LiteralPath $probe) { throw '请换一个不存在的新目录，不要覆盖已有证据。' }
$dist = Join-Path $env:TEMP ('creatoros-live-dist-' + [guid]::NewGuid().ToString('N'))
$env:CREATOROS_BUILD_OUT_DIR = $dist
npm --prefix web run build
if ($LASTEXITCODE -ne 0) { throw '独立前端构建失败，请检查保留的构建目录。' }
Remove-Item Env:CREATOROS_BUILD_OUT_DIR
& $env:CREATOROS_PYTHON -m tests.live_topic_references --serve --root $probe --dist $dist --port 18931
```

启动器拒绝已有根目录、缺失的独立构建、共享的 `web` 目录、8765 和占用端口，并在导入应用前设置新 SQLite 地址。`setup.json` 记录构建目录及每个构建文件的 SHA-256；仍需在源码稳定后构建，且运行期间不要改动该目录。核对监听进程确实是上面启动的服务、打印的根目录和构建目录正确。服务还会生成测试 Skill 和两张参考图；需要已配置且可用的 DeepSeek 与 Codex 凭证，以及 Chrome。真实 Codex 的用户级配置、登录及生成图片目录仍可能共享；配额和外部模型调用不受 SQLite 回滚保护。

在第二个 PowerShell 窗口进入同一工作树的 `web`：

```powershell
$env:CREATOROS_LIVE_REFERENCE_ROOT = (Resolve-Path '..\tmp\reference-live-full-e2e').Path
node node_modules/@playwright/test/cli.js test --config playwright.live-reference.config.ts
```

该配置只接受本工作树 `tmp` 下的独立根、该根内两张参考图和显式本机非 8765 端口，不替使用者证明端口当前监听的进程确实指向该数据库。浏览器证据写入该根下新的 `browser-results-*`。失败或结果未知时保留 SQLite、文件、会话及报告，先核对是否已经发生模型调用或生产，不直接用同一目录重跑。

`web/e2e` 下 12 个 `live-*.mjs` 是独立手动脚本，不属于 Playwright spec 清单；`web/live-images`、`live-calibration`、`live-context-focus`、`production-tasks-live`、`media-tests` 和 `media-agent-tests` 是独立目录，均不计入以上全量结果。它们各有来源、凭证、历史工件和外部副作用前置条件，不要默认接入正式服务。
