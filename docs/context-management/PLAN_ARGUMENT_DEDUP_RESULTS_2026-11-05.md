# 计划结果参数复用：隔离验证（2026-11-05）

## 行为与边界

同一次 ModelContext 中，同一计划、步骤和工具的长参数字典按 JSON 值完全相等时（字典键顺序归一化），首次仍完整保留，后续才换为指向该次可见工具回执与步骤的 `arguments_ref`。其余计划版本、状态、依赖、错误、失败/unknown、重试与结果继续分别保留。写入回执仍沿用原有不回显参数的契约，短参数保持完整。原始账本、快照、工具执行和结果不变。

仅在计划 ID、步骤 ID、工具名、内容相同且两个工具调用 ID 均在当前上下文唯一时复用；同一结果中的重复步骤 ID 不引用。若被引用的较早消息不在当前上下文，投影会重新从原始账本构建并保留当前全文。未知或异常结构沿用现有结果；匹配按精确值，不按相似度或状态猜测。

## 回放与测试

使用 `scripts/context_attribution_baseline.py` 固定的六条历史请求，分别用提交 `beb790d` 中的 `project_tool_messages` 与本次实现处理同一请求快照；仅计算所有 tool 内容的项目内字符估算，不调用模型，不重放业务动作。计划管理样本 tool 内容从 **22,211** 降到 **14,810**，减少 **7,401** 估算 token，涉及三条 tool 消息；其余五个样本无变化。数字不代表供应商实际 token 或线上任务成功率，快照是旧版本请求且输入已在其当时被投影。

复跑：`python scripts/context_projection_replay.py --sessions-root D:/CreatorOS/data/creatoros-agent-sessions`。脚本先核验固定快照与 Trace 前缀摘要，再从本地 Git 固定提交 `beb790d` 加载当时可信的投影源码，与当前工作树投影比较；只输出样本标签和工具内容估算，不调用模型或业务工具。要求本地存在该 Git 提交和未纳入仓库的历史数据；缺失或校验不一致时报错。

`python -m unittest tests.test_plan_argument_context tests.test_tool_index_context tests.test_model_projection` 共 36 项通过；`python -m unittest tests.test_skill_context_projection` 14 项通过。覆盖多步骤、保留状态和 unknown、参数变化、不同计划/步骤/工具、复用调用 ID、重复步骤 ID、引用源不再可见后恢复全文、投影幂等和原账本不变。一次误写的测试模块 `tests.test_skill_extraction_context` 不存在导致 ImportError；改为实际存在的 `tests.test_skill_context_projection` 后通过。

`python -m tests.live_plan_argument_context` 默认不调用模型；显式加 `--live` 才发送一次真实 DeepSeek 请求，不执行任何工具或写入业务数据。合成夹具将随机校验码放在首次计划步骤长参数中，第二次相同参数改为引用、状态设为 unknown。2026-11-05 执行保存的探针：模型从引用找回随机校验码并回答 unknown，input/output 1,747/13，cache hit/miss 640/1,107，工具调用 0；此前同构的一次内联探针也通过，input/output 1,741/11，hit/miss 0/1,741。第一次内联尝试因标准输入环境 `load_dotenv()` 找不到调用文件而在任何远端调用之前失败，后改为显式 `.env` 路径。两次均是单案例接线/理解验证，不是全面模型成功率或成本对照。
