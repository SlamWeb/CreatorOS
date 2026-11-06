# 工具网关重构：真实模型隔离试验（2026-11-07）

## 最终代码验证与局限

修复参数空白字符可能绕过未知写入门禁的问题后，冻结源码并重新运行四题隔离真实业务批。用户可执行相同命令重建新批次；`--batch` 必须换成未使用的名称，且须按运行环境配置 `--env-file` 和 `--ffmpeg-dir`。评测模型为 DeepSeek Flash，非视频、不发布守卫；题集为 `creatoros-tool-calling-v1-20261107`。原始批次含本机路径和合成业务数据，位于本机 `data/agent-benchmark-v2/<batch>/`，包括源码快照、配置、每题请求、调用、答复、数据库前后状态及原评分。实际账单未获取，费用以各批报告中的冻结价格估算为准。

```powershell
python -m tests.run_gateway_business_eval --live --batch <new-legacy-batch> --mode legacy
python -m tests.run_gateway_business_eval --live --batch <new-pure-batch> --mode discovery
python -m tests.run_gateway_business_eval --live --batch <new-fast-batch> --mode discovery-fast
python -m tests.live_gateway_acceptance --live --report tmp/<new-directory>/report.json
python -m tests.score_gateway_acceptance tmp/<new-directory>/report.json --output tmp/<new-directory>/score.json
```

| 批次 | 主模型请求 | 供应商累计输入 | 缓存命中 / 未命中 | 原始程序 / 语义结果 |
|---|---:|---:|---:|---|
| `gateway-refactor-20261107-baseline` 旧协议 | 12 | 290,947 | 261,632 / 29,315 | 3/4 / 3/4 |
| `gateway-refactor-20261107-pure-normalized` 纯发现 | 16 | 157,244 | 127,232 / 30,012 | 1/4 / 4/4 |
| `gateway-refactor-20261107-fast-normalized` 快捷入口 | 12 | 134,782 | 105,984 / 28,798 | 1/4 / 4/4 |

同四题单次运行，快捷入口累计输入少 53.7%，未命中 token 少 1.8%；加权命中率从 89.9% 降至 78.6%。缓存预热、具体回答和运行时机不同，不能据此声称缓存命中改善或确定生产费用收益；输入为多次请求总和，不是最后上下文长度。根工具 Schema 的项目估算为旧版 14,622、纯发现 1,466、快捷入口 2,215 tokens；这不是供应商精确 token 账单。

**程序分存在协议不兼容**：旧评分器只认识旧业务工具名，把发现入口/快捷入口误判为非法、把实际业务动作误判缺失。不能把上表原始程序分作为新旧调用质量对比，也没有改写原分。逐条独立复核 [最终批证据](GATEWAY_BUSINESS_FORENSIC_2026-11-07_NORMALIZED.json)：两组共 12 次 `manage_topics` 业务动作均通过原参数解析、宿主回执标记的动作后缀匹配，下一次模型请求确实包含成功回执；B05 仅首轮查询，B07 分页 offset 0/100，B10 两轮各有查询。另用实际请求根 Schema 验证纯发现 10 次入口调用和快捷 6 次入口调用；各业务参数通过原 parser。标记检查只核对动作后缀，不能据此单独证明 scope hash。B06 两批均包含原始完整正文；B07 137 个不同标题均在答案出现。语义 Judge 未经人类一致性校准，之前 B07 评分出现自相矛盾；不把单次 Judge 视为用户体验已验证。

自然任务 R2 为安全替身，不调用真实业务处理器；[原始结果](GATEWAY_ACCEPTANCE_2026-11-07_R2_RAW.json)、[机械证据整理](GATEWAY_ACCEPTANCE_2026-11-07_R2_SCORE.json) 及[当次 runner](gateway-acceptance-r2-runner.py.txt)保留。R2 运行期间产品源码哈希前后一致，但其后修复了参数规范化，故不是最终代码完整 A/B。G07 预置历史时夹具漏传 allowed_tools，两个新模式实际走旧协议而报错；保留失败，修正夹具后另存 [G07 诊断](GATEWAY_ACCEPTANCE_2026-11-07_G07_FIXED_RAW.json)，两次均未重复提交且明确建议核对，但仅证明替身场景。

