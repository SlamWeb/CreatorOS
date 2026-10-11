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

## V3 独立续验

修正采集器后新批次，不覆盖前两轮，不从中挑成功结果。尚未得出V3通过率，执行命令：

```powershell
$env:CREATOROS_PYTHON='D:/Anaconda4.7g/envs/deepcode/python.exe'
$env:CREATOROS_WORKBENCH_BATCH='artifact-chain-v3-20261011'
$env:CREATOROS_WORKBENCH_REVISION='artifact-chain-gui-v3-20261011'
npm --prefix web run eval:workbench -- --project P03
```

同批次首次槽已占用时命令会拒绝重跑；之后需新freeze/batch并保留原记录。
