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

## V2 整轮结束：P06 产品错误，P04 判分错误

冻结 `discussion-rework-gui-v2-20261011`，源码 `c416978`，运行提交 `1ae2579`；批次 `discussion-rework-v2-20261011`。两题串行、各一次、retries=0，整轮结束且在改代码前验冻通过。正式14表/654文件完全不变。原程序0/2、检查10 passed/8 failed/2 needs_review 保留，不能把已完成的返工改签为原评测成功。

### P06：输入与附图都正确，产品守卫错误中断

记录 `425b892b2f464b869266f6869461f482`，49.156秒。账号聊天给出实际Run链接/第1版 → DeepSeek get_content_run → discuss_content_run → fork真实来源thread为 `01a12b4d-061c-7752-bfec-6bbe49823b29` → 实际 LocalImageInput SHA与来源图一致 → SDK turn/started → item/started userMessage → 本地守卫报“非预期工具调用”并interrupt → 讨论failed/空答复 → DeepSeek如实告知未完成，不自动重试。真实事件中没有命令、写文件或生图。

根因：`item/started` 是所有SDK item的生命周期，不等于工具开始。产品只允许 reasoning/agentMessage，把正常用户输入回显也认作工具。修复只忽略 userMessage，不放宽写入/联网/生图或未知工具；这不是Codex不会看图。

数据库14表完整行不变，原图/Skill/审批/发布不变；实际只产生1条failed讨论，未发第二问，没有fork/resume完整两轮。原自动3 passed/7 failed/1 needs_review。DS真实3请求/2工具，input22,303/output494/total22,797；Codex被中断前没有终态usage，未知为null，不称零成本。

次级脚本错误：失败后仍要求Eval时间线包含尚未发送的第二问，导致Eval查看再失败；另外先断言无alert再采集答复，漏保存用户已经看到的解释。V3先采集真实答复/alert，按实际POST核实冻结query的准确前缀，完整两轮判据仍要求两问，不能将失败补写成完成。修后GET-only复读原失败成功，原评分/记录不改。

### P04：真实返工交付完成，数据库预期错误

记录 `b41638b53c06443a8922dd9052993cd0`，325.313秒；业务Run `deb8506e-efec-49cb-9a59-4bf84746e01c`。原GUI保存返工201（queued、零Attempt/SDK/新图）→ 切旧/新版本 → 刷新不重提 → 点击生产202 → 真Codex thread/turn完成及1次生图 → 新版待批准 → 原图/新版/Prompt/Skill/刷新 → 原Eval全原文和14表可读取。

实际同Run、Revision1→2、Attempt1→2、事件2→6，旧版本及图完整保留；新版未批准/未发布。新图1122×1402、2,230,798字节，SHA `41eae2ea30f46f9d093435f0e54178e0ab31444175eb9c8a136f4b57d2484ef3`。Run完成不等于图片质量已经批准。

唯一failed检查是database_scope：判分要求选题整行不变；真实生产验收本来应将目标Topic设为ready并更新updated_at。夹具又把已完成旧Run的选题错误设成queued，暴露这个漏写的正常状态变化。V3先修夹具一致性，在执行前冻结目标选题ready预期，仅准许该Topic的status/updated_at变动；标题、归属、其他账号行、旧版本仍严格一致。不是放宽到任意数据库变化。

原自动7 passed/1 failed/1 needs_review仍保留。纯GUI入口无DS请求，SDK线程累计input353,756/output9,754/total363,510；cached304,128和reasoning5,303为子集，不再加到total；费用null。21次真实GET重置均第二次恢复，保存/执行仅2次POST、零POST/模型重放。SDK按机制在用户generated_images目录另新增1图，不能声称一切文件写入都位于夹具。

### V1 后时点补充，不重算原分数

