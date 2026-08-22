# CreatorOS Agent 工作规则

本文件只记录仓库级、长期有效的协作规则。阶段目标、当前假设、影响面和验证记录放在距离改动最近的 `SPEC.md` 中。

## 开始工作前

1. 修改前先阅读本文件和距离目标文件最近的 `SPEC.md`；如果不存在 `SPEC.md`，先写一份短的 SPEC Draft。
2. 先查看仓库现状，不要询问能从代码、文档、测试或 Git 历史中得到答案的问题。
3. 未知较多时只迈一个 `small step`；只有目标、边界、接口和验证方式稳定后，才允许扩大改动。

## 学习目标与节奏

1. CreatorOS 当前首先是一个从零手写的 Python Agent Runtime 学习项目，不是快速生成完整产品。
2. 每一步只引入一个新概念，并保持可运行、可验证、可提交。
3. 每一步先说明要解决的工程问题，再给用户约 5～20 行需要亲手输入或修改的代码，逐行解释，等待用户运行并反馈结果；确认理解后再进入下一步。
4. 用户是主要编码者。除非用户明确要求代写，否则不要直接生成大段实现或一次搭建完整目录结构。
5. 遵循 `Simple first, abstraction later`：先观察朴素实现的真实痛点，再引入 Provider、Tool Registry、Context、Session、Guard、Event 等抽象。
6. 如果现有写法开始暴露架构问题，先让用户思考继续扩展会遇到什么问题，再解释和重构。
7. 适配手机端阅读：每个聊天代码块最多展示 20 行；更长内容优先提供已推送提交对应的 GitHub 文件链接，再拆成不超过 20 行的片段并分别解释。
8. 讲解每个新概念时固定按此顺序：先说明原方案解决什么问题、暴露什么痛点；再给出 CreatorOS 的最小解决方案；随后逐行解释语法、对象和数据流；再对照 Pi 与成熟框架的行业做法；最后明确当前实现、暂缓实现和验收方式。零基础概念必须先用具体例子，再使用抽象术语。

## Pi Agent 参考方式

- 源码仓库：https://github.com/earendil-works/pi
- 文档：https://pi.dev/docs/latest
- Pi 是架构参考，不 fork、不复制其 TypeScript 实现，也不逐行教授 TypeScript。
- 阅读 Pi 时按顺序解释：为什么这样设计 → 解决什么工程问题 → 如何翻译成 Python 思维 → 当前最小版本是否需要实现。
- 当前不需要的 Pi 能力要明确标记“先不实现”，避免过度工程化。

## 当前范围边界

1. 先完成最小 Runtime，再逐步学习 Agent Loop、Tool Calling、Tool Registry、Provider、State/Context、Session、Memory、Compaction、Streaming、Events、Observability、Guards、Hooks、MCP、Human-in-the-loop 和 Evaluation。
2. 暂不实现 Trend Discovery、Creator Routing、内容生成、发布、分析反馈等 CreatorOS 业务能力。
3. 暂不接入 PersonaForge；未来把它视为 Tool 或 Service。
4. `SlamWeb/DeepSeek-Coding-Agent` 只代表旧的 Runtime v0 学习经验，不修改、不复制进本仓库。

## 验证、SPEC 与 Git 闭环

```text
read SPEC -> draft -> small step -> user types/edits -> verify -> update SPEC -> commit -> push
```

1. 每次改动后运行与风险相称的最小 smoke/test/eval；没有自动验证时，记录人工验收方式和原因。
2. 每轮把新事实、假设、验收方式和最近验证结果写回最近的 `SPEC.md`。
3. 每个已完成的小步骤都要形成清晰的 Git commit，并推送到 `https://github.com/SlamWeb/CreatorOS.git`；不得把多个学习概念塞进同一提交。
4. 提交前检查 staged/unstaged diff，只暂存本步确认过的文件，不夹带无关改动。
5. API Key、Token、密码和其他秘密只通过环境变量或本地忽略文件提供，绝不提交到 Git。

完成前确认：改动是否足够小且可回退、用户是否亲手完成了约定部分、验证是否通过、SPEC 是否更新、commit 是否已推送。
