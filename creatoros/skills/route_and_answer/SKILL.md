---
name: route-and-answer
description: 根据实时热点匹配创作者，并在用户确认或自动选择后调用数字分身生成回答
---

# Route and Answer

## 目标

把实时热点路由到合适作者，并生成一条数字分身回答。

## 工具边界

- 先使用 `route_hotspots` 获取候选。
- 只能从候选快照中选择作者和热点。
- 选择完成后才使用 `ask_author`。
- 不读取 PersonClone 本地文件或 Qdrant，不负责发布。

## 模式

- `preview`：展示候选并等待选择。
- `confirm`：使用 `snapshot_id`、`author_id` 和 `hotspot_rank` 回答。
- `auto`：选择最高分候选后回答。

## 输出

返回候选、选择、回答、来源和 trace_id；失败时保留结构化错误类型。
