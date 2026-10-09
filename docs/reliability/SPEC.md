# Reliability Baseline

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
