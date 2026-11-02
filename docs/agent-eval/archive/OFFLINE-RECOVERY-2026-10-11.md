# P05：真实前端生产与离线恢复

本轮原 SPA → 真 Codex 生图 → 声明的宿主交付故障 → 网页显式恢复 → 同图重新交付完成。浏览器首尝试 **1/1**；原自动检查 **15 passed / 1 needs_review / 0 failed**，整题仍为 `needs_review`。没有替用户批准、发布或将内容质量待复核改成通过。

前一切片的作品提炼→编辑→入库→新题材生产已真实完成，原 Eval 读取失败仍保留，见 [P03 报告](ARTIFACT-CHAIN-2026-10-11.md)。本轮不重复那次付费生产，新增 P05 恢复场景。

## 冻结与运行

- 批次 `offline-recovery-v1-20261011`，协议 `offline-recovery-gui-v1-20261011`，源码 `9a7a8f02040dc287b5332170cb3af7f9a9766523`。
- [冻结清单](manifests/offline-recovery-gui-v1-20261011.json) SHA256：`c030fd4ec8f7404b6afd3328c93d04dc7266aebbdc369e023510c87eda5735a0`。
- Eval Run `5bfb85f661504de0bfed17ce88234ee8`；首尝试一次、Playwright retries=0、原始失败与恢复完整保留；收费运行期间未改源码，结束后验冻通过。
- 实际模型 `gpt-6-luna/xhigh`，`openai-codex 0.157.1`；此次用户从栏目网页手动添加/生产，**不经过 DeepSeek 聊天，DS 0 请求**，不继承 P03 的 DS 轨迹。
- 免费接线验证：23 个本地判分/结构/故障边界测试、17 个覆盖定义检查、原 SPA 免费定位预检1/1、strict TS/build通过。这些不算付费模型题目或成功率。

## 从输入到恢复的完整链路

北京时间 2026-10-11，当前隔离账号“词汇实验室”，栏目“每日辨词”。实际输入：

> bring/take/fetch/carry：一张四格中英双语情境漫画，不额外标题，不发布

| 时间/步骤 | 真实动作与结果 | 本步评估 |
|---|---|---|
| 19:00:22 网页添加、生产 | 原 SPA 写选题，创建 Run，再启动；201/201/202。绑定 Skill 的当前文件冻结到输入快照 | 一次用户请求只产生一份业务任务，未跨账号 |
| 19:00:23–19:05:20 真 SDK | 新 thread 调研词义/安排四格、生成1张图、读图并交付；保存 Prompt、原图、checkpoint、完成回执及全量公开 item | 真实产物和终态齐全，不用种子图或 Mock 代替 |
| 19:05:20.471 声明故障 | 真 Producer 已返回且本地产物校验成功后，控制器抛一次宿主 `native_delivery_failed`，页面可见失败，Attempt1完整保留 | 故障位置明确；不是供应商随机失败，也不是生图失败 |
| 19:05:22 网页点恢复 | 同 Run、同 Revision 新建 Attempt2；execute 请求202，`expected_version=4`，离线复用原文件，375ms完成 | 没有再开启 SDK client/native execute，没有新生图，不伪造新 turn |
| 19:05:23 后读结果 | 回到待批准；实际浏览图片、完整 Prompt、Skill、放大、刷新、栏目封面；原 Eval 展开11份原文和14表对照 | 图片加载及原始证据读取通过；通用 Eval 摘要仍有展示缺口，见下文 |

一次恢复仍对应同一篇内容、同一个版本：

```text
Run 7a1c8038-34b6-494e-bcac-de33c9d621d0
└─ Revision f3e1d3eb-f0f6-4f18-9897-54e534c9b3d6（第1版）
   ├─ Attempt ba8d8eec-6cf2-4324-9a72-f0329764499b：宿主失败
   └─ Attempt c8023928-7ef8-451e-9e63-397dc77c3202：离线恢复成功
```

最终 Run `awaiting_approval` / version8 / approved_revision=null。原失败 Attempt 完整行不变；其18份文件的**路径集合与 SHA**均不变。新恢复 Attempt 不复制原 SDK 回执，没有第二个完成 turn；完成依据引用原 Attempt 的最后 `turn/completed`、`capture/finished`、回执及同 thread/同图摘要。

唯一 SDK thread：`01a12a9f-0046-7cb3-be59-f3799eda3d99`；turn：`01a12a9f-04d8-7b53-993a-6db6654340c4`。两次 producer 实例不代表两次 SDK 调用；初次 native execute/client 各1次，恢复各0次，前后公开 SDK 事件相同。

## 数据库预期与实际

| 持久化对象 | 预期增量 | 实际 | 结论 |
|---|---|---|---|
| topics | 当前账号新增1，原外账号选题不改 | 1→2；原完整行不变 | 符合 |
| content_runs / content_revisions | 各1，恢复不能再建内容版本 | 0→1 / 0→1；同Run/Revision | 符合 |
| content_attempts | 首次失败保留，恢复新增1 | 0→2；failed/succeeded，旧行不改 | 符合 |
| content_run_events | 创建、启动、失败、恢复、产出、验收 | 0→6；顺序与终态一致 | 符合 |
| pending_operations / operation_events / write_receipts | 仅一次真实入队 | 0→1 / 0→3 / 0→1 | 符合 |
| creators / series | 不新增、不转移、不修改原实体 | 2→2 / 2→2；完整行一致 | 符合 |
| manual_publications / publication_metrics | 0，不发布不回填 | 0→0 / 0→0 | 符合 |
| topic_removals / alembic_version | 无删除、无迁移 | 0→0 / 1→1 | 符合 |

