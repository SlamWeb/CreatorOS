# CreatorOS 后端面试：20 题短答

更新时间：2026-10-23。源码核对基线：`ad9e0c7`。

这是持续维护的口述笔记，不是功能开发计划。每题先讲清楚 30～60 秒短答，再对照代码追问；已有答案不代表已经掌握。当前学习进度：第 1 题，其他题目为待逐题训练的答案草稿。

不使用未经同一冻结题集验证的成功率、Token 降幅或收益数字；区分代码实现、自动测试、真实模型验证与用户质量验收。

## 01. 项目的后端整体架构是什么？

**面试短答：**

CreatorOS 是 Python/FastAPI 实现的本地内容运营工作台，架构上是分模块的单体，不是微服务。后端分四块：API 接收网页请求；自研 Agent Loop 调用 DeepSeek，理解用户要求并选择工具；业务模块管理账号、栏目、选题和生产队列；执行模块通过 Codex 完成调研、生产等任务。结构化业务状态存 SQLite，会话与执行记录用 JSON/JSONL，图片和 Skill 存本地文件。网页操作和 Agent 工具最终复用同一套业务接口；长任务先持久化，再由后台执行并回传进度，产物验收后交给用户确认。

**一句话记忆：** 网页是入口，Agent 选动作，业务代码管规矩，Codex 干活，存储保存结果。

**一条例子：** 用户说“生产这个选题” → 聊天 API → Agent Loop → 生产工具 → 本地业务 API → Run 和持久队列 → 后台 Codex → 文件校验 → SQLite 更新为待批准 → 网页展示。

**代码入口：** [应用组装](../../creatoros/web/app.py)、[Agent Loop](../../creatoros/agent/loop.py)、[业务工具](../../creatoros/tools/studio.py)、[任务执行器](../../creatoros/runs/executor.py)、[存储](../../creatoros/storage/database.py)。

**防追问：** 这是职责分层，不是四个独立部署服务；并非每个网页操作都经过 DeepSeek。现有服务也不是每一处都严格经过 Repository，不能把理想分层说成完全实现。

## 02. 网页操作和 Agent 工具为什么能操作同一份数据？

**短答：** 网页通过 HTTP 请求 FastAPI，Agent 的运营工具也通过本地 Studio Client 调业务 API。两条入口因此复用归属检查、业务状态检查和数据库写入逻辑，避免各写一套规则。代价是同机工具调用有一层 HTTP 开销，目前优先保留契约一致性。

**代码：** [Studio Client](../../creatoros/integrations/studio.py)、[工具适配](../../creatoros/tools/studio.py)、[API](../../creatoros/web/app.py)。

## 03. 你自己写的 Agent Loop 做了什么？

**短答：** 它组装上下文、调用模型、接收工具调用、执行工具、把结果放回消息，再让模型决定下一步，直到输出答案或达到轮次限制。同时负责持久化、上下文预算和事件记录。模型决定调用哪个动作，业务代码决定这个动作是否允许执行。

**代码：** [loop.py](../../creatoros/agent/loop.py)、[轮次限制](../../creatoros/agent/guards.py)。

## 04. DeepSeek 怎么把自然语言变成工具参数？

**短答：** 请求里除了消息，还提供工具名称、用途说明和参数 Schema。模型根据用户要求生成结构化工具调用，程序解析并校验参数后才执行。模型不是直接操作数据库；合法 JSON 也不代表业务合法，后面还要检查资源归属和当前状态。

**代码：** [工具 Schema](../../creatoros/tools/host_contract.py)、[参数与执行校验](../../creatoros/tools/execution.py)、[选题工具](../../creatoros/tools/topics.py)。

## 05. 工具返回结果和最终给用户的回复有什么区别？

**短答：** 工具结果是给模型继续决策的事实回执，例如是否成功、对象和可继续的动作；最终回复是给用户看的总结。项目保留原始结果供 Trace 检查，同时对模型侧结果做投影，减少不必要技术字段。投影不能改变真实业务成功与否，也不能保证模型永远不泄露内部字段。

**代码：** [ToolResult](../../creatoros/tools/results.py)、[模型侧投影](../../creatoros/tools/model_projection.py)、[执行接线](../../creatoros/tools/execution.py)。

## 06. 账号 Agent 的上下文由哪些部分组成？

**短答：** 主要是系统指令、当前账号目录、历史对话或摘要、用户本轮要求，以及可用工具 Schema。账号目录包含账号、栏目和绑定 Skill 的元数据，不默认塞完整 Skill、全部选题和全部图片。运营状态按需查，生产再冻结所选材料，避免上下文无限增长和状态过时。