仍存在语义缺陷：G04 在回执无状态时三种模式均虚构选题状态；G08 未知写入后，三种模式都可能根据普通读取断言修改失败，快捷模式还曾建议重提。守卫可限制相关重复写，不保证模型对未知结果的解释正确。列表 137 条时两页模型可见结果仍约 45,632 / 17,131 字符，是后续 token 增长来源。

最终代码网关/OCC/未知写/协议及题集评分相关联合回归 70 项通过；另有账号、计划续接、上下文和聊天相关回归。真实回执测试原夹具曾两次因同一选题同时触发 batch 与 tasks 状态竞争而得到 `scope_conflict`；调整为不同选题后通过，不能将原两次失败改记为成功。未知写门禁只在同一账号会话内生效，`fresh` / `reference` 尚无自动核对解除机制；普通聊天其他写工具与跨会话行为仍依赖各自业务规则。

## 运行中源码变化的探索批 R1

执行 `tests.live_gateway_acceptance` 的七个合成自然任务，各用原工具、纯发现网关、发现加只读快捷工具各一次；真实 `deepseek-flash` 非思考模式，独立临时账号会话，每个变体用不同随机 system 标识避免相互预热。运行前后源码哈希发现 `creatoros/tools/discovery_gateway.py` 在期间发生变化。Python 在脚本启动时已导入相关模块，但没有冻结导入瞬间源码，因此 **R1 仅用于探索，不作为最终版本公平对照**，也不以单次结果宣称因果或稳定收益；每次调用、实际答案和 usage 原样见 [R1 原始报告](GATEWAY_ACCEPTANCE_2026-11-07_R1_RAW.json)。每个模型请求的 `tools` 根名称做了检查，以防协议开关未生效被误算为通过。

全部业务调用被本脚本替身截获，未执行真实业务处理器。模拟 only `manage_topics.get` 和 `manage_plan.get` 的回执，其余操作均阻断；工具目录发现自身由当前宿主网关执行。跨账号查询的拒绝是替身或模型主动拒绝，不证明实际账号权限校验；unknown 测试中原生产调用是合成历史，不重放生产。任务、回复和回执都是合成内容；绝不把该试验当作完整业务验收。

| 任务 | 原版 input / 请求数 | 纯发现 input / 请求数 | 发现+只读直通 input / 请求数 |
|---|---:|---:|---:|
| 整理思路 | 20,972 / 1 | 23,326 / 3 | 7,971 / 1 |
| 选题详情 | 42,081 / 2 | 22,429 / 3 | 16,091 / 2 |
| 选题和计划 | 42,186 / 2 | 22,827 / 3 | 24,858 / 3 |
| 缺失状态 | 42,077 / 2 | 22,438 / 3 | 16,093 / 2 |
| 其他账号 | 20,973 / 1 | 7,185 / 1 | 7,976 / 1 |
| 两轮资料追问 | 63,226 / 3 | 30,312 / 4 | 24,237 / 3 |
| 未知提交 | 63,626 / 3 | 30,880 / 4 | 24,644 / 3 |
| 合计 | **295,141 / 14** | **159,397 / 21** | **121,870 / 15** |

Input 为供应商每次请求的累计，并非最终上下文长度。对应 output/hit/miss 分别为原版 `753/146688/148453`，纯发现 `1573/104448/54949`，只读直通 `815/64256/57614`。首次请求多为无预热，系统随机标识、实验顺序、模型输出和缓存状态不同；不得根据 miss 差值推算真实费用或生产收益。来源变化使这些数值更不能作为最终版本基准。

质量检查必须按真实结果逐条审阅：

- 缺状态回执的任务中，**三模式均自信声称“待选、未入队”**，而替身只给 id/title/brief；这与此前缺失字段测试一致，不能把该缺陷仅归因网关。
- 多工具任务中，三模式都读到了预期的选题和计划；只读直通的回答额外说选题“尚未入队生产”，回执没有该信息。
- “你好，一起整理思路”中纯发现模型主动查询列表和素材，替身没有提供这两条查询因而拦截；这是可合理探索的路径，不能自动算错误，但产生了两次额外模型请求，质量需人工评估。
- 跨账号问题三模式直接拒绝，无实际业务调用；两轮追问中都能复用给定的受众；unknown 情况没有发起重复生产，但检查接口部分被替身拦截，不能证明实际恢复行为。
- 原版替身拦截了 loop 的业务执行器，本次未显式重跑全部后端参数 parser；网关本身会校验，但该夹具不证明真实权限或实际数据库结果。模型回复仅人工依据公开合成回执审阅。

