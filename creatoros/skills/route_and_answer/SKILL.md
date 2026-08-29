---
name: route-and-answer
description: 通过 route_hotspots 获取作者候选，并在用户选择后调用 ask_author 生成数字分身回答
---

# Route and Answer

## 目标

按固定顺序调用两个已有工具：先获取候选，再根据选择生成回答。

## 工具边界

- 先使用 `route_hotspots` 获取作者侧候选队列。
- 不自行计算热点分数、重排候选或改写热点；匹配分以工具返回值为准。
- 交互模式下先展示候选并等待用户选择，选择完成后才使用 `ask_author`。
- `ask_author` 使用用户选择的作者和热点标题/介绍；不要重新调用 `route_hotspots`。
- 不读取 PersonClone 本地文件或 Qdrant，不负责发布。

## 模式

- 交互模式：`route_hotspots` → 展示候选 → 用户选择 → `ask_author`。
- 自动模式：仅当用户明确要求自动选择时，才调用 `route_and_answer(mode="auto")`；自动选择规则由 Runner 执行。

## 输出

返回候选、用户选择、回答、来源和 trace_id；失败时保留结构化错误类型。
