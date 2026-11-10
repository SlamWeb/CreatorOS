# 常规与完整回归目录 v1

该目录把请求实例组织为可版本化的只读视图；它不执行模型，也不代表已完成测试。定义和判分仍以各源题集及正式批次的冻结文件为准。

- `regular-v1`：按题面、预期、场景夹具和工具路径归类，包含 **108 个常规请求定义**，其中 11 题经 local-dispatch-v1 受控准入、97 题尚待执行准入。资源归类只说明按正确预期不要求新生图/视频、Codex 生产或外部联网调研，不保证错误调用没有副作用；默认全量不能跳过97题冒称完成。
- `full-v1`：完整列出 **120 个请求实例定义**，目前 11 个有可调用适配器，109 个待核实；其中 12 个根据题意明确涉及真实生产或联网调研，标为重资源。题集定义数、适配器准入数和已真实执行数是不同概念。
- 已准入不代表每题都已真实模型执行。当前本地守卫经隔离 HTTP 与 Agent loop 受控正反例验证，危险工具的调用在分派前被拒绝并产生关键失败，但它不是网络或进程沙箱；与旧 nonvideo 策略不能直接比较。`live_codex=false` 本身不证明低成本或无外部副作用。自然表达和原题是不同请求实例、共享父题；专项视图引用原题，不能额外计数。Judge 尚未经过人工一致性校准。
- B29、B45 和 I01 读取隔离世界已有图片，并可能调用多模态/视觉评审模型，模型能力和费用仍需核实；B34 要求跨账号生产，正确预期是拒绝，但授权或守卫失效可能触发付费，必须单独验证后才可准入。每条题目均带来源定义、场景及分类依据路径，组内分类不代表逐题真实模型已试跑。

## 加入新数据集或新测试集

1. 在 `docs/agent-eval/<新题集>/cases.json` 中按 [题集协议](../SUITE-SCHEMA-v1.md) 准备有效题面、世界、预期、来源、评分和 `family` / `split`；新增定义须有实际夹具和计分验证。题目编出来不代表 runner 已支持场景。
2. 复制此目录到新版本，更新 `catalog_id` 或 `version`；每个新 `sources` 项指定独立的 `namespace`、唯一 `dataset_id`、仓库相对 `path`、选择方式和规范化 `canonical_sha256`。`selection` 为 `suite_eligible` 时使用题集显式资格，`natural_eligible` 必须声明与题集协议吻合的父来源；普通完整来源可用 `all`。
3. 使用唯一的 `<namespace>:<case_id>` 在 `suites[].instances` 中组装测试集；可以在新版本使用 `include_all` 生成全体视图，或按已审查资源分类使用 `include_resource_classes`。原题和改写必须保留父子关联，同家族不能跨 dev/acceptance；同一个实例跨套件引用不增加题数。
4. 默认资源类别为 `unknown`、执行状态为 `requires_review`。通过逐题审查后记录 `classifications` 或 `classification_groups` 的资源类别、具体依据和代码路径；多模态调用、生产负例及其他副作用应单独记录。即使声明 `live_codex=false` 也不会自动准入。真正启用新 adapter 需要实现和审查执行器、隔离及失败处理，单改 JSON 不能伪造准入。
5. 每次改变成员、题面、资源或准入分类都创建新目录/版本；旧配置和历史批次不覆盖。`catalog_sha256` 标识完整目录配置，`membership_sha256` 标识每套成员及其题目摘要、分类与待归类候选；运行前冻结这两个值，报告按当时的定义解释。

规范化摘要可用以下只读命令计算，其中 `path` 替换为仓库中的题集文件：

```powershell
python -c "import json,pathlib; from creatoros.evaluation.benchmark.natural_suite import canonical_digest; p=pathlib.Path('docs/agent-eval/<新题集>/cases.json'); print(canonical_digest(json.loads(p.read_text(encoding='utf-8-sig'))))"
```

`creatoros.evaluation.regression_catalog.catalog()` 加载默认目录；可指定 `manifest_path` 和 `repo_root` 加载隔离的新版本。它拒绝目录穿越、链接、缺失文件、来源摘要不匹配、重复实例/套件、未知成员、家族跨 split 和伪造的适配器准入。新源或新套件的请求默认需要审查，不会悄悄成为可运行项。目录错误必须作为错误报告，不得显示为空集。

查看当前目录摘要：`python -m creatoros.evaluation.regression_catalog --summary`。这只是目录查询，不会启动模型或修改评测登记。

## 制定计划与运行

在仓库根目录执行；先检查完整范围与阻断项，再明确选择准入实例。计划只读取目录，不调用模型、不登记运行：

```powershell
python -m creatoros.evaluation.regression_runner plan --suite regular-v1 --change-scope tool
python -m creatoros.evaluation.regression_runner plan --suite regular-v1 --instances base:B05,base:B06 --change-scope tool
python -m creatoros.evaluation.change_rules tool
```

目前默认选择常规集全部 108 项，其中 97 项未准入，不能直接运行。以下示例只执行已准入的两个实例，属于部分覆盖；将示例身份替换为本次唯一运行 ID、轮次 ID 和实际代码版本：

```powershell
python -m creatoros.evaluation.regression_runner run --suite regular-v1 --instances base:B05,base:B06 --change-scope tool --round-id example-round --title "工具改动回归" --code-version <实际代码版本> --run-id example-run --allow-partial --allow-paid
```

PowerShell 中请将 `<实际代码版本>` 连同尖括号替换为实际值。`--change-scope` 可重复指定，当前规则支持 `tool`、`business`、`agent_shared`、`judge`、`ui`；未知范围会标记覆盖要求未知。`--allow-partial` 表示明确接受覆盖缺口，不会自动跳过选中的未准入题。`--allow-paid` 明确允许真实模型调用及费用；不要在未确认调用目标和预算时运行。不同代码或配置使用新轮次，同一轮次的重复测试使用新运行 ID。

运行前准备项目 Python 依赖、前端构建、所需媒体工具及模型凭证；凭证只从环境或忽略文件读取。具体环境和记录位置见 [工具专项运行说明](../tool-calling-v1/README.md)。默认登记库为用户级共享库；隔离测试可通过 `CREATOROS_EVAL_REGISTRY` 指定独立路径。运行期间保持源码和目录不变，避免与其他任务共享临时业务数据或输出目录。

目录、成员、选题与变更范围会冻结并绑定运行记录；部分运行不会记作全量回归。所有目录运行使用 `local-dispatch-v1`，该守卫不是网络或进程沙箱，也不能与旧守卫结果直接比较。历史记录保留当时的冻结目录；中断后恢复还要求 manifest 所绑定的源码、数据和其他证据一致，不满足时会拒绝恢复，不会自动重跑或补造结果。
