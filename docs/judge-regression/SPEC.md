# Judge 固定语义回归集

## Draft · 2026-10-23

- 目的：给当前 Judge v2 准备固定的小考试，检查操作要求不进入作品评分、Mind/Visualize/整体范围、未送达证据不得冒认，以及追加准则仍守范围。
- 先冻结 `tests/fixtures/judge_semantics_v1.json` 的输入和预期，再显式运行真实 DeepSeek；本轮不改产品提示词，不改原题答案来追求通过率。修改题意必须另发数据集版本。
- 零付费评判器只检查固定案例的明确质量锚点、操作短语、类别与证据措辞，并用正确/错误响应作故障注入测试；不是通用自然语言理解器，不声称能识别所有语义伪装。
- 真实 runner 复用产品 `evaluate` 和 `system_prompt`，只读当前源文件，输出全新 `tmp/judge-regression-*` 根；每题保存冻结输入、公开原始响应、用量、期望/实际和失败。没有数据库、Codex、生图或正式业务文件写入。
- 本轮优秀参考用案例内固定的文字片段；无图片的请求不得声称看过候选图或完整剧本。它测试语言/权限/证据边界，不验证实际看图审美。
- 每轮记录数据集 SHA256、Judge/Provider 源文件 SHA256、逐题完整 system prompt SHA256、Git HEAD/dirty 状态、模型与推理强度。单题失败仍继续，最终失败返回非零。既有 Eval 分数及旧失败不覆盖。
- 预定入口：`python -m unittest tests.test_judge_regression_suite -v`；付费显式入口：`python -m tests.live_judge_regression_suite --root tmp/judge-regression-<new-name> --allow-paid`。

## 实现与验证 · 2026-10-23

- 已冻结8题v1，原题和预期的文件SHA256为`509af8b435188119f37a40080b8b2e0bdb576ad2dd965a6f62260192ac8d0700`。夹具目录以`.gitattributes`强制此JSON为LF，避免Windows自动换行改变摘要。判分规则与产品提示词分开管理，未改任何产品Judge代码。
- 零付费判分器/运行边界18项通过：正确回复、跨角色枚举与措辞、操作伪装成内容、合法创作禁令、遗漏质量锚点、操作未独立留档、冒认产物/剧本/图片、固定重评漏项/追加、未知用量、缺少付费开关、输出目录保护，以及离线重评不调用Provider且旧证据逐字节不变。人为响应只用于判分器故障注入，不计真实模型成功率。
- 真DeepSeek基线于题目和预期落盘冻结后运行：8个请求逐题完成，首轮7/8（87.5%），来源与产品提示词SHA在运行期间不变，未自动修复或重试。证据`tmp/judge-regression-v1-20261023-baseline`。逐题R01通过、R02失败、R03/R04/R05/J06/J07/J08通过；每题request/raw/result/用量和失败检查均保留。
- 唯一失败已查明是评判器空白匹配：R02预期`2轮`，DS把操作正确留档为`2 轮`，未混入评分，也未错评呈现。整轮结束后才修判分器为空白归一化，并补“2 轮合法留档能过、2 轮串进评分仍失败”回归；未改v1输入/预期、产品prompt或首轮报告。对同8份原始回复离线重评8/8，追加模型请求0、旧证据hash不变，另存`tmp/judge-regression-v1-20261023-regrade-final`（中间离线报告亦保留）。这是判分器修复，不是产品质量提升或第二轮真实模型验证。
- 真请求使用当前产品默认`deepseek-v4-flash/high`，max_retries=0；输入7455、输出21217（含推理用量）、缓存命中3968，8份用量已知、未知0。没有Codex调用、新图片、数据库连接或正式服务操作，没有改旧RSI/旧Eval记录。
- 18项离线最终检查和最终离线报告均通过；`tests.run_reliability`已显式接入judge_semantics组，现有CI调用同一入口，绝不自动运行live付费runner。完整本地门禁结果另记docs/reliability/SPEC.md，不据配置声称远端CI已执行。局限：固定词组检查不能代替通用语义审查；题内只有文字优秀参考，不代表看图审美或训练多轮收敛验证。
