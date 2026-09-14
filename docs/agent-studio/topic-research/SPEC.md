# 栏目选题调研与选择

## 统一选题库切片（2026-09-14，API/Agent/Web 已接）

- 自动保存的调研建议作为待选选题；人工确认后成为正式队列，绝不因调研自动生产。本轮统一 API 和 Agent 查询，Web 列表合并随后实施。
- 不迁移底层 JSON/SQLite，新增只读 topic-library 投影；同一确定性 topic ID 在确认前后保持一致，正式 Topic 的编辑和执行状态优先，不重复展示。
- 查询支持 all/pending/queued（queued 表示已入队集合，不等于 Run 排队状态），统一分页；待选保留 batch_id/candidate_id、来源、切入点及 stale 状态。
- 继续通过 prepare_topic_selection 创建 Preview，用户确认；过期候选不可选。旧 /topics API 保持正式队列语义，生产 API 不放开待选项。
- 验收：隔离库待选可见且零 Topic 写入；确认前后 ID 一致、只显示一项、改标题/来源保留；筛选分页、过期、未知栏目及直接生产待选拒绝。不重新调用付费调研验证纯投影。
- 实现 GET /api/series/{id}/topic-library；list_series_topics 默认查 all，可筛 pending/queued。已入队按原队列顺序，待选按批次新到旧、批次内原顺序；序号只指当前返回列表，不是持久 ID。正式项保留当前 brief，research_angle 明确只是调研原始角度。
- smoke_topic_research、smoke_web_agent、smoke_agent_studio 通过。真实 DeepSeek 经隔离 loopback HTTP 调用 list_series_topics → prepare_topic_selection，正确选第二条并保留来源，正式 Topic 仍为零。报告 tmp/topic-library-20260914-182135/report.json；这是单次接线验证，不是成功率提升证明。
- 真实模型测试使用合成已保存候选（不伪称联网调研），宿主仅开放查询/预览三个工具防误启动付费生产；模型本身与 API 均真实。没有改正式库或运行生图/发布。
- 当前是请求时只读合并，批次多时会扫描工件，并非跨存储事务快照；最终确认仍重新校验。不迁移库。Web 后续切片已接统一列表/筛选/分页，批次选择复用原编辑面板；仍不支持跨批次一次确认。浏览器验收见 web/SPEC.md，下一步补不同自然语言表达的回归。

## 已确认的边界（2026-09-09）

- Codex 根据栏目定位、受众、绑定 Skill 与已有选题联网调研；默认 10 条，可调整，允许不足。不生成图片、不发布、不直接入队。
- 独立候选批次持久化，用户可再打开；新的调研不覆盖旧批次。选择标题、切入点及顺序后，复用 PendingOperation Preview 和人工确认。
- Agent 暴露 research_series_topics、get_topic_research、prepare_topic_selection；不暴露确认权。GUI 复用相同服务。
- 候选带标题、切入点、推荐理由与来源；选中后完整来源和切入点进入 Topic.brief，再由既有 ContentRun 快照传给生产。
- 运行中栏目/Skill 改变时重新读取最新配置并重新调研；连续变化最多重做两次，之后提示重新发起，避免无限付费循环。就绪后改变配置的旧批次不可选，需按新配置调研。
- Preview 确认时再次校验栏目配置；旧批次不会静默套用新定位。候选使用确定性 Topic ID，重复选中不会重复入队。
- 本步候选修改在候选面板或选择 Tool 内完成；研究型 Preview 不交给通用自由编辑 Parser，以免丢失来源和版本约束。
- Codex-as-Tool 的新建/恢复均显式 gpt-5.6-luna / xhigh，不修改全局配置。

## 存储与复用

- 候选是数据库旁隔离目录里的 JSON 工件；真实生产队列仍是 SQLite Topic，不做重复业务库。
- 复用 Codex JSONL、输出 Schema、进程树回收和用量；新建独立研究 workspace，避免继承仓库开发指令。
- 复用 PendingOperation 的事务、版本冲突和人工确认入口，不另造确认引擎。

## 验收计划

- 隔离 SQLite：候选零入队、修改和顺序、来源传递、重复选择、配置变更、重启和故障状态。
- 低频真实 Luna 联网调研、DeepSeek 调用选择工具；不使用正式运营数据库。
- 浏览器检查候选 → Preview → 确认及移动布局；不调用生图或发布。

## 状态

已完成本切片：研究候选、选择 Preview、宿主确认入队；没有执行内容生产或发布。

## 验证记录（2026-09-09）

- `smoke_topic_research`：隔离 SQLite 的零写入预览、改标题/切入点和顺序、来源进入生产快照与 Prompt、重复选择/确认、栏目变化阻断、运行中按新配置重做、重启不自动重新付费，全部通过。受控 researcher 仅用于配置竞争故障注入，不代替真实调研。
- 真实 Luna / xhigh 联网调研 2 条候选通过；JSONL 观察到 web_search。thread `01a08237-47a9-7871-9c63-c9b10ad89663`，input 182260 / cached input 125952 / output 1935；这是整次多步骤调研的累计用量，不是一次请求上下文长度。来源包括官方 Tool use 和 Context/Compaction 文档。输出有来源不等于每个事实已自动验证。
- 真实 DeepSeek 会话 `11cf4751-e0b4-4c40-abc5-f119838a1708` 调用 get_topic_research → prepare_topic_selection，正确只选 c2 并改标题，返回人工确认链接；未调用生产或重新调研。测试最初把内部 ToolCall 当成供应商 function 格式，修正断言后复核同一次真实会话，没有重复请求模型。
- 浏览器在隔离 `tmp/topic-research-live-20260909-021053/test.db` 走通全选、改标题、c2 上移、Preview、确认、刷新及已入队标记。来源保留，队列为 2 项、尚未生产。390px 无横向溢出；实际截图发现并修复 checkbox 继承全局宽度导致标题挤成一列的问题。浏览器 error 日志为空。
- 关联 smoke：pending_operation_service、codex_producer、producer_skills、web_agent、studio_operations 通过；前端 typecheck/build 通过。
- 正式数据库、用户全局 Codex 配置均未改；本步调用显式覆盖 Luna/xhigh。新建与 resume 命令均有回归断言；没有为验证模型参数再启动图片生产。

## 有意保留的限制 / 下一步

- 同时一项调研，不自动排队；失败/中断需显式重新发起。候选批次持久化，未确认勾选/输入仍是页面本地草稿，刷新会清空。
- 运行中配置改变会最多重新研究两次；已就绪后再改配置，需显式按最新配置重新研究，不能把旧候选重新贴标签。正式队列确认检查栏目字段；外部 Skill 使用不可变版本 ID。
- 不做跨批次语义级硬去重；同一候选确定性 ID 防重复，跨批次避免重复靠已有选题输入。未选不等于永久拒绝。
- 本步不支持从通用 Preview Parser 修改调研型计划；应在候选面板或选择 Tool 中修改，再生成 Preview，防止丢失来源和配置约束。普通手动选题计划的编辑不变。
- 下一步用一个真实自有栏目走通：绑定 Skill → 调研 → 选中入队 → 显式 ContentRun 生产 → 人工验收；随后把真实选择/歧义/冲突样例加入 Agent Eval。暂不引入更多调度和记忆抽象。
