# CreatorOS Tool Exposure SPEC

## Web 调研等待终态（2026-10-05）

- `research_series_topics` 只提交一次；Web 提供公开活动回调时宿主每秒 GET 同一批次，终态候选/安全错误再返回模型。`get_topic_research` 也可观察已有活跃批次。CLI 无回调时仍异步返回，不增加新工具或模型轮询循环。
- 有界等待为 Codex 宿主超时 + 10 秒，读取超时 2 秒；停止/等待超时/读失败保留原批次 ID 和 unknown，不自动重发 POST、取消后台任务或谎称研究失败。真实 failed/interrupted/stale 返回 error ToolResult，公开活动不反复塞进模型上下文。
- `smoke_research_chat` 通过真实隔离 HTTP/SQLite，模型受控以验证竞态：两会话共享同批次、独立 Provider/账本、终态/错误回到模型、等待中关服务/重启不重提、读失败/超时零重提、无自动入队或生产；低成本真实模型验收另见业务 SPEC。

## 账号会话工具范围（2026-10-04）

- Web 账号模式复用同一工具实现，仅缩小固定 allowed_tools；全局安装/提炼/栏目转移留总览。执行器照旧再次拒绝未开放工具。
- RuntimeContext 的 creator_id 和 agent_session_id 来自不可改绑的会话，不由模型传参。Studio 工具经现有 Client 携带会话头，API 验证真实资源归属；不复制业务服务，也不增加 Tool Router。
- 目录只返回本账号，Skill 目录只读共享。CLI 默认全局行为不变。隔离 HTTP 和真实 DeepSeek 证据见 `docs/agent-studio/web-chat/SPEC.md` P1。

## 账号 Agent 按需读取 Skill 正文（2026-10-04）

- 新增只读 `get_producer_skill(skill_id, offset, limit)`；仅在用户明确询问 Skill 写法/规则时调用。可读目录已登记 Skill 和固定内置 `knowledge-to-carousel`，每页最多 4000 字符；返回 name/description、正文和 `page.total_chars/has_more/next_offset`。
- 通过同一 Studio API `GET /api/producer-skills/{skill_id}/content` 读取；账号会话 guard 只放行该精确 GET 路径。服务端按注册 ID 映射工作目录或内置固定目录，不接收任意路径；校验受管目录与 `SKILL.md` 不为 symlink、正文直接位于 Skill 根目录。
- 读取不调用 `describe()`，不补建缺失工作副本、不计算全部 assets digest、不写数据库；正文缺失或未登记返回 404。`read_file` 范围不变。
- 验收：`python -m tests.smoke_producer_skill_read_tool` 覆盖真实隔离 HTTP、账号会话、固定内置/已登记 Skill、分页、超限、任意 ID/路径拒绝、缺失副本不恢复和非法 UTF-8 不写回；不访问正式数据库或外部服务。

## Artifact → Skill Agent tools (2026-10-02)

- Web 与 CLI 共用 extract/get/save/cancel 四个 Studio 工具。提炼支持用户明确提供的本地 `image_paths` 或已有 `upload_ids` 二选一；CLI 本地读取复用 `read_file` 的项目根目录与敏感路径规则，且在有界读取前检查大小。Web 的 `archive_only_reads` 宿主只接受 Skill 页已上传的 `upload_ids`。图片限 1–6 张 JPEG/PNG/WebP、单张最多 4 MiB。
- `request_id` 必填并透传服务端幂等协议；上传与提交不自动重试，结果不确定时调用方查询/复用同一 ID。无 `job_id` 的 get 查询历史任务。
- 草稿查询和保存分开；保存描述要求先展示草稿并获得用户明确确认，传回 `expected_digest`。工具不自动保存、绑定栏目、生产或发布。
- 验收：`python -m tests.smoke_skill_extraction_tools` 使用 StudioClient 传输桩验证 schema/请求与路径安全，不触发真实 Codex 图像提炼或 Skill 库写入。

## 安装工具描述与实际实现对齐（2026-10-02）

- 将 install_producer_skill 的旧“委托 Codex 下载”描述改为 CreatorOS 使用 Git 下载核验到本地生产 Skill 库；明确不调用模型、不安装到 Codex 全局目录。安装流程与参数不变。
- 同步 get_skill_install / list_producer_skills：登记不等于绑定或生产兼容；明确现有组合工具入口与当前本地工作副本，去掉仅网页可绑定的旧限制描述。
- 验收：`python -m tests.smoke_studio_composition_tools` 通过，包含模型可见 schema 描述回归及真实本地 HTTP/隔离数据库跨入口检查；未调用模型或启动真实调研、生图、安装，未修改正式运营数据。安装器使用现有隔离 Git 文件夹夹具，本轮只验证描述与接线，不冒充真实下载验收。

## 可编辑本地 Skill（2026-10-01）

- `list_producer_skills` 返回当前工作副本元数据及 `local_path`；`compose_series`/`update_series_composition` 可使用此路径或兼容目录 ID，经同一 API 规范化保存。
- 没有增加编辑工具或放开 Web `read_file`；本地文件由用户编辑，Codex 生产读取首次执行冻结的目录。隔离共用接口/工具回归通过。

## 栏目组合 Tool（2026-09-23，P2）