R1 原基准已冻结真实隔离业务 `gateway-refactor-20261107-baseline`（B05/B06/B07/B10），位于 `data/agent-benchmark-v2/`。B06 程序检查未通过但语义 Judge 通过：人工比较确认正文 424 字符中仅从第 52 字符起连续引用，开头 51 字符缺失。B07 程序通过、语义 Judge 的结构化失败与其文字“修正判断：pass=true”矛盾，须人工复核，不改原结果。这些基线失败不得被新运行覆盖。

仅显式 `--live` 才调用模型；报告路径不可存在：

```powershell
D:/Anaconda4.7g/envs/deepcode/python.exe -m tests.live_gateway_acceptance --live --report tmp/<新目录>/report.json
```

## 真实模型合成任务 R2（规范化修复前）

R2 使用 `deepseek-flash`，8 个自然任务分别运行旧协议、纯发现和发现加只读快捷入口；业务工具由合成替身接管，发现网关和模型调用是真实的，因此能检查模型选择和回执使用，不能证明真实业务数据库或实际权限边界。源码开始与结束哈希一致；运行时 runner 加过一行异常文本记录，但文件改动前运行进程已载入旧代码。根据记录的 SHA-256 复原并保存了当时执行的 runner：[R2 runner 快照](gateway-acceptance-r2-runner.py.txt)，其 SHA-256 为 `9012d74dbd0b5309d92d848a290d5cbf0461481263d37947e0d81f287d69c15b`。模型回答、调用、回执、使用量与源码哈希见 [R2 原始报告](GATEWAY_ACCEPTANCE_2026-11-07_R2_RAW.json)，保守机器评分见 [R2 评分](GATEWAY_ACCEPTANCE_2026-11-07_R2_SCORE.json)；机器评分不代替语义审阅。

| 任务 | 实际观察及边界 |
|---|---|
| G01 普通聊天 | 三模式均有回复；纯发现、快捷分别 1 次请求。是否应调用工具需结合回答判断，不能规定唯一轨迹。 |
| G02 查询选题 | 三模式均查询并回答；旧、纯发现、快捷分别 2、3、2 次请求。 |
| G03 跨两个资料的任务 | 模型使用选题和计划资料；需以原始回执核对回答，不能把不存在的信息当事实。 |
| G04 询问缺失状态 | **三模式都把未返回的状态说成待选、未入队。**这是明确的无依据断言，不能因为工具调用成功就判整题通过。 |
| G05 跨账号 | 三模式均拒绝并未调用业务工具；这不证明真实服务端权限守卫生效。 |
| G06 多轮找回 | 各模式只查询一次选题并在下一轮复用；纯发现首轮还出现无依据状态断言。 |
| G07 已有生产结果未知 | R2 新协议的两次报错来自夹具：选择协议时漏传 `allowed_tools=ACCOUNT_TOOLS`，回退到旧协议后被线协议断言拦截，不是产品失败。原始失败保留；修正后另做的诊断见下。 |
| G08 写入后结果未知 | 三模式均只有一次合成更新，没有重复写；但随后根据普通读取到旧标题就声称修改失败或未生效。**读取旧值不足以证明未知写入最终未生效，语义缺陷仍存在。** |

G07 单独修正夹具后重新诊断，纯发现和快捷分别 4、3 次模型请求；两者都没有再次发起制作，但调用的是合成替身。修正批前后源码哈希一致，结果单独保存在 [G07 修正诊断原始记录](GATEWAY_ACCEPTANCE_2026-11-07_G07_FIXED_RAW.json)，并未覆盖或改写 R2 原失败。不能凭该诊断断言实际生产执行状态。

## 四题隔离业务验收

B05、B06、B07、B10 使用同一题集和真实模型，在隔离服务、数据库与会话中运行；结果分别保存在 `data/agent-benchmark-v2/` 下的 `gateway-refactor-20261107-baseline`、`gateway-refactor-20261107-pure-normalized`、`gateway-refactor-20261107-fast-normalized`。此前修复前的 `gateway-refactor-20261107-pure-final`、`gateway-refactor-20261107-fast-final` 也保留，但不与最终 normalized 结果混用。两个 normalized 批次的 `experiment-config.json` 和 `observed-tool-schemas.json` 记录协议开关和实际线上的工具 schema；旧 baseline 没有这两份附加文件，可查原有 manifest 和逐请求记录。原 benchmark manifest 本身不包含这两个实验开关。

