# 浏览器回归入口

## Skill 六图换两图的独立真实评测

`web/media-agent-tests/skill-edit-batch.spec.ts` 只由专属配置发现，不再混入旧素材集 Agent 配置。入口每次创建当前工作树 `tmp/skill-edit-batch-*` 下的新数据库、两个随机 ID 账号、两个 Skill、六张不同旧 PNG、两张新 PNG、独立 Vite 构建、系统分配的非 8765 端口、聊天和浏览器结果。它不读取历史账号、图片或 Skill，也不启动生产/发布/研究消费者；live 模式只启账号聊天，使用真实 DeepSeek，Codex 和其他外部执行器被禁用。这不是操作系统网络沙箱。

在当前工作树根目录的 PowerShell 中先做不调用模型的准备检查：

```powershell
$env:CREATOROS_PYTHON = 'D:\Anaconda4.7g\envs\deepcode\python.exe'
npm --prefix web run e2e:skill-edit-live -- --prepare-only
```

准备只证明隔离库和夹具可建立；`summary.json` 为 `prepared_not_run`、通过数为零，不启动浏览器或付费请求。真实执行需要已在环境变量中配置 `DEEPSEEK_API_KEY`，并显式打开 live：

```powershell
$env:CREATOROS_SKILL_EDIT_LIVE = '1'
npm --prefix web run e2e:skill-edit-live -- --live
```

live 会另建一套全新环境，不复用准备目录。测试在任何会话/上传/发送写入前，向专属服务取得独占 claim，服务用 `execution-started.json` 排他落盘；会话创建和唯一用户 turn 另有排他标记。失败、超时或响应丢失都不移除标记、不重发、不重试 Playwright。已有浏览器 owner、结果或服务目录均拒绝复用，不能通过改输出目录重跑同一账号。先检查 `summary.json`、`server/chats`、`server/*-started.json`、`frontend-batch-*.json` 与 `playwright.json`；另一套全新目录意味着新实验，不是对未知结果的自动恢复。

成功断言保留一次批量工具调用、仅一个候选、两张新图字节和 Markdown 引用、六旧图移除、原 Skill 正文/图片摘要、栏目归属及零 Run/禁止调用不变；刷新仍保留原回复。不用本地受控测试代替真实模型成绩。原 `tests.live_skill_assets_server` 仍是旧手动探针，本入口不调用它或其固定默认数据。

## 受控全量回归

在仓库根目录的 PowerShell 中设置项目 Python，并运行受控全量回归：

```powershell
$env:CREATOROS_PYTHON = 'D:\Anaconda4.7g\envs\deepcode\python.exe'
npm --prefix web run e2e:all
```

入口先核对 `web/e2e/*.spec.ts` 和 `web/media-tests/*.spec.ts` 清单，新增或删除 spec 后需要人工分类，不能静默跳过。随后在独立目录构建前端，分组启动真实本机 HTTP API 和 Chrome：普通 Studio、Agent Plan、制作任务/阶段、compact 和素材集夹具。素材集组自动新建两条受控图片 Run，不读取正式数据库或历史图片。各组分别使用系统分配的空闲端口、独立 SQLite、文件、会话及 Playwright 结果。日志打印根目录，`summary.json` 单列每组数据来源、通过、失败、跳过、未发现的文件与原始 JSON 报告；素材集组还记录本次新建源库、产物和结果目录。部分失败时仍执行后续组。`--group compact` 等选项仅用于排查，汇总明确标为部分运行。

任一组出现跳过或重试才通过的用例时，整组不会标为完整通过；仍应查看各组数量和失败证据。此入口不包含下文明确列出的目录外 live 脚本。

入口记录源码、spec 和前端资源的开始指纹，结束时若检测到变动会标出文件并将总结果标为失败；这不能发现运行期间被改动后又恢复的文件，也不冻结依赖或浏览器版本。需要同一版本的最终结果时，先等并行代码修改结束，再从稳定工作树执行。

受控回归清除 live 开关和 `AGENT_BASELINE`，不继承真实 DeepSeek 密钥或小红书地址；Codex 用户目录指向独立根，程序指向不存在的路径，Windows 下也阻止真实 SDK 启动。生产/提炼和参考图调研使用受控夹具，不能据此宣称真实模型通过。这些设置不是操作系统网络沙箱，其他外部服务和手工执行的脚本仍需独立审查。

素材集的两个浏览器测试及一项不启动浏览器的 HTTP 检查位于 `web/media-tests`，已接入全量入口。只准备新夹具、检查汇总而不构建或启动浏览器时，可运行 `npm --prefix web run e2e:all -- --group media --prepare-only`；汇总标为 `prepared_not_run`，记录四张图片已校验、两条唯一 Run、本地生产器调用次数及后台未启动的自检，并明确列出未运行的 spec，通过数为零。下面的命令可独立执行素材集组：它先用本地确定性生产器创建两条已校验的图片 Run，再建立单独前端构建、数据库副本和结果目录；不读取正式数据库或历史图片，也不调用模型。结果只证明本地文件、HTTP 和界面流程，不代表真实模型生成质量：

```powershell
$env:CREATOROS_PYTHON = 'D:\Anaconda4.7g\envs\deepcode\python.exe'
$run = [guid]::NewGuid().ToString('N')
$fixture = Join-Path (Get-Location).Path "tmp\media-source-$run"
$env:MEDIA_TEST_ROOT = Join-Path (Get-Location).Path "tmp\media-server-$run"
$env:MEDIA_TEST_OUTPUT = Join-Path (Get-Location).Path "tmp\media-results-$run"
$env:CREATOROS_BUILD_OUT_DIR = Join-Path (Get-Location).Path "tmp\media-dist-$run"
& $env:CREATOROS_PYTHON -m tests.media_collections_fixture --root $fixture
if ($LASTEXITCODE -ne 0) { throw '素材夹具准备失败。' }
npm --prefix web run build
if ($LASTEXITCODE -ne 0) { throw '独立前端构建失败。' }
$env:MEDIA_DIST = $env:CREATOROS_BUILD_OUT_DIR
Remove-Item Env:CREATOROS_BUILD_OUT_DIR
$env:MEDIA_SOURCE_DB = Join-Path $fixture 'review.db'
$env:MEDIA_OUTPUT_ROOT = Join-Path $fixture 'outputs'
$env:MEDIA_CREATOR_ID = 'review-lab'
$env:CREATOROS_E2E_PORT = '18963' # 先确认此端口空闲；并行运行时每份不同。
Push-Location web
node node_modules/@playwright/test/cli.js test --config playwright.media.config.ts
Pop-Location
```

每次必须选择新目录。配置要求源数据库、图片目录、服务目录和结果目录位于当前工作树的 `tmp`，并拒绝已有结果目录、共享 `web` 构建和端口 8765。服务只允许集合相关写入，并校验源 Run 行与图片是否变化；如需其他隔离源，可显式更换对应路径和账号 ID。Python 服务直接调用时也必须显式提供 `--source-db`、`--output-root`、`--creator` 和 `--port`，不会默认选择历史数据库或账号。

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

`web/e2e` 下 12 个 `live-*.mjs` 是独立手动脚本，不属于 Playwright spec 清单；`web/live-images`、`live-calibration`、`live-context-focus`、`production-tasks-live` 和 `media-agent-tests` 是独立目录，均不计入以上全量结果。它们各有来源、凭证、历史工件和外部副作用前置条件，不要默认接入正式服务。
