# 参考作品到新选题生产：真实前端评测

本切片只跑 P03，真实原 SPA 操作，隔离 SQLite/Skill/会话/产物。参考为仓库中用户提供的六格英语图；新题为 bring/take/fetch/carry 单张四格双语漫画。没有调用测试模型或用参考图冒充产出。模型配置：DeepSeek v4-flash；提炼 Codex Sol/high；生产 Codex Luna/xhigh。每轮先提交、冻结、独占首尝试，整轮结束后才修改采集器。

## 首轮 V1：失败保留

- 批次 `artifact-chain-v1-20261011`，冻结 `artifact-chain-gui-v1-20261011`，Run `e2bfa96dd5554f9eb18a2958f9302927`，执行提交 `76aaa14`。程序/浏览器 failed，完整任务 0/1；真实 DeepSeek/生产尚未发起，不算模型生产失败。
- 原页面实际上传1次、提交提炼1次；Codex thread `01a129fd-b62f-7f02-8a4a-36ac08c7b316` 正常完成，草稿 `english-confusable-comic` 可见，Job ready/v001，参考图 assets 完整保留。刚保存 draft_ready 快照，未编辑、未入库、未建栏目、未生成新图。14表与起点相同。
- 根因在新增测试：`readProgress()` 有意只返回 `{status}`，测试却执行 `current.files.filter()`。其后报告阅读器又要求尚未发起的 Agent 有 DS 请求和最终答复，造成次级查看失败。实际 Job API 本来含 files；不能把这次误写成供应商或产品提炼失败。
- 保存原浏览器错误、HTTP、完整公开 SDK items、输入指令、草稿/资产、数据库前后与判分。原报告不倒改；阶段缺失导致程序多个检查失败，并不证明账号越权、资产丢失或全局 Skill 污染。全局 Skill 独立摘要实际完全相同。
- DS 0请求/0tokens；Codex 提炼 thread累计 input190,549（cache172,160是子集）/output3,559（reasoning1,456是子集）/total194,108，last request30,513。没有把累计和 last 相加。费用未取账单，null。6次观测GET断线均有限恢复，0次业务POST重发。
- 运行结束后原冻结校验通过；正式14表/654文件未变。读取器小修仅保留 rich task 元数据及支持未发起Agent的失败报告，未改变业务实现或题目预期；本地真实HTTP 13/13 + 严格TS通过后另冻结新轮。

## 预期完整链路与证据

| 原页面操作 | 数据预期 | 权威证据 |
|---|---|---|
| 上传→提炼→阅读全文/资产 | 仅本次Job与草稿，业务库/catalog未新增 | artifact_stages / SDK / HTTP / 真实图片加载 |
| 修改名称与双语版式→保存→刷新 | v002，保留资产，新全文和CAS摘要一致 | 草稿全文、raw文件SHA、编辑请求与刷新 |
| 显式确认入库 | 仅1份所见Skill；working与immutable版本均一致 | saved_skills、catalog前后、文件映射 |
| 组合框创建本账号栏目 | 仅1栏目，绑定刚入库的完整Skill | series、写回执、UI请求 |
| 账号聊天发一次query | 本账号新增1 Topic/Run/Revision/Attempt | 每轮模型上下文/真实工具参数结果/数据库 |
| Codex生产→原Run读图/Prompt→返回栏目 | 本轮新图，审核版全文/资产准确冻结，待批准未发布 | SDK thread/turn/回执、Checkpoint、磁盘/HTTP/网页SHA |
| 刷新→Trace→Eval展开 | 不重提、不自动批准；原始数据完整可读 | browser、network、eval_view |

原始证据位于 `data/agent-eval-workbench/<Run>/`，不上传凭证、正式内容或大图到Git。图片质量与链路/文件合法性分开；实际图像API参考参数当前不可观察，不伪称已核对传参。

## V2：提炼、编辑与入库完成，浏览器观察中断

- 批次 `artifact-chain-v2-20261011`，Run `a02b7925f104475b99b250059f8d3e1c`，冻结执行 `6d8d6a0`。完整交付仍为 0/1。原 report 只是启动占位，尚未完成评分；另存 `interruption-assessment.json`，不将占位 needs_review 当成终局成绩。
- 实际原页面完成 draft_ready → draft_edited/v002 → adopted/saved，完整修改和参考素材均保留。建栏测试停在账号下拉框：`getByLabel("归属账号", {exact:true})` 包裹 select，其标签包含 option 文案，因此无法匹配。浏览器原 trace 的 call@209、最后截图和三次阶段快照保留；确认停点后主动结束测试，不等35分钟、不重发任何付费任务。
- 实际尚未创建新栏目、Agent会话或生产 Run；真实 DS 0请求/0tokens，真实新图0。Sol提炼 thread累计 input171,719/cache151,168/output4,830/reasoning2,064/total176,549；last request32,714 单列。正式14表/654文件不变，源码结束后验冻通过。V2全局Skill起点摘要在进程内、未落盘，取消后无法完整对账，明确缺证据，不声称该维度通过。
- 结束后只改测试：限定组合框内的账号标签前缀；所有浏览器动作/导航30秒超时，模型终态仍用原长任务poll预算；开局落盘 before/catalog/global Skill hashes 防止下次取消丢失证据。新增免费原SPA/真实隔离SQLite下拉框预检，不调用模型、不记模型成绩。