原P04报告采集截止时没有SDK公开item和最终usage；其后、测试宿主关闭前，工作目录留下已读Skill的公开命令事件及partial input21,906/output429/total22,335（thread `01a12ae8-727c-7370-8d2c-4ec8e1373a1c`）。这是报告截止后观察，不是最终完成用量，无新图/SDK completed证据。原V1报告/账本的截止口径不改；不能从原sdk.json空数组推断完全没调用Codex。

## V3 统一修复与续跑

仅修讨论事件分类、夹具/数据库预期和失败证据读取。免费正负例/真实隔离HTTP/原失败GET-only复读后提交，再冻 `discussion-rework-gui-v3-20261011`；按P06→P04各一次，整轮结束前不再改源码。旧V1/V2均保留，模型/图片内容仍独立待评估。新轮运行完成后追加真实结果。

## V3 整轮结束：首次看图恢复，续聊测试歧义与 Checkpoint 错误保留

源码 `eee68b5` / 运行 `2514e69`，冻结 `discussion-rework-gui-v3-20261011`，批次 `discussion-rework-v3-20261011`。两题各一次结束、修改前验冻通过；正式14表/654文件不变。原程序0/2，7 passed/11 failed/2 needs_review，两个原Eval全量读取均成功；不因能够解释失败而追改分数。

P06 `4d52f413431345d0a4a8294456b8933f`：74.437秒，第一轮真实fork/LocalImageInput/SDK completed并返回480字分析，讨论thread `01a12b62-5b08-7eb0-9632-6d794f74c290`。独立读图核对了四格位置、深红词标题、气泡位置差异、fetch往返箭头及抱箱carry，基本分析有图像依据，不等于完整教学质量通过。第二问“继续上一轮讨论”未明确让Codex再分析，DS直接依据上一轮tool result回答carry在右下并给短句；只有1条讨论/1个Codex turn，没有resume。输入与强制两轮Codex判据不一致；不因此强迫产品每次普通追问都调用工具。原4 passed/6 failed/1 needs_review保留。DS4请求/2工具，input30,909/output725/total31,634。Codex最后一次请求input59,087/output1,435/total60,522；fork线程累计input499,918/output13,369/total513,287含继承生产历史，不当本次费用。费用null。

P04 `f68c2a1adaaa4af89968c0e0d1b9b075`：357.969秒。真实网页保存queued/零执行→显式生产→SDK生图completed→Checkpoint保存失败→Run/Attempt failed，原图保留。具体错误是 `native_checkpoint.json.tmp -> native_checkpoint.json` 的WinError5；NativeProgress.record_usage传播到collector，worker_receipt failed，不能拿已生成图片冒充SDK completed或整题交付。Checkpoint已保存真实partial图SHA `6308947aa81e4c5fe9210b412121e04f688e8e34c87a677f84ee4568a3c406f1`，本轮完整新交付0、完成生图1；原3 passed/5 failed/1 needs_review保留。

最近SDK token更新input369,553/output12,896/total382,449（cache309,248/reasoning8,767为子集），是失败前观察到的累计值，不称最终成功用量；磁盘production_usage旧值input314,418/output11,885更早，因为后续Checkpoint写入先失败。DS0为手动网页入口。13次GET重置均有限恢复、零POST/模型重放。占用Checkpoint的进程未确认，未取消其他用户服务或扩权限。

V4仅统一共享atomic_json到已有唯一临时文件/有限Windows sharing重试，不吞永久权威错误；明确第二问委派Codex续聊，允许复用已在上下文中的讨论结果，仍严格核实新活动卡、2记录/2次实际图片输入/fork→resume。新批冻结后再跑，V1/V2/V3全部旧分数保留。八份历史报告及登记证据保留基线共330文件，原先249文件哈希完全不变。

## V4：生产走通；讨论刷新暴露采集语义错误

冻结 `discussion-rework-gui-v4-20261011`，源码 `c1289e6`、运行 `9d0e3f0`，manifest SHA `7252d58faf504e3809e2eba2d6d758737710edf02762376c74e1ec3aaa965c15`。两题全部结束后、任何下一轮修复前验冻通过；正式14表/654文件及此前330登记文件SHA均不变。原评分：P06 failed（8通过/2失败/1质量待复核），P04 needs_review（8通过/0失败/1质量待复核），浏览器1/2。独立业务链完成2/2不等于原评分2/2通过，更不能写成简历成功率提升。

