# route_and_answer Skill SPEC

## 目标

- 将已存在的 `route_hotspots` 和 `ask_author` Tool 组织为一条可复用的作者回答流程。
- 支持先展示候选、复用候选快照确认回答，以及自动选择最高分候选三种模式。

## 边界

- Skill 只处理一次热点到一次回答，不负责质量评审、发布、调度或后台恢复。
- 当前候选快照保存在进程内，服务重启后失效；不重复实现路由或 PersonClone HTTP 逻辑。
- `auto` 是确定性的最高相似度选择，不额外调用 LLM 做重排。

## 验收

- `preview` 返回带快照 ID 的作者候选队列。
- `confirm` 只能从快照中的作者/热点选择，并调用 `ask_author`。
- `auto` 选择最高分候选；未知快照或非法选择返回结构化错误。
