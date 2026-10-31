# Benchmark v2 方法核查（2026-10-31）

本轮按 research skill 委派作一手资料复核。以下是已核对的原则与 CreatorOS 的具体选择，不把建议说成唯一行业标准，也不代表已执行的模型成绩。

- **从真实失败起步，但不要为凑数复制问题。** Anthropic 明确建议初期可从真实失败整理 20–50 个简单任务，同时包含该做与不该做的情况；这是起步规模建议，不能证明50题覆盖充分。CreatorOS 先按实际 Trace 根因去重，再保留成功、澄清、无工具和安全负例。[Anthropic：初始题集与平衡样例](https://www.anthropic.com/research/demystifying-evals-for-ai-agents#collect-tasks-for-the-initial-eval-dataset)
- **允许多条正确路径。** 同文建议优先验证最终产物，指出严格匹配工具顺序容易误杀有效解法。本项目采用最终SQL/文件状态、必要动作、禁止动作、关键依赖与调用预算；“参考轨迹”不是逐步脚本答案。[Anthropic：harness 与 grader](https://www.anthropic.com/research/demystifying-evals-for-ai-agents#design-the-eval-harness-and-graders)
- **pass@k 与 pass^k 分别回答“至少一次成功”和“全部成功”。** 没有同题重复 trial 时只报首尝试结果，不计算可靠性指标，也不把不同题或重试挑出的最好结果当重复成功率。[Anthropic：非确定性](https://www.anthropic.com/research/demystifying-evals-for-ai-agents#how-to-think-about-non-determinism-in-evaluations-for-agents)
- **判分证据分层。** SQL、Schema、文件和权限用确定性检查；开放回答可用结构化二分类Rubric加缺证据状态。LLM Judge 仍需人工校准，未做校准不能宣称与人一致。[Anthropic：grader 设计](https://www.anthropic.com/research/demystifying-evals-for-ai-agents#design-the-eval-harness-and-graders)

**离线 benchmark 不等于 Mock 模型。** LangSmith 将离线评测定义为在整理好的 dataset/examples 上对应用做实验；参考输出只提供给 evaluator，不送入应用。在线评测则针对生产 runs/threads。该区分是目标与数据的区别，未规定必须把LLM替换成Mock。因此本轮原前端→真实DS→真实工具/隔离世界符合离线 benchmark 的定义；故障注入、纯DTO与安全强制探针要独立标记，不能冒称模型自己选出的行动。[LangSmith：Evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts)

**干净世界与事务回滚是两个层级。** Anthropic 要求每个 trial 从干净环境开始，以免文件、缓存或历史状态污染结果，但没有说一个数据库 rollback 能撤销所有外部行为。[Anthropic：稳定环境](https://www.anthropic.com/research/demystifying-evals-for-ai-agents#design-the-eval-harness-and-graders) SQLAlchemy 的事务由 Session 使用底层 Connection 管理，外部事务测试需要把 Session 显式绑定到该连接。[SQLAlchemy：外部事务测试](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#joining-a-session-into-an-external-transaction-such-as-for-test-suites) **本项目推论：** 现有 Database.session() 各自创建/提交 Session，后台线程和多连接不受一处外层rollback统管，SQLite rollback 也不撤销文件、API费用、Codex会话或发布。使用独立进程/磁盘SQLite/完整文件根，不复制正式授权队列；发布不接真实平台，费用和未隔离的SDK输出如实列明。

**价格只作冻结估算。** 本日官方 Flash 每百万token非高峰为hit $0.003、miss $0.15、output $0.6，高峰为两倍；旧 deepseek-v4-flash 映射到当前 deepseek-flash。价格参数必须随manifest冻结并传给计算函数；可报范围，不冒充账单。Codex订阅费用未知，另列null。[DeepSeek官方价格与旧名称映射](https://api-docs.deepseek.com/quick_start/pricing/)

本核查支持方案中的真实Trace起题、隔离、结果加轨迹约束、多类grader与可追溯指标；不支持“唯一固定工具序列”“强制Mock才正规”“数据库回滚即无外部副作用”这些绝对说法。
