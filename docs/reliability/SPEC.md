# Reliability Baseline

## 校准测试自动化补口（2026-10-20）

- 最终统一后端23/23组通过，报告 `tmp/reliability-20261020-120122-390456/report.json`；包含辅助队列9项、调研队列13项。收尾交叉检查又补Skill恢复拒写、提交授权窗口，随后完整Skill队列19项＋校准24项联合43/43通过（118.916秒）。先前失败/超时报告全部保留，不覆盖旧Eval分数。
- 最终隔离浏览器28/28通过（162秒，0跳过/0重试），独立输出 `tmp/calibration-regressions-final-20261020`；TypeScript/build及Node18/18通过。真实Codex草稿/排队提炼/两批调研/一图试用/确认保存、真实DeepSeek只读回复亦通过，证据和用量见 `docs/skill-calibration/SPEC.md`；不计入受控测试通过率。
- 将既有零付费 runner 接入自动 CI，显式加入校准和任务收件箱；前端执行类型、构建、Node 状态测试及相关 Playwright。真实模型验证仍单独授权、单独报告，不向 CI 注入生产密钥或自动发现 live 脚本。
- 校准回归补三处：延迟的旧 GET 在新草稿保存后返回不能覆盖新差异；同一 Run 的旧 Revision 有多图时，仅评价一页仍保留整组顺序/文案/图片，不混入新版本；校准创建、试用和应用前后比较原数据库行全部列值，而不仅是行数。
- 全部夹具使用新临时 SQLite/文件根；故障时保留失败日志。不启动、重启或修改正式服务和在途任务。内容质量与真实 Codex 可用性由独立真实链路验证，不拿受控边界的通过率替代。
- 自动入口：`.github/workflows/reliability.yml` 在 push / pull_request / 手动触发时运行 Windows 上的显式零付费后端组、前端类型/构建、全部 Node 小测试和校准/收件箱/提炼 Playwright，并留存通过与失败日志。配置完成不代表远端已执行或分支保护已启用。
- 校准组目前24题，允许300秒（每题真实迁移/SQLite/文件）；其他组保留120秒。新增独立 Skill 队列、调研队列组，失败继续其余组。不会自动运行 `live_*`、支付生图或发布。
- 新数据库保护断言覆盖原 Creator/Series/Topic/Run/Revision/Attempt 每列（含JSON、时间、状态），按原ID集检查；新增“只损坏Topic.brief但行数不变”负例证明可检出。多版本用例冻结同Run旧版3图/完整故事，新版2图完全不同，只标旧版第2页后仍交付完整旧版3页，并经过试用/确认后再查原行不变。
- 新旧响应浏览器回归先保存中间稿并拖住旧GET，再保存新稿并等新差异显示，最后释放旧响应；MutationObserver检查显示过程没有回退。第一版加强脚本拦截过早导致初始diff也被拦，是测试时序问题，失败截图/Trace保留于 `tmp/calibration-regression-stale-first-failure/`，改为初始diff已加载后才注入，1/1通过；未改产品代码来适配测试。
- 本分支开发中首轮校准23/24，唯一失败为旧“忙时409”断言；按本轮用户确认的排队行为更新为第二份202/queued、第一份不取消、释放后两份ready且原件不变。原失败报告保留；最终统一runner结果由主代理补录。校准初轮浏览器5/5、加强竞态版1/1、Node18/18与typecheck/build通过，不作为真实模型质量分数。
- 开发中统一runner第一轮17/20：`tmp/reliability-20261020-112950-071550/report.json`。除上述两份旧busy断言外，迁移smoke比较0008和head的`SELECT *`元组，因新增reference_id列误判旧行被改。改为比较0008全部原列，新增列必须空；降级必须准确还原旧列，重新升级核对完整Run行，仍验证外键与metadata零漂移。该迁移smoke及新忙时校准断言单独通过。
- 后续在多个真实模型任务并行时运行零付费回归，22组中21通过，校准组300秒超时，日志已通过前20题，停在旧的重启恢复题；保留 `tmp/reliability-20261020-113650-796034/`，不通过延长时限掩盖，需低负载串行再验证。runner现将日志实时写盘，未来无需等组结束才能查看停在哪题。主代理随后带faulthandler独立重跑该恢复题与新多版本题，2/2通过（16.5秒），没有复现挂起；仍以完整串行回归为最终门槛。
- 同期28题浏览器26通过/2失败：一处等待页面跳转5秒超时，原因待独立重跑核对；另一处被并行真实探针清理同一test-results导致ENOENT。剩余证据保留 `tmp/calibration-regression-browser-overlap-20261020/`；生成helper改为先断言实际POST202再验跳转，使失败能够区分服务响应与前端路由。后续验证必须独立output目录且避免互相清理，原失败不算产品内容质量得分。
- 后续完整浏览器28/28通过；23组后端22通过、native smoke一处失败，原报告保留 `tmp/reliability-20261020-114841-682544/`。失败断言把“调用前启动0.75秒取消计时器”误当作“已有生产turn时取消”：文件初始化较慢时会在turn创建前正常返回 `codex_interrupted`，不存在可调用interrupt的turn。相邻0.3秒超时用例也有同样前提风险。只修改故障注入脚本：由生产collector真正开始消费后触发取消或推进受控时钟到deadline，同时新增预先取消不启动turn的断言；未改产品的取消/超时逻辑或放宽时限。该完整native smoke定向重跑通过，仍保留22/23原分数。

