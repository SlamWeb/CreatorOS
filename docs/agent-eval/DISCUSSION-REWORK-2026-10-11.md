# 看图讨论与返工：真实前端评测

## V1 首尝试：0/2 完整交付，原失败保留

冻结 `discussion-rework-gui-v1-20261011`，源码 `9e80f9f`，运行提交 `f5022f4`；批次 `discussion-rework-v1-20261011`。两题均 retries=0，整轮结束后验冻通过。正式14表/654受管文件完全不变，未改原8765服务。用真实 SQLite 隔离世界和原 SPA；既有图来自已验证的真实 P05，仅作讨论/返工前置，不冒充本轮生成。

### P06：目标不明确，未发生 Codex 讨论

记录 `4b32fc7566b1488c8c2d3a27256896ed`，261.469秒。步骤：打开账号聊天 → 发送“请 Codex 看我选定这一版四格图，分析可读性；先不要修改。” → DeepSeek完整答复 → 打开该轮Trace → 脚本等待不存在的讨论卡片240秒超时 → 保存原失败 → 原Eval文件读取再失败。

请求中没有 Run、作品标题或版本；页面选择也没有传入账号聊天。DeepSeek真实回复要求提供作品/链接，零工具调用。合理澄清不是已证实的 Codex 故障。测试脚本把页面的“选定”误当模型已知，是本题输入/预期不匹配；是否增强页面选择传递是另一个产品功能，本轮不偷偷实现。回复另有无依据的跨账号猜测与“宿主说明”泄露文案，应独立记为回答质量问题。

预期：只新增2条同一 Run/Revision 的讨论记录，实际首轮fork生产thread、次轮resume讨论thread，两轮都发送真实图片；数据库14表、旧产物、Skill、审批、发布不变。

实际：14表完整行不变、旧版文件和P05来源摘要不变；讨论记录0、SDK调用0、生图0，未发送第二query。不是“讨论成功”，也不能评价Codex看图效果。原自动3 passed/7 failed/1 needs_review，原报告仍failed、review=null。

DeepSeek一次真实请求：input6,929/output98/total7,027；cache hit6,528/miss401为输入子集。Codex新增调用为0；费用未记录，为null，不估价。

### P04：脚本遇到读断线退出，生产未走到终态

记录 `246a3b6ca93b4940b8fcdb234ea9d75b`，39.328秒。步骤：原Run读旧图/第1版 → 保存返工201 → 新版queued、零Attempt/零SDK/零新图 → 切旧版SHA不变 → 切新版无图片 → 刷新零POST → 原按钮点击生产202 → 进度GET ECONNRESET → 脚本退出 → 采集仍运行中的业务 → 测试服务关闭。

预期：同Run +1 Revision/+1 Attempt/+4状态事件，新版真实生成一张图并待批准；旧Revision全部字段/文件不变、无批准发布。

实际：Revision1→2、Attempt1→2、事件2→4（预期6）；Run仍producing、新Attempt running，无业务错误、无完成回执。已经创建生产thread `01a12ae8-727c-7370-8d2c-4ec8e1373a1c`，checkpoint turn_completed=false、receipt turns为空；未采到SDK公开item/新图/最终用量。旧文件及P05来源SHA不变。完整交付失败是有限GET恢复遗漏和测试器提前结束，不把它说成已证实的SDK生图失败或产品越权。原自动2 passed/6 failed/1 needs_review，原failed/review=null保留；程序boundary维度因未满足完整终态合同失败，不代表正式账号真的被改动。

保存阶段零执行已核实；点击后SDK用量未知（null），不能拿checkpoint初始0充当最终免费。该题没有DeepSeek入口，外层请求0。唯一业务POST是保存与明确执行各一次，没有重放。

### 两题的独立 Eval 阅读错误

原Eval GET中 `EvalStore._bytes` 的 `stream.read(maximum+1)` 抛MemoryError（读取上限2/8MiB）。原截图、trace、browser.error都保留。现有证据不能证明是SDK、32位Python或哪一个外部进程导致：Python实际64位；系统另有其他项目进程，未取消它们。确定可改的是小文件不必每次预申请全部上限，改为64KiB分块至EOF，保留超限拒绝和路径/白名单。该优化是否解决本次现象由只读复读及新轮验证，不能预先宣布根因已清除。

旧finally/Eval确认把次级读取失败放到顶层error，原主错仍在browser.json。新轮分别保存首错与次错，测试也不能用finally断言替代主链路错误。旧report不追改。

## 下一轮约束与复现

V2仅统一修评测输入/GET观察/错误保留及只读文件读取，不改业务Prompt、Runtime、工具或正式数据。P06明确给用户可见的实际Run链接与第1版；不是加入oracle/预期答案。先免费定位、原失败GET-only复读和本地正负例，再提交/冻结，按P06→P04串行各一次，收费期间不改源码、不自动重复收费。

只读查看原失败（不调用模型）：

```powershell
python -m creatoros.evaluation.workbench view --run-id 4b32fc7566b1488c8c2d3a27256896ed --port 8903
```

然后打开 `http://127.0.0.1:8903/eval?case=P06&run=4b32fc7566b1488c8c2d3a27256896ed`。P04可用另一端口按其run-id查看。不要重用V1批次的独占槽付费重跑。V1/V2输入和采集器改变，因此不是可直接用于简历的固定基准成功率提升。

V2结果将在整轮结束后追加，不覆盖以上结果。