## V3：真实业务链完成，原评测失败保留

批次 `artifact-chain-v3-20261011`，冻结 `artifact-chain-gui-v3-20261011`，manifest SHA256 `824fced0a587f2b311bea774825c649fa93b998473d508b4cbf7b6393b7f2bf8`；执行提交 `ab3eed9`。首槽只运行一次，期间无源码修改，结束后验冻通过。

Eval Run `eede1b8a2da84d8197102a877be0a924`，616.11秒；原报告 **failed，25通过 / 2待复核 / 1失败**。失败为末尾测试用子串选择文件，同时选中 `before.json` 和 `global_skills_before.json`。不是Codex失败或图片丢失，但首次完整评测交付仍0/1，不倒改为通过。原 `browser.completed=true`，业务链独立阅读1/1；质量/参考参数未代用户签字。

### 原页面操作与真实工具链

1. Skill页上传真实参考作品（SHA `6f1d61a0…`），选择单完整Skill，原页面一次202回执触发真Sol提炼；不是后端代发业务请求。
2. 草稿ready/v001，打开完整SKILL.md和真实asset。改名 `reusable-word-scenes`，追加中英双语、2×2、白色底衬、无总标题要求，保存为v002；刷新全文仍一致，尚未入库。
3. 显式确认v002入库；自由组合框仅放此完整Skill，给当前账号建「参考炼化辨词」，受众高中英语学习者。没有强制双Skill或静默替换。
4. 原账号浮动聊天发一次query：

> 给「参考炼化辨词」新增 bring/take/fetch/carry 选题并生产。只要一张2×2四格中英双语情境漫画，不额外标题，不发布。

5. 真DeepSeek `queue_topics`新增一条并返回真实topic/操作回执；`start_content_run`用该topic，返回同账号Run和producing；第三请求生成答复。会话创建1/发送1，复制、同会话刷新、Trace可读；刷新重提0次。
6. 新Run `d1a64648-e1fd-49f0-937a-b1e317ebad24` / revision1 / attempt1，使用审核版Skill与asset冻结快照。Luna生图→发现分隔线噪点→同turn图像编辑→单张终稿、Prompt、文案。**不是仅本地修像素，不是测试重跑或新业务Attempt。**
7. 原Run页待批准，图片1254×1254，磁盘/HTTP/浏览器SHA一致；查看Prompt全文、Skill、放大、刷新和栏目封面。未批准未发布。

### 数据库预期与实际

执行前的1条栏目/选题是另账号隔离负对照；评分检查真实完整行和资源，不根据“已完成”字样判成功。

| 对象/阶段 | 预期 | 实际 |
|---|---|---|
| ready及编辑v002 | 仅草稿，不入库/新增业务 | 两阶段saved_skills空，series1/topic1/run0 |
| 确认入库 | 仅审核版新增1 Skill | job.saved/v002，saved_skills1，业务行不变 |
| 建栏目 | 本账号新增1，绑定该Skill，不生产 | series1→2，topic1/run0；单绑定准确 |
| 入队并生产 | 各新增1 Topic/Run/Revision/Attempt | topics1→2，后三表0→1；亲缘/归属一致 |
| 状态 | Topic产出就绪、Run待批准、Attempt成功 | ready / awaiting_approval / succeeded，错误空 |
| 操作账本 | 入队成功、创建和入队回执 | pending_operations0→1(succeeded)，operation_events0→3，write_receipts0→2 |
| 生产/发布 | 状态事件，不批准或发布 | content_run_events0→4，manual_publications/publication_metrics均0 |
| 原对象 | 两账号及外账号栏目/选题完整行不变 | creators2→2，foreign完整行/文件一致 |
| 资源 | v002→入库→生产冻结逐份一致 | SKILL.md SHA `39eac0cb…`，参考图SHA `6f1d61a0…`全链一致 |

### SDK用量、图片与独立阅读

真实DS3请求/2工具：input21,814 / output311 / total22,125。Codex另计：