## 干净重做集成回归（2026-10-17）

- 新增 fresh领域/API 联合验证，不扩大冻结 Agent Eval：35条联合测试通过。首轮统一 runner 为17/18，报告 `tmp/reliability-20261017-132132-320741/report.json` 保留。
- 唯一失败 smoke_topic_remove 在 Web 执行器启动后直接 service.execute 播种旧工件，消费者观察到无账的外部执行日志而正确暂停；不是删题绕过或模型失败。将播种移到 Web 启动前，保留409拒绝移除后执行、图片/数据库不变等断言，不降低执行锁保护；单独重跑通过，全量另存新报告。
- 浏览器首轮全站113/117及修复情况记录在账号工作区 SPEC；真实 DeepSeek/Codex 一张图及只读图片讨论由独立探针记录，不并入零付费 runner、不改旧分数。正式服务仍有生产，不强制重启。
- 修正夹具执行顺序后，统一 runner 全量18/18通过：`tmp/reliability-20261017-133210-508074/report.json`，首轮17/18报告不覆盖。35条 fresh/工具/归属/链接联合测试再次通过；smoke_studio_api/native_artifact_web另通过。均不计为模型任务完成率。

## 批量生产增量（2026-10-17）

- 统一 runner 新增 production_queue 组，涵盖10项串行队列故障注入与5项隔离真实 HTTP/工具边界验证；原17组保留，live 探针仍只能显式启动，不被 runner 自动发现。
- 首轮18组报告 `tmp/reliability-20261017-110517-592951/report.json` 为17/18：唯一失败是旧移除迁移 smoke 把升级到 head 的结果写死为0009（此前 head 已为0011），非本轮队列执行失败。修正为读取当前迁移 head，仍保留升级/降级/重新升级、旧行不变、外键及 metadata 零漂移断言，并检查不回填生产授权；旧报告不覆盖。
- 单独存储 smoke 同类旧版本0006断言修为 migration head，失败时通过 ExitStack 关闭数据库，避免 Windows 清理错误覆盖原失败。上述属于本地回归脚本维护，不修改冻结 Agent Eval 的题目、分数或历史记录。
- 真实 DeepSeek/Codex 小批次与测试目录 Windows ACL 错误分列于 runs/web SPEC；实际生成与故障注入不混计成功率。
- 修正测试前提后，全量重跑18/18组通过，报告 `tmp/reliability-20261017-110959-403692/report.json`；没有覆盖首轮17/18报告。另浏览器9/9通过、真实 DeepSeek/Codex HTTP 两条串行生产待验收通过，后者不纳入零付费 runner。

