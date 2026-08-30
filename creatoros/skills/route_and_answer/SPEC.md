# route_and_answer Skill SPEC

## 目标

- 将已存在的 `route_hotspots` 和 `ask_author` Tool 组织为一条可复用的作者回答流程。
- 支持先展示候选、复用候选快照确认回答，以及自动选择最高分候选三种模式。

## 边界

- Skill 只处理一次热点到一次回答，不负责质量评审、发布、调度或后台恢复。
- Skill 不计算热点分数、不重排候选；`route_hotspots` 返回的匹配结果直接作为候选依据。
- 当前候选快照保存在进程内，服务重启后失效；不重复实现路由或 PersonClone HTTP 逻辑。
- 普通 Agent 对话使用 `route_hotspots` 和 `ask_author` 两个原子工具；组合 Runner `route_and_answer` 仅供宿主侧固定编排，不进入默认模型工具 schema。
- `auto` 是确定性的最高相似度选择，不额外调用 LLM 做重排；只由宿主侧 Runner 执行。
- 默认交互粒度是一个“作者 + 一个热点”；作者队列中的 `position` 用于用户选择，不能把全局热榜 `rank` 当成作者队列序号。

## 验收

- `preview` 返回带快照 ID 的作者候选队列。
- `confirm` 只能从快照中的作者/热点选择，并调用 `ask_author`。
- `auto` 选择最高分候选；未知快照或非法选择返回结构化错误。
- Agent 交互路径默认只确认一个作者队列中的一个 `position`；未明确要求批量时不要求用户遍历全部热点。
- Agent smoke 能验证 `read_file → route_hotspots → 等待选择 → ask_author`，且不会暴露组合 Runner。

## 本轮目标（并发回答执行器）

- 接收已经确定性展开的 `SelectionAssignment`，复用现有同步 `ask_author` Tool，通过 `asyncio.to_thread` 并发等待多个 PersonClone SSE 回答。
- 使用 `asyncio.Semaphore` 限制同时运行的请求数，默认最多 3 个；`asyncio.gather` 保持输出与输入任务顺序一致。
- 每个任务保留独立 `ToolResult`，一个作者失败时不取消其他作者的回答。

## 当前边界（并发回答执行器）

- 本轮不把 PersonClone HTTP Client 改成原生异步，不汇聚 token 级 SSE 到终端，也不实现后台任务恢复。
- 执行器属于宿主侧固定编排，不新增模型可见 Tool；模型仍只看到原子 `ask_author`。
- 每个同步调用都会创建并关闭独立的 PersonClone Client，不在线程间共享同一个 `httpx.Client`。

## 验收（并发回答执行器）

- 本地并发 smoke 验证并发上限、结果顺序、单任务失败隔离、问题拼接、空任务和非法并发数。
- 真实验证从实时路由结果中选择两位作者，以并发数 2 同时调用 PersonClone `ask_author`；不使用 Fake 代替真实生成。
- `batch_answers_smoke=passed`，并回归通过 selection expansion、route-and-answer Skill 和全包 `compileall`。
- `live_batch_answers=passed count=2 max_concurrency=2`：真实并发生成两份回答，分别返回 616、548 个字符和独立 trace_id；使用现有 `deepcode` 环境与真实 PersonClone SSE，没有伪造生成成功。
