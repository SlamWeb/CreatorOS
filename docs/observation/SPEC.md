# Observation：本地全链路观察

状态：2026-10-08 已完成。用户授权先做 Observation，再做 Eval；本轮不启动评测、生产或发布。

## 工程问题与边界

回复 Trace、调研进度、生产 Attempt 与 Codex 事件分散，无法沿一次业务任务定位失败。新增左侧第四入口 Observation，复用持久化记录做只读投影，不建立第二套任务状态或新的执行器。外观沿用白底、浅灰、蓝色动作与既有组件。

- 账号 → 栏目 → 选题/调研 → Run → Revision → Attempt → Codex turn → 公开 item。
- 账号 → Agent 对话 → 用户 turn → 主模型/压缩请求 → 工具调用与结果；从明确的业务 ID 跳转对应任务。
- 总览会话及尚未归属账号的 Skill 提炼单独分组，不凭名称、时间或模型文本猜归属。
- 讨论跟随指定 Run/版本，讨论完成与作品批准分别显示。旧数据无请求快照/turn/item 关联时标记缺失或部分记录，不倒推、不伪造。
- Codex 公开回复、工具输入/输出及 SDK 事件可按需展开；不展示内部 reasoning 正文、认证字段或图像 base64。仅保存/读取公开诊断数据，不宣称能看到供应商内部完整模型上下文。

层级含义：一次用户请求可以包含多个 CreatorOS 模型请求（含压缩），模型请求可发起多个工具调用；委派工具关联业务任务，该任务可有多个执行尝试，每个尝试可包含多个 Codex 回合与 item。`item/completed` 只表示 item 已结束，不替代工具的失败/拒绝状态，更不替代业务验收。

## 数据与接口

`GET /api/observation/tree?parent=<node id>&offset=0&limit=100` 返回 `items[{id,label,kind,status?,has_children}]`、`next_offset`、`has_more`。节点 ID 引用服务器已知业务对象而非任意文件路径。按层加载，不一口气把全库日志注入浏览器。

`GET /api/observation/detail?node_id=...` 返回 `id,label,kind,status,sections[{title,content}],links[{label,node_id?,href?}],warnings[],active,ancestors[],timeline[]`。每个时间线项含稳定 `id`、`kind`、`label`、实际 `at`（可缺）、`status`、公开 `content` 及可选 `node_id`。链接必须是实际结构化关联；聊天快照复用现有 request_trace 读取/脱敏。

业务事实仍在 SQLite、会话 JSON/JSONL、批次/讨论记录和受管工作目录。新 SDK 公开事件写入独立诊断文件，不改变原进度或验收语义；历史只有摘要就标 partial。读取绝不 run/resume Codex，不需要登录、不读全局 Codex 会话。

## 页面行为

- 用户经 grill-me 确认：右侧以完整时间线为主，原始数据按需展开，而不是以多个调试标签为主。
- 用户确认运行中自动追加公开活动；向上阅读时不强制滚底，提供“回到最新”，隐藏页面暂停刷新。
- 左侧可展开的树，右侧选中记录详情；选中节点写入 URL，刷新/返回仍指向同一记录。
- 长正文默认缩略，可展开全文；分组展示输入、输出、工具结果、事件与关联对象；显示真实状态、错误和缺失信息。
- 加载、空态、读取失败/重试、过期节点分别呈现。切换节点的迟到响应不能覆盖当前记录；观察不会产生业务写入。
- 提供手动刷新；仅选中活跃记录适度刷新，并暂停隐藏页面刷新。不因页面开启而全库高频轮询。
- 查询失败保留“未知/读取失败”，不把查询超时变成任务失败；任务记录没有保存来源聊天时明确未记录，不把所有引用它的聊天都称为创建来源。
- 桌面双栏，窄屏上下排列/局部滚动；沿用固定主导航，不改 Skill、工作区或聊天交互。

## 验收

1. Given 账号下有对话、栏目与生产记录，When 展开树并选择 turn/request/工具，Then 看到真实关联与完整脱敏快照，零模型及业务写入。
2. Given 明确工具回执包含任务 ID，When 点击关联记录，Then 到该业务任务及对应 worker 数据；没有关联证据则显示缺失而不是猜测。
3. Given 旧日志、坏 JSON、reasoning/秘密/大内容，When 查看，Then 明确 partial/错误、敏感内容不泄露，正文不静默截断冒充完整。
4. Given 加载 A 延迟，When 切换 B/刷新/返回，Then URL、树选择与内容一致；错误可重试、旧响应不串线。
5. 桌面 1440×900 与窄屏 390×844 检查视觉；Playwright 验证点击、展开、分页、返回、只读与失败路径。隔离 API/文件测试验证归属和路径边界。

## 基线

- 修改前 `reply-trace.spec.ts` 1/1 通过（17.6s）；保留现有 Trace 桌面/手机截图作为组件基线。
- 正式数据只读；故障注入/长日志/SDK capture 使用隔离夹具，不触发真实生图或发布。此验证不属于 Agent 能力 Eval。

