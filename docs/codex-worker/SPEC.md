# CreatorOS ↔ Codex 最小通信协议

状态：2026-10-06 已实现并通过真实历史恢复验证。复用 Python SDK、现有调研批次、Run/Revision/Attempt，不新增调度框架。

## 本次真实问题

两次 native 生产在 Codex 完成后被宿主拒收：第一次内容稿在生成图片后补充，第二次图片做了音标修正。旧 ingest 把生产途中的预览当成不可变最终交付，报“已验收页的图片、Prompt、引用或内容不能隐式覆盖”。这不是图片缺失或 SDK 登录失败。

## 三种消息与唯一状态来源

1. TASK：宿主保存 `worker_task.json`，包含协议版本、kind（research/production）、业务 scope、输入文件引用、交付要求。Prompt 分工具编写，使用同一任务的独立目录；生产只加载已选 Skill 的冻结本地路径。
2. EVENT：消费一次 SDK stream。保存 thread/turn ID、turn 开始及 SDK 终态；公开进度由已有事件接口展示。`worker_receipt.json` 是 SDK 执行证据，不是另一个业务状态机。消息/工具活动与心跳分开。
3. REPORT：调研按 ResearchReceipt 返回候选；生产写 `work/delivery.json` 及真实图片/文案。宿主验证结果后，原调研批次变 ready，原 Run 变 awaiting_approval。页面和 Agent 读这些原业务记录。模型口头说完成、单个工具完成、文件存在都不独立决定成功。

生产中 delivery 是可修改的工作稿。每次读取完整核验路径、图片字节、顺序、引用和内容；只有有效版本替换预览，旧图片按内容摘要保留。Codex 成功结束且 complete=true、全套文件有效后冻结 checkpoint；最终验收/审批继续校验摘要，之后不得静默改稿。已取消任务不能因迟到结果复活。

调研只要求标题、切入点、理由、来源；选中候选通过现有入队逻辑成为生产输入。生产不强制 PageSpec，中间形式由 Skill 决定。结果与审核状态分开：awaiting_approval 不是发布。

## 恢复

- SDK thread 创建后立即保存 ID，turn 开始立即保存 ID；启动失败允许没有 thread。
- thread_resume 恢复上下文后创建新 turn，不恢复某条工具指令。技术恢复沿用同任务同输入/Skill；新主题/新版本新 thread。归属通过现有批次/Run输入→账号/栏目/选题，历史 thread 通过 Attempt 保留。
- 保存原 work 的最新 delivery，不用旧预览覆盖它。路径只在已知旧 Attempt 根下重定位；原始记录保留。
- SDK turn 已成功、只是宿主验收失败时，可离线重验文件并交付，不调用模型/生图；必须有本任务持久化的成功 turn 证据。旧任务需只读核对原 Codex ledger，不能根据 final 文案猜成功。
- SDK turn 失败/中断不自动当成功；保留现有显式恢复操作。交付格式修复最多一次，不自动重画。工作文件/证据变化仍拒绝不可信恢复。

## 这版的边界

保持本地单任务执行所有权。邮箱是每任务目录中的请求、事件、回执文件；不要求 Codex 轮询文件领任务，不新增 RabbitMQ/Redis。SDK 原生 steer/ExternalMessage 留作后续用户中途改要求的接口，本轮不实现。账号级统一任务列表与多 worker 调度留待下一步，现有 Web/Agent 仍通过各业务接口查询。

## 验收

- 复现共享 content.md 补稿、同页修图、更换 Prompt；生产中可更新且已冻结交付不可改。
- 无效新版本不得破坏上一份有效预览，越界/坏图/重复图仍拒绝。
- SDK失败与交付失败分别留证；完成后离线恢复不调用模型，不改旧Attempt；取消终态不复活。
- 复用用户真实失败图片在隔离目录重验，正式恢复经既有执行所有权/API完成；不重新生图或自动批准发布。
- Skill 编辑另由其最近 SPEC 记录，历史 Run 冻结文件不随 Skill 库编辑变化。

参考：OpenAI Docs https://learn.chatgpt.com/docs/codex-sdk 、https://learn.chatgpt.com/docs/app-server；用户提供的《Codex Python SDK试用》最后两轮（任务契约、结构化回执、thread恢复语义）。

## 本次验证记录

- 隔离真实历史重放 2/2：只读 SDK thread history 确认最后 completed turn、cwd 和已保存 final response；使用既有真实图片完成新 Attempt 的产物验收。测试在模型入口设置失败护栏，确认没有任何新模型或图片请求。
- 正式恢复：Run `115e51bf-c9ab-4fe2-b17b-21976d715fe8`，通过既有 `/execute` API，版本 4 → 8，状态 failed → awaiting_approval；同 Revision 新 Attempt 2 成功，耗时 516ms，用量为 0，最终 1 张真实图片。原 Attempt 1 保留失败日志。未自动批准或发布。
- 同类历史 Run `13e42ec1-f004-44a5-8249-32adf4b0e074` 已被用户取消，本轮保持 cancelled。另一条未开始的 queued 任务没有执行。
- 恢复前 SQLite 一致性备份：`tmp/creatoros-before-delivery-recovery-20261006-221628.db`。正式产物、thread history、备份及回放报告留在忽略目录，不提交 Git。
- 自动回归：smoke_worker_delivery、smoke_native_production、smoke_native_run_wiring、smoke_native_artifact_web、smoke_production_progress、smoke_studio_production_progress、smoke_topic_research_sdk、smoke_content_run_service、smoke_studio_executor、smoke_studio_run_api 通过。故障与取消验证使用隔离受控执行器；真实 SDK 验证只读，不冒充新的付费生产评测。
