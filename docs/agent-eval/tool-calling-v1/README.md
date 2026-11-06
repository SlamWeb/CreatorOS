# 工具调用专项 v1

`creatoros-tool-calling-v1-20261107` 复用原 benchmark-v3.4 的 B01、B02、B03、B05、B06、B07、B10、B18、B31、B32、B38、B40：12 个已存在的请求实例，不是 12 个新增能力。定义复制自 `codex/context-engineering-20261105` 中提交 `2720cfa16039745c37120047334196136d130a98`；与当前父集 SHA-256 相同且每题定义由测试校验。其他工作树的运行批次不复制、不改写。

按业务结果、读取证据、必要工具行为及权限约束评分，允许合理替代路径；程序、语义和安全门禁分开，缺证据不算通过。Judge 尚无人工一致性校准。专项运行不能代表基础工具契约、安全边界或完整链路均完成。

## 运行前

本专项沿用已有 benchmark 的每题独立进程、临时业务 SQLite、文件与会话，并调用真实模型和前端。以下注册适配器先在用户级共享 registry 登记正式运行，再冻结、执行、写入安全摘要；每次运行必须使用新运行 ID，代码或配置变化另建轮次，同一版本的重复运行保留在原轮次。运行失败不覆盖、不自动重试。记录源工作树、题集/评分/驱动/夹具散列、计量范围和变更必要项清单；正常情况下每 12 秒发心跳，进程被强杀或注册库不可用时不得根据缺失记录推断成功。捕获异常时尽力同步已落盘的完整题目；强杀或摘要缺失时不自动补录，原始批次保留供检查。

必须准备项目 Python 依赖、前端构建和模型凭证（仅从环境或忽略文件读取）。执行真实模型可能产生费用，需显式指定 `--allow-paid`。当前适配器只允许 B05、B06、B07、B10：项目的 `nonvideo-no-publication-v1` 会禁止视频派发和发布客户端，但**不是网络、凭证、模型调用或进程沙箱**；资源和外部副作用须预先核实。余下题目没有同等确认的执行约束时，保持未运行。

```powershell
python -m creatoros.evaluation.tool_calling --round-id example-round --title "工具调用专项" --code-version <记录的版本> --run-id example-run --cases B05,B06 --allow-paid
```

源码与实际运行时配置不因提交号自动变成可恢复版本；原 runner 会冻结题集、源码清单和部分快照，具体成员与限制以其 manifest 为准。执行期间应避免修改测试工作树。

## 记录和验证

默认登记库为 `~/.creatoros/eval/registry.sqlite3`，可用 `CREATOROS_EVAL_REGISTRY` 显式指定独立库。registry 内每题记录脱敏任务模板、经浏览器提交记录与对话交叉核验的实际用户请求、工具名和参数、程序/语义/关键状态、有限判定依据、供应商 agent 请求实际用量、每题墙钟耗时及估算费用。无法核验的实际请求保持未知，旧登记不回填；模型用量缺失保持 null，语义项未人工验证。模型工具回执正文与完整原始追踪仍保存在源工作树 `data/agent-benchmark-v2/<run-id>`；登记的相对路径仅是来源标识，不承诺跨工作树可读取。

```powershell
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m unittest tests.test_tool_calling_v1 tests.test_tool_calling_registry tests.test_benchmark_suite_schema tests.test_benchmark_cases_v2 tests.test_benchmark_nonvideo_guard
```

此测试使用受控替身，不调用模型；测试通过不代表真实 Agent 通过。过去另一个工作树的模型试跑以其原始记录为准，不能作为当前源码的新结果。

## 中断批次的证据恢复

```powershell
python -m creatoros.evaluation.tool_calling --recover-run-id <原运行ID>
```

这是显式本机恢复，不调用模型、不重跑任务；只允许 failed/interrupted/unknown 终态，读取当前工作树相同 ID 的原批次。正式运行在冻结完成、模型执行之前，将 manifest SHA-256 不可变地绑定到登记记录；恢复时必须存在且完全匹配该绑定，且冻结配置必须含相同运行 ID。旧记录若没有预先绑定，不得事后伪造或补写绑定。还须满足完整 Git SHA、冻结时干净工作树、当前源码与冻结 manifest 一致，注册元数据、选题、守卫、首试 claim、原始报告和 summary 均一致。缺失或不符时拒绝恢复。冻结时存在未提交修改则保守拒绝：现有源码快照只是部分允许文件，不能证明完整还原了当时未提交的工作区与配置。终态保持不变，成功补入的题目和本地 SHA-256 写入单独的恢复记录，重复恢复相同数据幂等，冲突不覆盖。

未来新批的每题首试 claim 和 report 还会在执行时各自记录批次 ID 与该批冻结 manifest SHA-256；恢复时二者必须都与登记绑定及批次目录匹配。旧批如果缺这些执行期绑定，不能自动恢复或事后补写。即便具备这些本地字段，本地散列也仅用于追溯和防止无意串批，并非外部签名或防止本机材料被协同篡改的证明。强杀后运行尚处于 running（但已失联）时，不会自动改成终态或重试；需先由维护流程确认进程及外部调用状态后再处理。