SDK 事件语义参考 [官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)：事件表示过程，completed item 是该 item 的最终内容；turn 终态与宿主交付验收分别呈现。实现继续使用项目固定的 Python SDK，未升级 SDK、模型或引入 Agents API。

## 本轮实现细节

- 新事件文件：每个受管执行目录的 `codex_public_events.jsonl`，与原 `codex_trace.jsonl`、progress 和 worker receipt 并存。共用 SDK 单消费者补记录，不开第二条 stream。
- 保存公开回复、工具参数/结果和 thread/turn/item 身份。completed item 覆盖该 item 的过程数据；未完成的文本 delta 在回合结束时合并脱敏后标 partial，不逐 token 写盘。页面实时性是公开活动/item 的更新，不是承诺逐 token 输出。
- SDK `interrupted` 即使正常返回也写中断回执，不用“没有异常”推导 completed。诊断写入失败不改变 SDK 原结果；不改变已有业务验收决策。
- 宿主已终止或已经进入后续执行，而旧 SDK 回执仍停在 running：保留原始回执，观察投影显示 unknown/最后记录状态并停止轮询，不假设进程仍活着。工具 `completed` 在 UI 表述为“调用已返回”，不代指业务成功。
- 识别到的密钥/认证字段脱敏，reasoning 只留存在性与省略说明，不保留正文；图片/音频 base64 省略，普通业务 `data`、本地文件路径和长公开输出保留。每条事件 8 MiB、读取文件 64 MiB 上限，超限显式说明，不冒充完整。
- 旧提炼的 public_events 与 SDK 摘要同时展示；提炼的初次生成/修改/试做各执行分别可查。旧记录只有摘要、损坏尾行、未记录 turn、归属改变分别提示，绝不调用 SDK 回补别的历史会话。
- 失败任务可直接看安全读取、脱敏后的已保存 error.txt；输入文件、原始上下文/工具 schema 与结果默认折叠。未知字段按原记录展示，不让模型生成一份看似完整的故事。
- 时间戳只用于显示顺序，不用于推测跨任务关联。无时间戳保留来源顺序并提示。批准与发布状态分开，不凭 approved 断言已/未发布。
- Observation 是本地使用者的只读全局页面；账号 Agent 带作用域的请求不能借此读取其他账号（403）。没有在此页面加入重试、取消、恢复、审批等写操作。

## 验证记录（2026-10-08）

- `npm --prefix web run typecheck` 与 `build` 通过。构建仍提示主 bundle 超过 500 kB，本轮没有做打包拆分。
- Playwright：Observation 4/4；与原 reply-trace 联合 5/5，最后复跑 26.5 秒。包含真实隔离 API 创建账号/栏目/空会话后只读浏览、URL 恢复、分页深链、全文展开、跨记录链接、手机与键盘、迟到响应、404/读取故障、活跃跟随/读旧内容不跳动/隐藏暂停/终态停刷。长内容、故障与活跃时序使用受控响应；不把这些称为真实模型 E2E。
- 后端 8 组 smoke：`smoke_observation`、`smoke_codex_public_events`、`smoke_production_progress`、`smoke_topic_research_sdk`、`smoke_content_discussion`、`smoke_reply_trace`、`smoke_account_scope`、`smoke_worker_delivery` 通过。SDK 边界测试使用固定 SDK 类型的隔离事件夹具，无需付费模型/生图；只读 API 使用真实 SQLite/FastAPI/文件，读取前后文件 hash 不变。
- 本地正式数据库通过 SQLite `mode=ro`，搭配不启动执行器/lifespan 的诊断 app 验证：第二轮 561 个历史节点 tree/detail 无 HTTP 失败。是有边界的历史抽查，不是全库完备性证明；历史缺失提示保留。
- 浏览器实际操作：展开英语账号 → 同义词栏目 → 失败调研 → 关联聊天；确认原有错误“未找到 codex CLI”可见，而非只有通用失败文案。只读预览与正式 8765 服务分离，未重跑调研、生产、发布或改正式业务状态。
- 已检查桌面 1440×900、手机 390×844 截图；默认长文预览进一步收至 200 字，复杂对象按需展开，避免把时间线变成满屏字段。
- 状态与文件边界复核补了真实 Windows 内部 junction 的拒绝、历史提炼与当前修改的区分、旧调研 attempt 不跟随新 attempt、宿主 failed 而 SDK running 的负例。补 liveness 校验时原提炼夹具缺少真实 operation/progress_directory 导致一次失败；按现有 Skill workbench 真实记录结构修正夹具，并用 ExitStack 确保断言失败也先关闭 SQLite，再重跑通过，未放宽生产规则。

## 本轮未做

- 不补造供应商内部 system prompt/隐藏推理；不保证历史丢失的内容可恢复。
- 不做自动修复、任务重试/并行调度或 Agent 能力 Eval；不依靠 Observation 改写业务事实来“修好”历史任务。
- 暂未做大量长会话下的索引/增量传输与性能基准。树按层分页，活跃详情每 4 秒读一次；后续用实际使用数据决定是否需要日志游标。