GUI业务写请求共4次：添加、创建Run、启动、显式恢复；刷新后重提0。一次观察GET的socket hang up被有限恢复，零POST重放、零模型重提。受管夹具文件7→39，新增32份只在 outputs；原 Skill 文件及原有业务文件不变。

正式运营数据库14表、654份受管文件，批次起点/终点逐项相同。正式8765服务PID32196未重启。P05未登记全局Codex Skill哈希起点，故 `global_skills_unchanged=null`，不冒充已验证。SDK按原机制在用户 `.codex/generated_images/<thread>/` 新增1图（夹具外），路径/摘要留证；不能说所有文件写入都在夹具内。

## 真实图片与用量

SDK原文件、首次Attempt、恢复产物、HTTP响应、刷新封面的同一图片 SHA256：

`df9553c3d7eb02d44d7215452fbc4e122a01208f3b5f37a8c1c156b899ead185`

图片1122×1402，2,350,243字节；完成生图1次，新交付1图，恢复阶段生图0次。参考图在本地 Skill/冻结资产中保存准确，但公开 SDK item 不提供图像 API 的实际参考参数，继续记 `not_observable`。

| 用量范围 | input | cached input | output | reasoning output | total |
|---|---:|---:|---:|---:|---:|
| DeepSeek 外层 | 0 | — | 0 | — | 0 |
| 初次 Codex thread累计 | 440,831 | 393,472 | 11,934 | 7,175 | 452,765 |
| 离线恢复新增 | 0 | 0 | 0 | 0 | 0 |

cache/reasoning为对应列子集；last request53,023不再相加。初次Attempt297,437ms，恢复Attempt375ms，整条浏览器收集311.969秒；不是“整次生产零成本”。实际账单未采集，费用null。

主线程看过真实图，另做只读恢复审查：2×2、四词英中释义、浅色文字底衬、无全局标题基本满足；fetch用去/取/返回连续场景表达，carry有负重搬运场景。一个样本不能证明长期角色/字体稳定，没有替用户批准，也没有将独立阅读倒灌为原质量判分。

## 原评分和展示缺口都保留

原 `auto_status=needs_review`，15项通过/1项质量待复核；`review=null`。浏览器1/1与运行结构通过并不等于用户验收整题成功，不将15/16写成通用任务成功率。

原 Eval 的通用摘要只认识账号聊天请求，因此纯Codex题仍显示“模型请求未完整记录”“未识别query/最终回复”，用量只显示DS的0，预期oracle也未映射到摘要。**原始SDK证据完整且可展开，这属于展示映射缺口，不是SDK缺日志。** 原文件读取通过与摘要呈现不正确必须分别记录；本轮未改应用前端来掩盖问题。

付费结束后仅追加 `reading-summary.md` 和 `independent_assessment.json` 及2个证据索引。原40份登记文件集合摘要不变 `c9edb895…319c0`，report非索引摘要不变 `523fe6bd…836b`。现42份原文可读，computer-use已实际打开并展开新增全文；原判分/用户review没改。

## 查看、续跑与下一步

[P05完整Eval](http://127.0.0.1:8897/eval?case=P05&run=5bfb85f661504de0bfed17ce88234ee8) 已有只读查看器运行。页面“浏览文件”里有新增完整链路/用量说明，截图和 Playwright trace在 `web/tmp/live-workbench/offline-recovery-v1-20261011/`。关闭后从根目录重开，只读不产生模型调用：

```powershell
$env:DATABASE_URL='sqlite:///D:/CreatorOS/data/agent-eval-workbench/viewer-bootstrap.db'
& 'D:/Anaconda4.7g/envs/deepcode/python.exe' -m creatoros.evaluation.workbench view --run-id 5bfb85f661504de0bfed17ce88234ee8 --port 8897
```

首尝试不能原批重跑。仅在确需新付费回归时，新冻结/新批次：

```powershell
$env:CREATOROS_PYTHON='D:/Anaconda4.7g/envs/deepcode/python.exe'
$env:DATABASE_URL='sqlite:///D:/CreatorOS/data/creatoros.db'
$env:CREATOROS_WORKBENCH_BATCH='offline-recovery-'+[guid]::NewGuid().ToString('N')
$env:CREATOROS_WORKBENCH_REVISION=$env:CREATOROS_WORKBENCH_BATCH
& $env:CREATOROS_PYTHON -m creatoros.evaluation.workbench freeze --revision $env:CREATOROS_WORKBENCH_REVISION
& $env:CREATOROS_PYTHON -m creatoros.evaluation.batch guard --batch-id $env:CREATOROS_WORKBENCH_BATCH --snapshot before
npm --prefix web run eval:workbench -- --project P05
& $env:CREATOROS_PYTHON -m creatoros.evaluation.batch guard --batch-id $env:CREATOROS_WORKBENCH_BATCH --snapshot after
```

当前18个不同场景/19执行槽已接线，50规划还有32个未接线，不能称50题全部完成。下一小步优先Codex专用时间线/数据库预期/分范围用量投影（不改历史分），再接P06看图讨论和P04显式返工；它们尚无可运行project，先接线/判分负例/冻结，不给假续跑命令。统一业务修复仍等预定基线结束后进行，新模型轮次单独留账。
