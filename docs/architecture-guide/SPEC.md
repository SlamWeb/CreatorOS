# CreatorOS 架构学习手册

## 目标与范围

- 用户希望重新理解项目来时路、两条内容业务线、Runtime、上下文、记忆与下一步取舍，能够在面试中讲明设计缘由。
- 交付可离线打开的单文件中文 HTML，包含架构导航、真实代码入口、具体例子和可展开面试问答。
- 以 `bb219dd` 源码、模块 SPEC、Git 历史和用户已确认的产品讨论为证据；区分当前实现、历史方案、保留接口与未来建议。
- 仅新增学习文档，不修改业务实现、正式数据库，不调用内容生成或发布。

## 内容重点

- 分清 CLI Agent Loop、Studio OperationPlan 和 Codex 内容生产三条执行路径；不得暗示 Web 已完整复用 CLI 的 Session/Compaction。
- 解释 domain-only Max Similarity、缓存失效、作者侧候选与 SelectionPlan；注明 perspective、常青/实验队列的实现边界。
- 解释 Skill Loader、固定 Producer 和通用 Skill 调度的区别；Tool Registry 不等于 MCP Server。
- 分清会话快照、压缩 checkpoint、业务数据库和 Codex thread；持久化数据不等于自动召回的长期运营记忆。
- 给出以真实任务成功、恢复与成本为中心的下一步验证方向，不预写收益指标。

## 验收

- 离线 HTML 无外部资源依赖；目录、架构节点、演示切换与问答可操作。
- 桌面及 390px 视口检查无溢出、文字可读、无 JavaScript 报错。
- 代码链接固定到本次审阅版本；资料中不包含密钥、Cookie 或正式运营内容。
- 更新本 SPEC，commit/push。

## 状态

- 2026-09-07：完成单文件 `index.html`。共 12 章，13 个可点架构节点、4 个上下文投影示例与 15 个面试问答，包含源码入口和后续逐模块学习路线。
- 实际核查确认：Studio Parser 不消费 CLI Session/Compaction；Producer 固定 knowledge-to-carousel；domain-only 路由无 perspective 重排；运营数据库尚未形成反馈提取/召回闭环。文档明确区分上述实现边界。
- 浏览器验证：桌面与 390×844 手机页面已截图目视检查；13 个节点与 4 个上下文阶段逐一点击通过，问答展开通过，console error 为空、无空链接；手机页面宽度未超过视口。
- 已修正长英文模块名在窄节点内的换行。源码链接固定 bb219dd，逐项核查引用文件与历史提交存在。
- 本轮仅文档，未运行生产服务或模型 API；本地静态文档服务用于浏览器验收，不读取正式运营库。
