# 工作台四场景真实 GUI 回归：独立证据阅读

这是 Codex 对真实执行证据和本轮图片的独立阅读，不是用户签署，不改原程序成绩。只有新增 assessment sidecar、S13 六资源对账及 report 的 evidence_files 索引；原题、冻结源码、请求、回答、数据库快照、source_manifest 与评分均保留。

## 执行口径

- 批次 `workbench-v2-20261011`，冻结 `workbench-gui-v2-20261011`，执行源码 `3955c22a4e9dc9ec4f875064574e10a67e4687de`。
- A14/P01/P02 从原账号 GUI 输入一次 query，调用真实 DeepSeek；S13 从原 Skill 组合框直调真实 Codex。均为独立 SQLite/目录/端口，没有 Mock LLM 或受控 Producer。
- 付费测试 `retries=0`，每槽首次执行；仅 GET 观察遇到 socket hang up 时有限恢复，不重发 POST、不重新发模型任务。P02 同一生产 turn 内重画第2页一次，原事件保留，不冒充测试重跑或首次图完美。
- 原程序结果为 **3 needs_review / 1 failed**；Playwright suite 为 3 passed / 1 failed。程序待复核不等于用户验收通过；S13 的失败是确认的路径判分假阴性，不回写成 passed。
- 两个生产任务交付 **4 张真实新图**（1+3）；实际成功生图事件 **5 次**（1+4），其中1张为弃用稿。没有批准或发布。
- 主线程已验证本轮冻结来源，正式 `formal-after/guard` 对账为14表、654文件 unchanged；本文件逐例检查的是隔离世界。没有为审计触发新模型或生图。
- 下文时间为 UTC；本地日期2026-10-11与原记录中的UTC 2026-10-10不矛盾。

| 场景 | Eval Run | 原 auto_status | 独立事实 |
| --- | --- | --- | --- |
| A14 | `c5a48af48614439c87e69933073c8e48` | needs_review | 单完整 Skill 栏目正确创建，宿主入口真实点击、详情与绑定全文、刷新完成 |
| S13 | `8553b3d604ca47aab4a39b4656c6e86e` | failed | 融合、资源保留、编辑与刷新完成；原采集器错误读取持久版本目录 |
| P01 | `491483f35f9f45eba67efc7af97a4aba` | needs_review | 单 Skill 一图真实生成、前端加载验收链完成，待批准未发布 |
| P02 | `9ec24dbcc77f401091d598a825dd0fd6` | needs_review | 双 Skill 三图真实生成、前端逐页验收链完成，第2页有一次实际返工 |

原证据根为 `data/agent-eval-workbench/<Eval Run>/`；真实浏览器 Trace 位于 `web/tmp/live-workbench/workbench-v2-20261011/*-<case>/trace.zip`。四例均有 committed 的 view_confirmation.json，原 Eval 实际读取完整原文和全部14表，不仅打开页面或读非空占位。

## 如何查看、续验

