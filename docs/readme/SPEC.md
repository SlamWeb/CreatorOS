# CreatorOS README 与品牌首页 SPEC

## 目标

- 参考 `learn-to-interview` 的产品型 README 信息层级，为 CreatorOS 建立清晰的 GitHub 首页。
- 用原创图标和横版封面表达“从零写 Agent Loop，到能够构建、运营、恢复并讲清一套 Agent 系统”。
- 让第一次进入仓库的人在一分钟内看懂：它解决什么问题、已经跑通什么、如何体验、为什么值得深入阅读。

## 边界

- 只修改 README、品牌图片和本 SPEC，不改变 Runtime、Studio、数据库或业务行为。
- 已实现能力、保留实验线和 Roadmap 必须明确分开；不声称已经发布、完成长期记忆闭环或提供 MCP Server。
- 品牌图片保持原创，不复制其他项目的图标、构图或视觉资产。

## 视觉方向

- 图标：简洁的 CreatorOS 抽象标记，表现一个小起点进入可恢复的闭环；深色底、柔和紫与薄荷绿、少量暖黄。
- 封面：从左侧最小 Agent Loop 出发，经过 Tool、Context、Memory、Workflow，抵达可观察的 Creator Studio；横版、编辑感、少字、适合 GitHub README 首屏。
- 封面文字只保留 `CreatorOS` 和 `FROM ZERO TO AGENT SYSTEMS`，避免小字和营销口号堆叠。

## 验收

- README 顶部包含图标、封面、简短定位和可点击导航。
- 快速启动命令与当前代码一致，完整能力与未实现边界均可定位。
- 两张图片真实存在、可解码、尺寸适合 README；Markdown 图片路径和内部链接有效。
- `git diff --check` 通过，更新本 SPEC 后独立 commit 并 push。

## 状态

- 2026-09-07：README 已按“结果先行 → 为什么 → 架构 → 学习路径 → 启动 → 当前边界 → 下一步”的产品首页结构重写。
- 使用内置 ImageGen 生成原创 `creatoros-icon.png`（1254×1254）和 `creatoros-cover.png`（1672×941）；本地以原始文件重新打开检查，Logo、标题和副标题清晰，无额外文字或水印。
- README 明确区分当前主线、PersonClone 实验线与 Roadmap；未声称自动发布、MCP Server、完整长期记忆或已完成 Agent Benchmark。
- 本轮只变更文档与品牌图片，不运行模型业务链路、不访问正式数据库、不生成或发布运营内容。