| 阶段 | Thread | input / cache / output / reasoning / total |
|---|---|---|
| Sol/high提炼 | `01a12a0f-b552-77c3-8ccf-ac834dcdaa15` | 194,051 / 173,312 / 4,673 / 2,401 / 198,724 |
| Luna/xhigh生产 | `01a12a11-47d2-7611-8b35-9c24309de30a` | 811,499 / 746,368 / 18,654 / 11,824 / 830,153 |

这些是thread累计，cache/reasoning为子集；last request32,951/57,113不再次相加。实际账单未采集，费用null，不当DeepSeek或图片模型费用。

首稿生图完成16:28:31，SHA `0bf0a1d3…`；同turn编辑图完成16:30:17，SHA `3e110b50…`，共2次生图/1最终图。两公开saved_path、原文件、最终交付SHA对应，原稿保留。终稿 `images/01.png` 2,223,923字节，与网页读取一致。

主线程看过原稿与最终交付：四格分别讲bring带来、take带离、fetch去取再带回、carry承托搬运；英中释义/白色标签基本达标，无总标题。仍有背景英文标语；carry中文未完整强调移动，fetch单帧不能独立证明曾去取；不能称完美教学或长期角色稳定。参考图在Skill/冻结快照中精确存在，**实际图像API参考参数不可观察**，不把文件路径或producer声明当传参证明。

原回复“已提交生产、未发布”符合当时producing，但工具前英文 `I'll add this topic…`、正文内部URL违反中文/宿主导航规范。业务链完成不等于回复交付合格；这两项保留待统一修复，不在本批边跑边改。独立阅读不改原independent_quality或用户review。

### 失败保留与免费复读

7次真实GET连接重置均有限恢复，零POST/模型重提。原paid失败完整保留。结束后仅测试文件按钮改exact，免费受控回归3/3。原报告GET-only复读12份证据、全部3次请求上下文、14表和原文；第一遍读完后fullPage截图447652px导致Chrome分配失败，另存 `readonly-recheck-v1`；单屏截图后复读1/1(16.0秒)，另存 `readonly-recheck-v2`。两次均零新模型，不是新评测样本，原failed不变。

正式运营数据14表/654文件及全局Codex Skill摘要不变。SDK另在 `.codex/generated_images/<thread>/` 新增首稿/修稿，记录了路径和摘要，**不能称所有文件变动都在Run隔离目录**；未改正式账号/Skill/会话/产物或8765服务。

仅追加assessment与复读证明索引；原43份登记证据整体SHA `54dc57ab…`、report非索引SHA `4b3cd479…`不变。完整摘要见本地 `independent_assessment.json`。

### 查看与后续

[打开P03完整Eval](http://127.0.0.1:8894/eval?case=P03&run=eede1b8a2da84d8197102a877be0a924)。保留原失败，下方显示独立业务/回复/图片阅读。关闭后根目录重开，只读、不重跑：

```powershell
$env:DATABASE_URL='sqlite:///D:/CreatorOS/data/agent-eval-workbench/viewer-bootstrap.db'
& 'D:/Anaconda4.7g/envs/deepcode/python.exe' -m creatoros.evaluation.workbench view --run-id eede1b8a2da84d8197102a877be0a924 --port 8894
```

已有首次槽拒绝重跑。只有明确做新付费回归时，先另freeze/batch：

```powershell
$env:CREATOROS_PYTHON='D:/Anaconda4.7g/envs/deepcode/python.exe'
$env:DATABASE_URL='sqlite:///D:/CreatorOS/data/creatoros.db'
$env:CREATOROS_WORKBENCH_BATCH='artifact-chain-'+[guid]::NewGuid().ToString('N')
$env:CREATOROS_WORKBENCH_REVISION=$env:CREATOROS_WORKBENCH_BATCH
& $env:CREATOROS_PYTHON -m creatoros.evaluation.workbench freeze --revision $env:CREATOROS_WORKBENCH_REVISION
& $env:CREATOROS_PYTHON -m creatoros.evaluation.batch guard --batch-id $env:CREATOROS_WORKBENCH_BATCH --snapshot before
npm --prefix web run eval:workbench -- --project P03
& $env:CREATOROS_PYTHON -m creatoros.evaluation.batch guard --batch-id $env:CREATOROS_WORKBENCH_BATCH --snapshot after
```

当前17个不同场景/18槽，50规划还有33个未接线。下一切片P05真实完成后离线重验（恢复阶段零SDK/生图、原图hash不变），再P06看图讨论/P04显式返工隔离；先接journey/fixture/grader并冻结，再运行，不提供未实现project的假命令。中文进度/内部URL、工作台oracle的时间线映射是已记缺口，后续统一修复并全量回归。

后续进展：P05已独立冻结并完成首轮真实GUI恢复，详见 [离线恢复报告](OFFLINE-RECOVERY-2026-10-11.md)。当前增至18个不同场景/19槽；本P03历史failed与回复瑕疵不改写。