[打开本轮真实三页生产的 Eval](http://127.0.0.1:8894/eval?case=P02&run=9ec24dbcc77f401091d598a825dd0fd6)。这是独立 GET-only 查看器，左侧可切换本轮四题，历史运行保留首轮失败；不会调用模型、恢复后台任务或修改正式数据。原回答内的临时生产地址属于历史原文，执行服务退出后不保证仍能访问；当前证据文件用本报告已登记的文件入口展开。

查看器关闭后，在仓库根目录运行（仅查看，不重跑）：

```powershell
$env:DATABASE_URL='sqlite:///D:/CreatorOS/data/agent-eval-workbench/viewer-bootstrap.db'
& 'D:/Anaconda4.7g/envs/deepcode/python.exe' -m creatoros.evaluation.workbench view --run-id 9ec24dbcc77f401091d598a825dd0fd6 --port 8894
```

若要付费重跑现有四题，先确认修复已提交并使用全新批次/冻结；不复用或改写旧 manifest：

```powershell
$env:CREATOROS_PYTHON='D:/Anaconda4.7g/envs/deepcode/python.exe'
$env:DATABASE_URL='sqlite:///D:/CreatorOS/data/creatoros.db'
$env:CREATOROS_WORKBENCH_BATCH='workbench-'+[guid]::NewGuid().ToString('N')
$env:CREATOROS_WORKBENCH_REVISION=$env:CREATOROS_WORKBENCH_BATCH
& $env:CREATOROS_PYTHON -m creatoros.evaluation.workbench freeze --revision $env:CREATOROS_WORKBENCH_REVISION
& $env:CREATOROS_PYTHON -m creatoros.evaluation.batch guard --batch-id $env:CREATOROS_WORKBENCH_BATCH --snapshot before
npm --prefix web run eval:workbench
& $env:CREATOROS_PYTHON -m creatoros.evaluation.batch guard --batch-id $env:CREATOROS_WORKBENCH_BATCH --snapshot after
```

命令当前只运行 A14/S13/P01/P02，不是运行50题；34个未接线场景必须先补 journey/fixture/grader 再冻结。下一真实切片优先作品提炼→用户编辑→入库→新选题生产（P03），然后补融合冲突、恢复和只读图片讨论。本轮全部首次槽已结束，没有等待中的付费任务。

收尾只读查看另发现 assessment 中 Windows 本地路径被 Markdown 安全转换清空的问题。后处理 UI 只将当前报告登记的同 Run 文件映射到已有展开/定位入口，拒绝其他 Run、未登记文件和任意路径；不会修改历史 Markdown、评分、query 或模型证据。P02 已从阅读正文实际打开 sdk.json，显示对应 composition thread/turn 和原公开事件；这种展示复核不是新的模型样本或追溯修分。

链接/拒绝/键盘/刷新零写的普通 UI 回归15/15通过，typecheck/build通过；受控 Markdown 用于展示测试，不替代上述四真实任务。桌面/390px截图已检查。收尾只读快照另存 `data/agent-eval/batches/workbench-closure-20261011/formal-before.json`，与本工作台起点14表/654文件逐项一致；正式8765进程未重启。当前通用执行链对直调SDK的S13没有外层DS请求，SDK与融合任务使用原始证据/独立阅读查看；工作台oracle亦可展开原文，尚未完整映射成逐步期望标签，这属于展示待完善，不是任务再次失败。

## A14：完整单 Skill 创建栏目

### Query → 工具 → 落盘

> 用「四格辨词」这个完整 Skill 建个栏目「每日辨词」，面向高中英语学习者。

原账号 GUI 发送 → DeepSeek `list_producer_skills` → `compose_series` → 真实 SQLite 创建回执 → 最终完整答复。3次真实 DS 请求、2次模型工具，无 Codex 或生图。

预期只新增当前账号栏目和创建回执。实际 `series` 1→2、`write_receipts` 0→1：

- 栏目 `series-9844d8986bdf4f90bd39`，归属 `creator-workbench-a`，名称每日辨词，受众高中英语学习者。
- 完整单 Skill `four-panel-vocabulary--e434c692ae7bc24c`；mind_skill_id/production_skill_id为空，没有误存为双绑定。
- create_series 回执资源就是该新栏目；没有 Topic、Run、Revision、Attempt 或发布增量。源件/原记录未改。

### GUI 与阅读结论

工具成功 DTO 返回实际 series_id 与 `/series/series-9844d8986bdf4f90bd39`。GUI 点击真实宿主入口，到 `/?series=series-9844d8986bdf4f90bd39`；核对栏目标题、完整 Skill 卡片原文，刷新仍是同一栏目。完整答复、复制、同会话刷新、Trace、原 Eval 全文均完成；刷新没有新的提交。

最终答复只说已创建、受众和完整 Skill，符合数据库；没有虚报已调研或生产。原机械检查全部通过，内容阅读作为独立记录，未代用户签署。

[完整阅读](D:/CreatorOS/data/agent-eval-workbench/c5a48af48614439c87e69933073c8e48/assessment.md) · [工具账本](D:/CreatorOS/data/agent-eval-workbench/c5a48af48614439c87e69933073c8e48/messages.json) · [DB前](D:/CreatorOS/data/agent-eval-workbench/c5a48af48614439c87e69933073c8e48/before.json) · [DB后](D:/CreatorOS/data/agent-eval-workbench/c5a48af48614439c87e69933073c8e48/after.json) · [GUI/刷新](D:/CreatorOS/data/agent-eval-workbench/c5a48af48614439c87e69933073c8e48/browser.json)

## S13：Codex 融合、源资源保留与草稿编辑

### Query → 原组合框 → SDK

> 把双语辨词方法与浅蓝漫画融合，先给草稿，保留来源资源，不直接入库。

原 Skill 页面查看两份原文并加入组合框 → 一次真实融合 POST → Codex 读取来源/写草稿 → ready → 查看两图/全文 → 编辑保存 → 刷新复原。无外层 DS 请求是该工具的正常分支。

- Job `8c351884e82e3b97c1369e55fe9677f8e53fdf30e23b18a4afd9fb5948612f26`，最终 ready/error=null、revision=2、draft_directory=versions/v002、saved_skills=[]。
- SDK thread `01a127fb-54f4-7e12-8161-c2d50f59111b`；turn `01a127fb-59c7-7130-a2ec-d16380ede565`。
- 111条公开 SDK 事件；turn/completed `22:44:33.972810Z`、capture/finished `22:44:33.978810Z` 为同一thread/turn且 completed、capture_write_failed=false。没有生图。

预期不入库、不建栏目或选题、不生产。实际14表 before==after，原库登记和源文件均未改；新增文件限本 job 的来源副本、草稿和版本。

### GUI 编辑与实际资源

两命名空间 reference.png 均在网页真实加载为80×80；完整 SKILL.md 被读取。用户动作追加 `保留双语解释，不额外加总标题。`，保存为v002，刷新后全文精确保持这次追加，没有第二次融合POST。原Eval也读取了全链路和14表。

**原 source_resources failed 是判分器假阴性，不是业务资源丢失。**

`creatoros/evaluation/workbench.py:662` 将 worker 草稿目录 `MODE_FOLDERS["single"][0] == "skill"` 用作持久版本目录；业务 `creatoros/integrations/skill_extraction.py:545` 实际按 `skill.role` 写入 `legacy_end_to_end`。因此原 source_manifest 查看的是不存在的 `versions/v002/skill/assets/fusion-sources/...`，正确位置是 `versions/v002/legacy_end_to_end/assets/fusion-sources/...`。

六资源的 **注册源before/after、实际源文件、job来源副本、v001、v002** 摘要逐项相同：

| 来源命名空间 | 文件 | 实际各阶段一致SHA256 |
| --- | --- | --- |
| source-01-bilingual-word-method--1ad82f3c45c41083 | assets/reference.png | `9dbb4622e68fecce95a28a7aa489a419d8b7c9fffdc207b73aa4e04aef3271cf` |
| 同上 | scripts/example.py | `02d33601208dcc88f5dfe68a109ec6cb6f9e078409168213cebb7b4dc5899dad` |
| 同上 | SOURCE-SKILL.md | `17d9b41f8b8f4b4c38aa4fc44958917a8be0fcae110a2f8c2d5114000f68bbc2` |
| source-02-light-blue-comic--d77a7687ee8916f3 | assets/reference.png | `98072c5db56ac88448647a1458ceb901c934b049f61c31f87e04ec8b9811eee5` |
| 同上 | scripts/example.py | `02d33601208dcc88f5dfe68a109ec6cb6f9e078409168213cebb7b4dc5899dad` |
| 同上 | SOURCE-SKILL.md | `0f7a429a7e79c3beaf7f24545172bd0b9fa6577e8fb3de22cdc632d9174237d1` |

独立阅读融合稿保留词义核实、日常场景、英中解释和浅蓝表达，说明配色图不是固定人物/版式，示例脚本仅溯源；冲突请用户确认。该草稿未试产，文件/编辑通过不证明后续内容质量稳定。原 auto_status/checks/source_manifest 保留，新增 source_resource_assessment.json 只提供实际资源对账。

[独立阅读](D:/CreatorOS/data/agent-eval-workbench/8553b3d604ca47aab4a39b4656c6e86e/assessment.md) · [原失败源清单](D:/CreatorOS/data/agent-eval-workbench/8553b3d604ca47aab4a39b4656c6e86e/source_manifest.json) · [六资源实际对账](D:/CreatorOS/data/agent-eval-workbench/8553b3d604ca47aab4a39b4656c6e86e/source_resource_assessment.json) · [SDK公开记录](D:/CreatorOS/data/agent-eval-workbench/8553b3d604ca47aab4a39b4656c6e86e/sdk.json) · [草稿/GUI](D:/CreatorOS/data/agent-eval-workbench/8553b3d604ca47aab4a39b4656c6e86e/browser.json)

## P01：单完整 Skill 一图生产

### Query → DS工具 → SDK → DB终态

> 给「每日辨词」新增 bring/take/fetch/carry 选题并生产，只要一张四格中英双语漫画，不额外标题，不发布。

原账号 GUI → `queue_topics` → 用真实回执topic_id调 `start_content_run` → 点击真实宿主Run入口 → 等真实Codex生产 → 逐图/Prompt/冻结Skill/刷新/封面 → 原Eval完整读取。3次 DS请求、2次模型工具。

预期一条Topic/Run/Revision/Attempt、一张新图、待验收未发布。实际：

- Topic `topic-6eb13cd74d23499ab357`；Run `0f0b9e5f-7bff-4eba-915f-2e98ea38ae73`。
- Revision `83471ec8-837b-4dcd-bbe4-391d52f02fe3`；Attempt `162dd012-126f-4e6e-bb94-c976ed5e9df4`。
- 均归属当前creator/每日辨词；queue回执与pending_operation指向同栏目/Topic，内容含完整四词和用户要求。
- Run awaiting_approval，Attempt succeeded，approved_at=null，manual_publications/publication_metrics没有新增；原文件、旧行和外账号不变。
- SDK thread `01a127fb-b8fc-7112-9024-31f54cbb2427`，turn `01a127fb-bb5c-7970-b707-e0351f08628b`；90条公开事件，最后turn/capture在 `22:46:33Z` completed，回执/checkpoint/业务终态一致。

### 真图、网页与独立阅读

一次真实imageGeneration completed；SDK saved_path文件、业务图片、HTTP/GUI及栏目封面均为SHA `3eb2d545a1ad41e24c7382e0cae938a81cbdbd8f8caa6b7f86d9a1d12621f665`，1122×1402。完整Prompt与交付原文相等，冻结Skill全文与网页绑定原文一致。放大、返回、刷新以及原Eval14表/原文完成，刷新没有新提交。

外层最终答复“已提交、生产中、未发布”符合工具返回的时点，未提前虚报图片完成；网页随后正确显示待批准。答复仍含原始Run URL/ID，是文案收敛瑕疵，不是生产状态矛盾。

主线程实际看图：2×2四格，四词/音标/英文例句及英中释义清楚，无额外总标题；bring向说话者、take向外、fetch取回、carry负重与文字基本对应，白底高对比。本样本基本教学/视觉目标达标；不验证考试频率、人物精确身份或长期一致性。

[独立阅读](D:/CreatorOS/data/agent-eval-workbench/491483f35f9f45eba67efc7af97a4aba/assessment.md) · [图/Prompt/冻结源证据](D:/CreatorOS/data/agent-eval-workbench/491483f35f9f45eba67efc7af97a4aba/outputs.json) · [SDK](D:/CreatorOS/data/agent-eval-workbench/491483f35f9f45eba67efc7af97a4aba/sdk.json) · [GUI](D:/CreatorOS/data/agent-eval-workbench/491483f35f9f45eba67efc7af97a4aba/browser.json) · [真实最终图](D:/CreatorOS/data/agent-eval-workbench/491483f35f9f45eba67efc7af97a4aba/fixture/outputs/creator-workbench-a/series-workbench-vocabulary/0f0b9e5f-7bff-4eba-915f-2e98ea38ae73/revision-001/attempt-001/images/01.png)

## P02：双 Skill 三页连续生产

### Query → DS工具 → 组合协调 → 生产

> 给「问题教学×小白图解」栏目新增选题：一次带幂等键的订单写入请求如何提交、重试和避免重复副作用。面向零基础读者，做三张连续图并生产，不发布。

原账号GUI → DeepSeek `queue_topics` → `start_content_run` → 单Codex thread内composition turn协调 → 同thread production turn产三页 → 原前端逐页翻图/放大/Prompt/刷新/封面 → 原Eval全链。3次DS请求、2次模型工具。

预期双绑定正确、只一条业务Run、三张有序新图，待批准未发布。实际：

- 双绑定 `problem-teaching--518dcf06a7cdf93a` × `xiaobai-diagram--1c9fbabd7c2d3428`。两份SKILL.md各自冻结，网页绑定全文分别核对；配色reference.png是80×80合成palette，不是角色原图。
- Topic `topic-46b83864abea4821a61c`；Run `fddeab68-2b32-439d-b709-cae9e5f9fd89`。
- Revision `dd866579-6e5d-4eb7-8594-683b3dfdbe14`；Attempt `a00e736a-3b92-4380-9592-c7a085ea2543`。当前creator/目标栏目、回执、输入主题和子记录关联准确。
- SDK thread `01a127fd-ae2f-7b02-8b3b-af8699e31fe2`；composition turn `01a127fd-b323-7cc3-a77a-875e50665dc0`，production turn `01a127fd-e972-7611-a688-763d998add16`。127条公开事件，两turn和capture均completed、采集写入成功，worker receipt/checkpoint对应。
- Run awaiting_approval、Attempt succeeded、card_count=3、approved_at=null，零发布；原件及外账号不变。

### 逐图交付与一次真实返工

三最终图均1122×1402；SDK原生成文件、磁盘、HTTP/GUI和Prompt逐项核对，封面是第1张，刷新零再提交。

| 最终顺序 | SHA256 |
| --- | --- |
| 1 | `f8242645565abd83387fc99fcc736ca9fce70f197984b654e6d8543e04cd55ec` |
| 2 | `9bcb2c03d238e0cc19f8bd43b45b2345984d53b7cd8c782a579571d206610e06` |
| 3 | `2c00e2cd75ee7a2dea9cb840551362d628a74338e2290da19ce42aa1cb995503` |

SDK有4次成功生图：第2页首版 `74244d16d167fe15bbf7adc280e0f736570feef0733503fffe05de3f342114e5` 被弃用。公开进度消息说明噪点问题，随后在同一production turn简化版式、重画并更新delivery。主线程查看弃用图确认透明/黑色噪斑损伤文字，清晰版已消除；不是套件隐藏重试或覆盖失败轨迹。

主线程逐页看最终图：超时≠失败 → 同键同请求复用结果 → 本库协调保存/异内容拒绝/外部去重/有效期/非全链路恰好一次，因果连续，字清楚，奶白蓝围巾猫系列一致。基本目标达标；“协调保存”没有展开事务实现，不能宣称完整教学或并发算法均已验证。

[独立阅读](D:/CreatorOS/data/agent-eval-workbench/9ec24dbcc77f401091d598a825dd0fd6/assessment.md) · [三图/Prompt/双冻结源](D:/CreatorOS/data/agent-eval-workbench/9ec24dbcc77f401091d598a825dd0fd6/outputs.json) · [SDK含弃用稿](D:/CreatorOS/data/agent-eval-workbench/9ec24dbcc77f401091d598a825dd0fd6/sdk.json) · [逐图/刷新/原Eval](D:/CreatorOS/data/agent-eval-workbench/9ec24dbcc77f401091d598a825dd0fd6/browser.json)

## GET恢复、usage与证据边界

S13在 `22:43:43.055Z` 第一次观察socket hang up，第二次GET于 `22:43:43.165Z` 恢复running；P01的两次观察在 `22:45:00.308Z`、`22:46:37.397Z` 断开，分别于 `.433Z`、`.515Z` 第二次GET恢复producing/awaiting_approval。每次失败与恢复都在browser.observation_retries留证。A14/P02没有此恢复事件；全轮零POST/模型重提。

| 场景 | DS请求/工具 | DS input/output/total tokens | SDK input/cached/output/reasoning tokens | 整条GUI耗时秒 |
| --- | --- | --- | --- | --- |
| A14 | 3 / 2 | 21,346 / 201 / 21,547 | 不适用 | 38.375 |
| S13 | 0 / 0 | null（直调SDK） | 351,098 / 326,272 / 8,212 / 4,118 | 162.266 |
| P01 | 3 / 2 | 21,754 / 321 / 22,075 | 469,308 / 420,608 / 14,303 / 6,684 | 274.516 |
| P02 | 3 / 2 | 22,136 / 296 / 22,432 | 500,627 / 420,736 / 19,315 / 10,793 | 489.875 |

DS合计9请求、6模型工具、66,054 tokens。SDK按各保存usage文件分列；cached input是input子项，reasoning是output统计子项，不能重复相加；不同供应商计数不能混成一个成本。**实际账单费用null**，不估填人民币。

- 原生交付 `reference_assets` 表示worker声明的实际使用冻结Skill文件，允许SKILL.md，不限图片。P02列两份SKILL.md合法，但该清单不能证明图像模型接收了参考图。
- 公开SDK不含 referenced_image_paths。可验证冻结资产、imageView查看、worker资源声明及生成saved_path/SHA；实际图像API参考参数仍 **not_observable**，不是自动通过，也不能据此推断未使用参考图。
- 网页完整Prompt与生产者交付原文一致；`prompt_provenance=producer_reported_not_image_service_verified`，未伪称掌握图像服务内部完整请求。
- 四个assessment的原报告核心摘要（排除唯一允许追加的evidence_files）与全部原证据文件摘要复核不变，S13原source_manifest失败留存。没有保存用户review或追溯改分。
- 本轮与首轮改变了采集协议/链接回执/剪贴板检查，且存在判分假阴性；展示明确事实与分维度结果，**不写严格成功率提升**。这四个生产/融合样本也不代表50场景都已接线，更不代表发布/反馈运营闭环已经完成。