## 本轮范围（2026-10-09）

修复真实 Skill 提炼中的 Windows 文件访问失败，并审计 SDK 执行、诊断记录、业务验收和测试之间的边界。后续按用户授权补讨论／调研收尾隔离。不换模型/SDK、不重构全部服务，不自动重试模型、不改正式任务或 Skill，不启动正式生图、调研或发布。

统一回归入口 `python -m tests.run_reliability` 当前显式列出 17 组零付费调用测试（含新增选题移除 HTTP/迁移），独立进程、每组 120 秒上限、失败后继续其他组；将日志和汇总留在忽略的 `tmp/reliability-<UTC>/`，非全通过返回非零。不得自动发现或运行 `live_*` 探针。

本轮验收要求：真实 Windows sharing lock、错误报告写失败、SDK 正常返回 interrupted、实际隔离 HTTP、网页刷新及草稿入库门槛；最后以同图真实提炼补一次接线验证。故障注入不作为真实模型成功率。执行器偶发失败须保留报告，补断言细节和失败时资源清理，不延长 lease 或删掉测试来掩盖失败。

## 审计与结果

### 已确认的结构性问题

1. **诊断与执行耦合**：ProgressWriter 的 JSON replace、usage、Trace append 及提炼活动处在 SDK 消费链中，OSError 可触发 interrupt。原 PublicEventCapture 单点 best-effort 测试不能证明整条链安全。本轮已隔离这几个诊断点，不吞权限/业务 guard 或权威 receipt 错误。
2. **失败收尾会二次失败**：提炼、改稿、试产先写 error.txt/活动、后写权威终态；审计在临时目录复现错误报告 PermissionError 后 worker 退出、终态未更新。先调整这三个入口，随后补讨论／调研：权威终态先落盘，错误/回复副本及用量/Trace I/O 不遮蔽原结果。关键 thread/批次观察器和业务安全检查仍严格失败。
3. **SDK 终态解释分散**：collector 已正确返回 interrupted，适配器却依有效 final 或文件验收。本轮在 6 个验收入口统一 completed 门槛，并将中断分类传递到宿主任务。
4. **测试没有持续基线**：审计起点有 114 个 smoke 脚本，不是没有测试；没有后端统一 runner、pytest/tox/pyproject 测试配置或 `.github` CI。实际 pytest collect-only 为 no tests collected；多数脚本须逐个执行，前端则已有 Playwright。旧测试较多覆盖正常业务/权限/幂等，缺少上述文件故障与终态组合。本轮增加明确 allowlist runner，不声称已经接上 CI 或覆盖全部历史脚本。

### 可复查验证

