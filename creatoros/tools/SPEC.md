# CreatorOS Tool Exposure SPEC

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
