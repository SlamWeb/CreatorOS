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

## 本轮目标（原生异步回答执行器）

- 新增 `AsyncPersonCloneClient`，使用 `httpx.AsyncClient` 原生读取 PersonClone SSE，不修改现有请求字段或事件协议。
- 接收已经确定性展开的 `SelectionAssignment`，共享一个异步 Client，通过 `asyncio.Semaphore` 限制同时运行的请求数，默认最多 3 个。
- `asyncio.gather` 保持输出与输入任务顺序一致；每个任务保留独立 `ToolResult`，一个作者失败时不取消其他作者的回答。

## 当前边界（原生异步回答执行器）

- 保留同步 `PersonCloneClient` 和模型可见的同步 `ask_author` Tool；原生异步 Client 仅供宿主侧批量执行器使用。
- 本轮不汇聚 token 级 SSE 到终端，不实现后台任务恢复，也不改变 PersonClone 服务端容量策略。
- 一个批次共享一个 `AsyncClient`，批次结束后显式 `aclose`；不同批次不共享生命周期不明的全局 Client。

## 验收（原生异步回答执行器）

- 本地并发 smoke 验证并发上限、结果顺序、单任务失败隔离、问题拼接、空任务和非法并发数。
- `batch_answers_smoke=passed`、`personclone_async_smoke=passed`，并回归通过同步 PersonClone、selection expansion、route-and-answer Skill 和全包 `compileall`。
- 首次复验时 PersonClone 未监听，记录为 WinError 10061；服务部署交接分支 `5f8ac815f8b9278ef58ea515fe3876f6c5b75bb7` 后已完成真实复验。
- `live_batch_answers=passed count=2 max_concurrency=2`：两个真实 PersonClone SSE 请求并发成功，分别返回 674、669 个字符和同秒生成的独立 trace_id；没有伪造成功结果。

## 本轮目标（串行/并发基准）

- 用同一批两个作者、同一批路由候选分别执行 `max_concurrency=1` 和 `max_concurrency=2`，记录总耗时、节省秒数和加速比例。
- 基准脚本只输出字符数和 trace_id，不输出回答全文或任何凭证；不修改服务端容量配置。

## 验收（串行/并发基准）

- `tests/live_batch_benchmark.py` 已加入并通过本地编译检查。
- 本次真实基准尝试时 PersonClone 再次停止监听 `127.0.0.1:8000`，在路由阶段收到 WinError 10061，未消耗生成 API；服务稳定运行后重试该脚本即可获得有效对比。