- 选题移除切片后的 17/17 组统一基线通过，报告 `tmp/reliability-20261009-134544-675509/report.json`；新增项覆盖实际隔离 HTTP/SQLite 的删除、归档、候选防复活、旧 Preview/生产门禁、作用域/幂等与迁移保留，不调用模型。另账号任务摘要投影补丁后独立 remove smoke 通过；相邻 8 个 smoke 与浏览器 9/9、任务摘要定向 1/1 通过，行为/截图及首轮失败见 web/storage SPEC。
- 讨论／调研切片新增 7 条 unittest（`tests.test_worker_finalization`）及 15 组统一基线通过，报告 `tmp/reliability-20261009-125005-161671/report.json`。覆盖失败与中断、preflight、成功回复副本、用量/Trace/response 拒写、权威写入拒绝、禁止工具、SDK 真正失败、坏回执、无搜索和读取不重试；接口通过实际隔离 loopback HTTP，SDK 内容及磁盘故障受控注入，零付费调用。原生产版本、digest、队列及正式数据不变。
- 把提交前的三个模块仅在内存中加载，再跑上述新用例，出现 8 个失败子例与 1 个 error；旧讨论终态仍 running、调研 preflight/关键观察器等缺口可被捕获。没有回退工作树或用真实任务做故障实验。
- 后端 14 组两次连续完整通过，末次证据 `tmp/reliability-20261009-115550-160210/report.json`（前次 `tmp/reliability-20261009-114830-590785/report.json`）；其中 7 条 unittest 包含真实 Windows 文件分享占用、隔离 HTTP，SDK 输出为受控故障注入。completed-turn smoke 使用实际固定 SDK 的 typed Notification/collector 契约验证 6 个入口。
- 首轮 `tmp/reliability-20261009-114509-495552/report.json` 为 12/14，通过数不能替代失败原因：workbench 失败是本轮过宽旧错误投影覆盖“原草稿保留”，已缩小条件并独立及全组复跑通过。另 executor 第二任务的 awaiting_approval 断言偶发失败，临时 DB cleanup 又遮盖原异常；**尚未定位其产品原因**。未改 lease、断言目标、执行器业务逻辑或删除测试；补失败状态/error_type/error_message 及 ExitStack 清理后，连续 3 次独立通过，仍须持续观察。
- 3 条定向 Playwright、typecheck/build 通过；桌面/手机截图检查与真实状态操作断言分列 `web/SPEC.md`。warning 路径使用受控 API，不混称真实模型浏览器 E2E。
- 用正式坏例的同一张图片、同样 visual 要求，在隔离目录执行一次真实 `gpt-6-sol/high` 提炼：ready、实际草稿和原图 assets 哈希一致、未生图/搜索/正式入库。证据 `tmp/skill-extraction-live-ot6pldrw/report.json`；一次成功不是可靠性统计或生成质量评分。
- 编译与 diff-check 通过。未停止正式服务；用户重启后才能加载新版本。
- 独立子 agent 只读复核本轮 diff：未见现有 DiscussionProgress 安全守卫/业务错误被吞掉，6 入口门槛均在验收前，native 在 repair 之前；权威 receipt/checkpoint 不 best-effort。此为代码审阅，不是新真实模型测试。

### 仍未完成／下一小步

- 权威 job/worker receipt/版本本身不可写必须严格失败；本轮只隔离可选诊断，不构建全盘故障下的第二套权威状态。整个目录不可写时诊断健康文件也会失败，只能在服务日志提示。固定安全警告会保留这次记录不完整的事实，即使之后 I/O 恢复。
- 讨论／调研当前已补本节列出的副本/收尾路径，不代表所有服务 I/O 都已全面统一。讨论副本失败仅记录安全服务日志；调研关键观察器的本地权威 I/O 失败仍中断，现有适配器可能归类为 codex_sdk_failed，错误分类细分尚未扩展。不会把所有 OSError 无差别吞掉。
- Observation/生产页对共享诊断 warning 的专门展示未在本轮扩散；目前新前端提示只覆盖提炼工作台。历史缺失不能补造正文。
- 将稳定的零付费基线接入 CI，再按真实坏例补跨入口恢复/取消/响应丢失矩阵。executor 偶发失败需依新断言证据定位；先不改线程数或迁移存储来掩盖问题。
- 不宣称达到某个成功率，也不把 smoke 通过当作“全部用户流程都能稳定运行”。真实使用案例先保存输入、终态、失败类型与证据，再纳入回归；内容审美/Skill 质量是另一类 Eval。

## 运行方式

```powershell
python -m tests.run_reliability
$env:CREATOROS_PYTHON = 'D:\Anaconda4.7g\envs\deepcode\python.exe'
npm --prefix web run e2e -- skill-extraction.spec.ts --grep 'diagnostic degradation|server extraction failure|uploads, creates'
```

真实提炼为显式可选、会消耗额度的探针（不由 runner 自动执行）：`python -m tests.live_skill_extraction --image <参考图绝对路径> --mode visual --instruction <用户原要求> --no-save`。真实生图/发布不属于此基线。