- **P06 `4a462a95dfbb41e292b2a8d75982b309`，104.719秒：** 账号聊天两条query真实发送，DS5请求/3工具（get_content_run、两次discuss_content_run），输入40,096/输出948/合计41,044。两条讨论 `3fb1674f4b8e4200b03ba44fc9edde62`、`4d4f9f53e9154f06acd704590a28d947` 均completed，同一讨论thread `01a12b76-03d8-7180-ac0d-601d9f4152b5`，首轮fork来源生产、第二轮续聊；两轮实际附图SHA均为原P05图 `df9553…ad185`。14表完整行/旧Run/Skill/图像/审批不变，零新生图。首轮分析字号、四格叙事和词义，第二轮实际Codex选fetch并建议“去取，再带回来。”；独立读取与图中三步顺序一致，未自动签署整体教学质量。
- **P06失败归因：评测脚本。** execution.json中3条非空assistant：工具前“我先核实这个Run的第1版信息”（complete=false/terminal=false），两轮final各complete=true/terminal=true且属于不同实际POST turn。产品正常展示三条公开正文，脚本刷新要求.chat-answer总数2，实际3；nth(index)也会错把进度当第一条final。没有重复final或已证实持久化损坏。每轮原等待同样可能被进度提前满足，下一轮必须同时修复，不隐藏说明迁就测试。原Eval完整读取、两轮query/5模型请求/2Codexturn/DB预期实际可见，原failed不改判。
- **P04 `d0fe9320f1fa4d169997b555498914e7`，349.125秒：** 原Run页保存返工时新版本queued、零Attempt/执行；显式执行后同一Run `9705c98f-af65-45bc-a2c7-a505c001ef5b` 新增第2版/1Attempt，SDK thread `01a12b78-4ee1-7450-a9ca-0ebdc39cc597` turn `01a12b78-574f-75e2-b5a3-a2fde86bf84a` completed，生产终态awaiting_approval未批准/发布。一真实imageGeneration completed、一新交付图，SHA `696ec9c7f61139f6a5a7e7d135c5125c3b132de251316b1b18af6ff283d52798`、1122×1402。磁盘/HTTP/网页、新旧版切换/刷新/Prompt/Skill/原Eval完整读取均通过；仅目标正常业务状态更新，旧版文件/源Skill保留。Checkpoint修复在此首尝试不再失败，不证明所有Windows锁场景已穷尽。11次GET断线全部只读恢复、零业务POST重放。
- **用量与边界：** P06两个turn最后请求分别61,121/66,616 tokens；最终thread累计580,502含继承生产452,765，不与新增127,737重复相加。P04累计输入549,264（缓存491,520）/输出14,754（推理9,070）/总564,018，DS为0而非缺失；缓存/推理是子集。SDK另在用户generated_images目录生成1原文件，非正式CreatorOS数据、但在夹具外；不是完全无外部文件写入。参考参数仍不可观察，Prompt为producer报告。独立读新图确认2×2、四词双语、每格一句中文、无总标题；仅样本观察，质量仍needs_review。

V5仅修采集并增加正负例，不改产品/题目/判分意图；刷新前后验证全部公开正文、按实际turn区分两条final、讨论ID和零POST。先提交再冻结重新跑整轮，原V4评分保持不变。

补充保留：V4 P04有一次Codex生成的PowerShell文件核查命令因foreach直接接管道语法失败，Codex随后修正核查命令并完成同一turn；不是重新生产/重放POST。原SDK item/exit_code原样可读。纯Codex链路明确显示“无DeepSeek请求”，但顶部/分列DeepSeek用量仍把report.usage=null显示“未记录”而不是实际0；Codex累计564,018正确可见。此展示口径缺口记录为后续项，不把未记录当计费0，也不因此改判本轮。