**代码：** [账号目录](../../creatoros/web/account_context.py)、[上下文组装](../../creatoros/agent/loop.py)。

## 07. 怎么防止一个账号 Agent 操作另一个账号？

**短答：** 会话绑定账号，宿主提供作用域信息，工具参数绑定和 API 守卫再检查目标资源归属，不只依赖 Prompt。边界是本地 Agent 的正确性保护，不是对公网开放的完整多租户认证系统，不能宣称数据库里每行都有独立安全策略。

**代码：** [账号参数绑定](../../creatoros/tools/host_contract.py)、[API 归属守卫](../../creatoros/web/agent_scope.py)、[聊天宿主](../../creatoros/web/chat.py)。

## 08. 对话太长怎么办？压缩后原文还在吗？

**短答：** 完整会话与本次实际送给模型的上下文分开保存。预算不足时生成滚动摘要、保留近期消息，通过 Checkpoint 记录投影位置；原消息和外置工具结果保留，必要时回读。摘要可能丢信息，因此不能用摘要替代数据库里的权威业务状态，也不等于已经实现自动长期记忆。

**代码：** [压缩器](../../creatoros/agent/compactor.py)、[Checkpoint](../../creatoros/session/checkpoint.py)、[会话保存](../../creatoros/session/snapshot.py)。

## 09. SQLite、JSON 和文件分别存什么？

**短答：** SQLite 保存账号、栏目、选题、Run、执行尝试、队列及发布反馈等需要关联和约束的状态。JSON/JSONL 保存会话、Checkpoint、部分任务记录和 Trace；图片、Skill 正文及 assets 保存为本地文件。不是所有记录都进数据库，也不是数据库与文件天然拥有同一个事务。

**代码：** [数据模型](../../creatoros/storage/models.py)、[聊天存储](../../creatoros/web/chat.py)、[会话原文](../../creatoros/session/snapshot.py)、[Skill 库](../../creatoros/integrations/producer_skills.py)。

## 10. 为什么用 SQLite，不用 MySQL 或 PostgreSQL？

**短答：** 产品主要是用户下载后本地使用，SQLite 不需要部署独立数据库服务，又能提供关系查询、事务和约束。模型调用耗时长，但数据库更新通常短且低频，目前适合单机使用。限制是同库单写者和多机共享能力；未来写竞争或部署方式改变再评估迁移，不能说换连接地址就万事大吉。

**代码：** [Database](../../creatoros/storage/database.py)、[连接配置](../../creatoros/config.py)、[迁移](../../creatoros/storage/migration.py)。

## 11. 事务和锁解决什么？五个 Codex 怎么同时生产？

**短答：** 事务让一组相关数据库修改一起提交或一起回滚；锁协调并发访问。五个 Codex 可以同时生成，但 SQLite 写入需要轮流，模型调用不放在长数据库事务里。程序记账锁协调本机部分状态操作，执行器文件锁阻止两个宿主重复调度；这些锁不是同一回事。

**代码：** [事务封装](../../creatoros/storage/database.py)、[状态更新](../../creatoros/runs/service.py)、[宿主执行锁](../../creatoros/runs/ownership.py)。

## 12. 长任务为什么要队列？现在怎么实现？

**短答：** 不适合让 HTTP 请求一直等待整个长时间生产，纯内存任务又会在重启后丢失。因此先把明确授权的生产请求写入 SQLite，再由本机调度器派发到最多五个选题工位；同选题不同时执行。未开始任务重启后可继续，已经执行但结果不确定的任务保守保留，不盲目再次付费生成。这不是分布式队列。

**代码：** [持久队列](../../creatoros/runs/queue.py)、[调度器](../../creatoros/runs/executor.py)。

## 13. 双击提交、网络超时会不会重复生产？

**短答：** 用请求身份、数据库唯一约束和当前状态检查识别重复请求；同一 Run/Revision 已排队或在执行时复用原请求，而不是再创建一次执行。版本检查拒绝过期操作。幂等有具体作用域，不代表模型调用、文件和数据库之间实现了分布式 exactly-once，结果未知时先核对。

**代码：** [工具请求身份](../../creatoros/tools/studio.py)、[入队检查](../../creatoros/runs/queue.py)、[唯一约束](../../creatoros/storage/models.py)。

## 14. Run、Revision、Attempt 为什么要分三层？

**短答：** Run 是一次内容制作任务；Revision 是这次任务中用户要求的一版内容；Attempt 是执行这一版的某次尝试。技术中断可以在同版新增 Attempt，用户改稿产生新 Revision；要求彻底清空上下文重做，则创建干净新 Run。这样业务修改和技术重试不会混成一段无法解释的历史。

