# CreatorOS Trace-derived Benchmark v2（Draft，2026-10-31）

## 目标与范围

- 用户授权从全部现存Trace构建约50道独立任务，包含正例、无需工具/澄清负例、跨账号安全、选题/栏目/Skill管理、计划/素材交接和真实Codex生产。所有Trace先做完整文件清单、hash及机器解析，语义案例按根因聚类，不声称逐段由人审过。
- 先收集与消歧、校验夹具/判分器，再冻结题目、参数、参考轨迹约束、源码与模型版本；首轮全部跑完前不按失败修改产品或判据。旧题集/旧分数/失败/图片保留，新版本独立命名。
- 优先复用现有真实Web/Agent/工具/SQLite/Codex服务接线，但旧runner硬编码12题和旧业务标准不作为新50题的oracle。

## 隔离与真实调用

- 每次trial用独立磁盘SQLite、聊天、Skill目录、输入/输出及外部任务目录；启动进程import产品前设置独立DATABASE_URL，再通过create_app显式注入。异步多连接不能依赖外层单事务rollback保证全部回滚。
- 主任务从真实前端发送，真实DeepSeek与业务HTTP；生产/提炼/视频样例真实Codex。安全强制探针明确标注component，不冒称模型自己选择。回放/故障注入单独标记，不计Live模型完成率。
- 评测结束保留只读证据/产物/用量并销毁或封存可变世界；不回滚模型费用，不取消或写入正式任务。仅清理本批明确的隔离根。发布不产生真实平台笔记。

## 判分和指标

- 黄金轨迹使用允许的工具、必要动作、顺序依赖、参数语义和调用预算，不锁死单一工具序列；业务最终状态优先。参考方案是否程序验证/人工审阅必须分开标注。
- 确定性SQL/文件/权限/Schema检查为主；回答与内容的语义用结构化二分类Rubric的独立Judge调用，失败/缺证据不给默认通过，人工复核状态不冒签。
- 每题独立报告：Outcome、工具选择/参数、轨迹约束/冗余、隔离/跨账号读写、模型调用数、累计与峰值输入、输出、cache hit/miss/加权命中率、首文本时延/首次工具时延/端到端耗时、DS估算费用及Codex账单未知。
- 基线每题首尝试一票；环境/产品/评测器失败分类且仍保留分母。未重复执行不计算pass^k；未校准Judge不宣称人工一致率；50题是项目内部benchmark而非通用能力排名。

## 验收

1. 所有正式Trace清单、解析覆盖和遗漏原因可核对，每道真实来源与合成边界来源可追溯。
2. 50题定义完整、无重复凑数，分组固定开发/保留集；判分器正负/替代轨迹测试通过。
3. 冻结后从真实前端逐题执行，至少包含真实Codex图像及非图像交付；每题DB预期/实际、原始链路和指标可查看，失败不覆盖。
4. 源码与题集验冻；正式CreatorOS业务库/目录无本批写入。共享Codex登录态、SDK会话日志/图片缓存及模型计费仍可能更新，不冒称全机零写入。旧Eval和正式服务不重启、不自动重跑。

## 方法来源

- Anthropic, Demystifying evals for AI agents：https://www.anthropic.com/research/demystifying-evals-for-ai-agents
- LangSmith evaluation concepts：https://docs.langchain.com/langsmith/evaluation-concepts
- DeepSeek实际计费参数在冻结时采自官方pricing，费用为估算、不是付款凭据：https://api-docs.deepseek.com/quick_start/pricing/

## 冻结前实现与验证

- 已建立50题（40开发/10保留，9题允许真实Codex）：题目带来源hash、准备条件、查询、SQL/文件预期、必要/禁止的工具动作及语义Rubric。保留集用于本轮统一验收，题目公开且未做人类校准，不冒称盲测。
- 新runner为每题单独启动真实SPA服务和Chrome；用户query与附件走真实前端。Web回复、Trace弹窗和刷新后的同一会话均需通过；原始请求、工具、数据库前后快照、Codex公开事件和Judge结果独立保存。
- DS请求记录开始与终止，用量缺失保留null；RSI、主Agent与Judge分桶。Codex按唯一thread的累计counter取最大值，不累加Attempt副本，订阅现金成本未知。
- 58项夹具/判分/冻结/统计/权限组件检查通过（34.274秒），含真实本地HTTP但未调用LLM；Node语法检查通过。真实模型是否走通尚待pilot，不将组件成绩当成Agent基线。
- 先冻结B01协议pilot，完成整题后才允许修测试程序；pilot不计入50题基线。正式轮冻结全50题、只计首尝试；产品与判分器在该轮结束前均不修改。
- 当前产品工作树有本任务之外的未提交改动。冻结包记录实际源码、静态前端和内置Skill，而非宣称只测试Git HEAD；本任务提交不夹带其他改动。