## V5 整轮结束：两个新失败均在评测采集，原分保留

源码`4201086`、运行提交`5d6360b`，冻结`discussion-rework-gui-v5-20261011` / 批次`discussion-rework-v5-20261011`，manifest SHA `f8712c0d3b58dd5e4c659fa4d862173fafbfdc38ef8008894e622a0635b79ffd`。同题/夹具/产品不变，仅修final/progress采集。两首尝试结束后验冻通过；正式14表/654文件不变，先前10报告415登记文件及账本前14轮对象完全不变。原GUI0/2，自动11通过/7失败/2质量待复核，两原failed不追改。完整原Eval阅读2/2不是原GUI成功率。

### P06：误把委派后的问题当成用户原话

记录`abc0396955ec4e869f023c802dddd8b4`、100秒，原9通过/1失败/1质量待复核。实际账号聊天两次真实POST/两条final/工具前公开说明/每轮Trace与两真实讨论都完成；全部原文、turn身份、讨论链接刷新后仍在，零重复POST。第1轮fork、第二轮resume线程`01a12b87-1f7c-7651-a9ed-2523c9d8693e`，两轮真实图片输入SHA均为P05源图`df9553c3d7eb02d44d7215452fbc4e122a01208f3b5f37a8c1c156b899ead185`，14表、旧产物/Skill/审批不变，无生图。

脚本在Run页面`.discussion-message`要求等于账号query；产品实际展示的是工具`message`原文。Agent将“分析可读性”展开成阅读顺序、字号/对比、词义对应等，并将第二问展开成选择一格/短中文释义/只读，仍在用户范围。真实Trace参数与已保存message相同，不是产品丢失原query。合理扩写不能以逐字不同判失败；语义正确性仍独立待复核，不能用参数一致证明一定答对。

DS5请求/3工具，输入40,279/输出1,042/总41,321。Codex两turn最后请求分别60,544和68,337；最终线程累计581,646包含继承生产452,765，新增128,881，不重复相加。费用null。原Eval可见两用户query、5实际请求、2Codexturn、完整DB预期/实际、最终短句“去取，再带回来。”；保留原failed与脚本错误。

### P04：图片等待5秒先于慢读取完成

记录`4c492e056be34379a91e810448b5937f`、22.219秒，原2通过/6失败/1质量待复核。未点击保存返工或生产，业务POST0、SDK0、生图0，正式与源图不变。失败截图只显示“正在读取Studio数据”。Playwright原trace中Run GET `/api/runs/e77e3b38-dcc3-4aff-974c-bd46165a7647`返回200，耗时4957.67ms；随后讨论、事件、图片GET均200，图片460.859ms。默认5秒图片可见性断言过早；没有证据将其说成Codex生产失败。慢读背后的系统原因未确认。

自动DB合同需要完整返工新增行，因未执行而失败，不代表有授权外修改。网页后续原Eval完整读取通过，报告保留原零调用/未完成事实。不得把V4已成功的新图继承为本轮产出。

独立阅读另发现展示缺口：P04未执行时`outputs.json`仍含隔离复制的第1版既有图/来源完成标记，通用“最终交付”投影会写“SDK turn已完成”，而本次SDK时间线明确0turn。此行是来源图证据，不能解释为本轮完成；未追改旧报告，当前只记录为来源/本次区分的展示待修项。完整读取通过不等于全部摘要语义准确。

### V6 最小改动

只修评测脚本：原用户POST保持逐字/turn核对；实际Trace的讨论工具参数及返回以call_id、turn、目标Run/Revision/digest关联持久化讨论和可见GUI，再检查刷新不丢失/不重提。图片可见与解码等待30秒，无业务或模型重放；坏图、错误对象/消息、缺final/重复、永久失败仍拒绝。先免费正负例、提交/冻结，再从原GUI整轮重跑，不倒改任何历史分数。新轮保护12报告493登记文件。