**代码：** [模型](../../creatoros/storage/models.py)、[生命周期](../../creatoros/runs/service.py)、[工具入口](../../creatoros/tools/studio.py)。

## 15. 服务中断后，如何恢复而不重复生图？

**短答：** 先确认旧执行者已经退出，再读任务状态、输入快照、Checkpoint 和已有文件。交付完整但未完成验收时可离线重验；部分生成则依据协议核对可复用产物，再显式恢复，不能仅凭状态字段就宣称成功。晚到结果还要核对 owner 和当前执行尝试，不能覆盖已经中断或更新的状态。

**代码：** [执行所有权](../../creatoros/runs/ownership.py)、[恢复及结果校验](../../creatoros/runs/service.py)、[原生生产](../../creatoros/integrations/native_production.py)、[离线导入](../../creatoros/runs/offline_adoption.py)。

## 16. Skill 怎么保存、修改和管理版本？

**短答：** Skill 是包含正文和 assets 的受管本地目录，提炼和改进可以生成独立候选版本；栏目绑定和本次生产选版分开。生产冻结输入与 Skill 材料，避免共享 Skill 后续变化影响旧任务。新版本可选择或回退，但不保证效果更好，质量仍需试产和验收。

**代码：** [Skill 库](../../creatoros/integrations/producer_skills.py)、[版本服务](../../creatoros/integrations/skill_version_service.py)、[生产快照](../../creatoros/runs/service.py)。

## 17. Codex 说完成了，为什么不能直接标成功？

**短答：** 模型完成与业务交付成功是两件事。程序需要核验交付索引、实际文件、路径和完整性等证据，保存可追溯摘要，之后才进入待人工验收。文件合规不代表故事优秀，人工批准又不等于已经发布，状态必须分开。

**代码：** [生产协议](../../creatoros/integrations/native_production.py)、[产物校验](../../creatoros/runs/artifacts.py)、[状态推进](../../creatoros/runs/service.py)。

## 18. Trace 和网页进度怎么接？

**短答：** Runtime 和 Codex 执行链记录公开事件、请求上下文、工具输入输出及用量，再由后端向网页提供流式更新和历史读取。Observation 关联账号、会话、任务和执行记录，方便追查一个请求走到了哪里。公开事件不等于模型隐藏思维链，心跳存活也不等于内容生产取得进展。

**代码：** [上下文 Trace](../../creatoros/session/context_trace.py)、[聊天流路由](../../creatoros/web/chat_routes.py)、[增量投影](../../creatoros/web/chat_stream.py)、[Observation](../../creatoros/web/observation.py)。

## 19. 怎么证明功能正常？测试和 Eval 有什么区别？

**短答：** 单元和回归测试检查确定性规则、状态变化和异常恢复；真实前端 Eval 检查用户 query 到最终回复和数据库结果的整条链路，并记录模型和工具轨迹。测试在隔离数据库和目录中执行，失败保留；要区分产品问题、评测脚本问题和内容质量问题。模拟模型通过不能证明真实 Agent 成功，改题或改判分后也不能直接算成功率提升。

**证据入口：** [可靠性 SPEC](../reliability/SPEC.md)、[Eval SPEC](../agent-eval/SPEC.md)、[失败归因实例](../agent-eval/DISCUSSION-REWORK-2026-10-11.md)、[报告读取](../../creatoros/evaluation/store.py)。

## 20. 后端和 AI 辅助开发最大的真实问题是什么？

**短答：** 主要是状态跨 SQLite、文件和外部 Codex 执行，无法靠一个事务包住全流程，可能出现图片已生成但业务仍失败。应对是冻结输入、记录执行身份与 Checkpoint、校验真实文件、保留失败并离线核对。AI 辅助开发容易只修报错表面，测试通过也不代表真实 Windows/模型链路通过，所以要限制改动范围、复现原路径、补回归；现有不足还有大服务文件、部分临界区含文件操作、HTTP 工具适配开销和非分布式调度，不能说已经全面鲁棒。

**证据入口：** [可靠性记录](../reliability/SPEC.md)、[生产契约与恢复记录](../../creatoros/runs/SPEC.md)、[状态服务](../../creatoros/runs/service.py)、[工具 HTTP 适配](../../creatoros/integrations/studio.py)。

## 逐题训练记录

| 题号 | 状态 | 下一步 |
|---|---|---|
| 01 | 已给出口述稿，尚未验证掌握 | 用户不看稿复述，纠正职责和数据流 |
| 02～20 | 短答草稿，待训练 | 每次只选一题，沿代码追问，不一次灌完 |

维护方式：用户回答后记录误解和修正版，不凭阅读过就标记掌握。以后源码改变时重新核对相应答案，不把本文件当功能权威状态。