- 新增 compose_series / update_series_composition / assign_series / queue_topics 四个写工具，全部经同一 Studio API（StudioClient 带 x-creatoros-origin 头），与 Web 表单同一服务、同一幂等与 revision CAS。
- queue_topics 是 A 策略直接入队：明确指令一次事务完成校验/写入/审计；prepare_topic_selection 保留为"先看影响"能力，两者都不暴露可复用的确认凭证。
- install_producer_skill 可声明 role（mind/production）；省略即未分类，可展示不可生产。
- Web allowed_tools（STUDIO_TOOLS）与 AgentPage 中文名同步更新；CLI 使用同一 tool_registry。
- 宿主指令补充 A 边界：明确给标题+栏目则直接入队（source=manual，不因库中不存在而追问）；查看/建议不写；删除/覆盖/发布不在入口内。
- 验证：smoke_studio_composition_tools（真实本地 HTTP，跨入口一致性）与 live_a_boundary_eval（真实 DeepSeek，6 题）通过；详见 docs/studio/composition/SPEC.md P2 记录。

## 统一选题查询（2026-09-14）

- list_series_topics 默认返回栏目统一选题库，state=all/pending/queued；待选提供批次/候选 ID 给现有 prepare_topic_selection，仍须用户确认。Tool 数量不增加。
- 真实 DeepSeek/隔离 HTTP 查询后直接准备第二条预览通过，来源保留且零入队。细节见 docs/agent-studio/topic-research/SPEC.md；旧正式队列 API 不变。

## 当前会话回读（2026-09-11）

- read_tool_result 从 Loop 绑定的 RuntimeContext.session_file 回读；模型不可指定文件或其他会话。无上下文直接调用兼容 CLI 默认文件。
- Web 开放 read_tool_result；同时开放受宿主绑定的 read_file，但 Web 只能读取当前会话的 `.tool-results` 归档目录，不能读取项目任意文件或其他会话。归档大文件用 `unit=chars` 分页；详见 docs/context-management/SPEC.md。

## 栏目选题 Tool（2026-09-09）

- research_series_topics 提交后台研究；get_topic_research 读取状态/候选；prepare_topic_selection 按结构化选择、修改和顺序生成 Preview。CLI/Web 共用 Studio API，确认仍不暴露给模型。
- 真实 DeepSeek 验证只选 c2 并修改标题成功，候选角度和来源保留；没有生产或确认副作用。详细记录见 docs/agent-studio/topic-research/SPEC.md。

## Web 宿主范围（2026-09-08）

- RuntimeContext.allowed_tools 为可选宿主范围；Loop 过滤模型 schema，执行器再次拒绝范围外请求。默认 CLI 保留既有完整工具集合。
- Web 只使用五个 Studio 工具。RuntimeContext.studio_url 显式传到同一 HTTP Client，测试库/自定义端口不通过改全局环境变量实现，避免宿主串线。
- 验证见 `docs/agent-studio/web-chat/SPEC.md`，本轮没有新增另一套业务工具或自动批准能力。

## 本轮目标

- 保留完整 `tool_registry` 供执行器、Skill Runner 和后台编排使用。
- 允许某些组合工具只作为内部 Python 能力存在，不默认暴露给 LLM。
- 默认模型工具列表只发送标记为可暴露的工具 schema；原子工具仍保持现有参数和执行行为。

## 当前假设

- `route_and_answer` 是 `route_hotspots → 选择 → ask_author` 的组合 Runner，交互 Skill 应优先让 LLM 编排原子工具，因此暂不放入默认模型 schema。
- `tool_registry` 仍必须保留 `route_and_answer`，已有 Runner 和 smoke test 继续通过统一执行入口调用它。
- “不暴露”只影响发给模型的 `tools` 参数，不影响直接通过 `execute_tool_call` 执行。
- `route_hotspots` 的作者队列为每个候选提供作者内 `position`，让 Skill 可以把一次用户选择明确绑定到一个作者和一个热点。

## 验收

- `route_and_answer` 出现在 `tool_registry`，但不出现在默认 `tools` schema。
- `read_file`、`route_hotspots`、`ask_author` 等原子工具仍出现在默认 schema。
- 既有工具执行 smoke 通过，新增验证能证明模型工具暴露边界稳定。

## Codex 内容生产 Tool

- `produce_content_pack(creator_id, series_id, topic_id, topic_title)` 把已选题目交给固定的 `knowledge-to-carousel` Skill，不让 Codex 决定栏目或选题。
- 一次 Tool 调用对应一个新 Codex thread；结果返回内容包路径与 `thread_id`，前台等待期间不重复调用主 Agent LLM。
- 生产失败返回结构化 ToolResult；成功结果只向模型投影摘要，不把图片字节或完整 JSONL 塞入上下文。
- `codex_producer_smoke=passed`，Registry schema、参数路径约束、回执解析、图片归属和 Manifest 验收通过。

## Agent → Studio（2026-09-07）

- 新增 5 个模型工具：list_creators、list_creator_series、list_series_topics、start_content_run、get_content_run，复用 Studio HTTP 接口。
- 旧 produce_content_pack 留给底层兼容调用，从默认 tools 隐藏。Agent Loop 用 model_requested=True 执行工具，拒绝历史消息或幻觉调用隐藏工具；宿主内部调用兼容保留。
- start 仅接 topic_id，返回 accepted/status/run_id/url；只提交尚未尝试的首版 queued Run，其余返回现状。查询状态不返回完整 Revision、图片或 Trace。
- 查询分页保留 total；busy/版本冲突/未知网络结果不自动重试。规范与验证见 docs/agent-studio/SPEC.md。
## 生产 Skill Tool（2026-09-08）

- install_producer_skill / get_skill_install / list_producer_skills 通过同一 StudioClient 暴露给 Web/CLI；安装返回句柄，不轮询等待、不生成或自动绑定。retry=true 只用于用户明确重试失败/中断。验收见 docs/agent-studio/producer-skills/SPEC.md。