| 四题合计 | 旧协议 baseline | 纯发现 normalized | 发现 + 快捷 normalized |
|---|---:|---:|---:|
| Agent 模型请求数 | 12 | 16 | 12 |
| 供应商返回的 input token | 290,947 | 157,244 | 134,782 |
| 其中 cache hit token | 261,632 | 127,232 | 105,984 |
| 其中 cache miss token | 29,315 | 30,012 | 28,798 |
| 本批 hit / input | 89.9% | 80.9% | 78.6% |

这是各请求 input 的累加，不是最终上下文窗口大小。与旧批相比，纯发现少约 **46%**，快捷少约 **54%**；快捷在本次多轮任务中避免了纯发现额外的请求。三批执行时间、具体源码和供应商缓存状态不同，样本只有四题，不能据此证明生产命中率、费用或稳定性；本批 hit 比例事实上下降，miss 总量接近。费用以各批现有报告记录为准，估算值不能冒充供应商实际账单。

### 原评分器误报和证据复核

最终两个新协议批次中，原评分器对 B05、B07、B10 报程序失败：它只识别旧的 canonical 工具名及其参数 schema，而 `calls.json` 如实记录模型调用的 `call_discovered_tool`、`list_topics` 等新根工具名。这属于评分兼容问题，不能直接算产品失败；保留原始分数，不能事后覆盖。四题的自动语义 Judge 均给通过，仍不等于人工完成全部语义验收。

独立核对结果见 [最终批调用与回执取证](GATEWAY_BUSINESS_FORENSIC_2026-11-07_NORMALIZED.json)。根据实际请求、模型调用、宿主映射的业务参数及回执：

- 纯发现 10 次根调用、6 次业务动作，快捷 6 次根调用、6 次业务动作，根参数均通过实际 wire schema 校验，映射业务参数均通过原 registry parser，业务回执无 error。
- B05 各仅在第 1 轮调用一次列表；B07 各读取 offset 0、100 两页，回执共 137 个标题，最终答复含全部标题；B10 各在第 1、2 轮查询一次，回执分别是 4 条与 5 条。
- B06 两批均读取一次选题，最终回答包含完整正文。取证检查了工具回执确实出现在后续模型请求中及工具调用 ID 对应；标记检查仅覆盖动作后缀，未独立验证 scope hash。

旧 baseline 有两项需要单独保留的评分冲突：B06 程序失败而语义 Judge 通过；人工核对原正文共 424 字符，回答从第 52 字符开始引用，前 51 字符遗漏，程序失败有依据，Judge 通过不可靠。B07 程序通过，但语义 Judge 的结构化结果为失败，其解释却写了修正判断通过，必须标记待人工复核；不能把这两项简单填成通过或修改原成绩。

## 运行命令及留档

下面是各批使用的命令形态；`--batch` 必须换成新的、未使用名称，避免覆盖历史。默认环境文件为 `D:/CreatorOS/.env`，不应把其中密钥写入报告；默认 ffmpeg 路径以脚本参数为准。R2 原执行版为上面的 runner 快照；当前 runner 已修正 G07 夹具，因此用当前代码再运行不会逐字复现原失败。

```powershell
D:/Anaconda4.7g/envs/deepcode/python.exe -m tests.live_gateway_acceptance --live --report tmp/gateway-acceptance-20261107-r2/report.json
D:/Anaconda4.7g/envs/deepcode/python.exe -m tests.run_gateway_business_eval --live --mode legacy --batch gateway-refactor-20261107-baseline
D:/Anaconda4.7g/envs/deepcode/python.exe -m tests.run_gateway_business_eval --live --mode discovery --batch gateway-refactor-20261107-pure-normalized
D:/Anaconda4.7g/envs/deepcode/python.exe -m tests.run_gateway_business_eval --live --mode discovery-fast --batch gateway-refactor-20261107-fast-normalized
```

真实业务批每题的调用、请求、回执、评分及使用量均保留在对应批目录；合成任务是受控替身而非实际业务。未覆盖更广的工具动作、真实跨账号权限攻击、未知写入最终状态核验、长期缓存稳定性及大样本模型正确率；G04 和 G08 暴露的无依据断言应继续作为回归任务。
