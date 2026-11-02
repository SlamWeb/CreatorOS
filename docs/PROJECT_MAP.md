# CreatorOS 项目地图

这份地图用来回答“一个需求该从哪里读起”。它描述当前代码的主要入口；具体业务规则与验收要求仍看相应 `SPEC.md` 和源码。

## 一张图看全局

```text
网页操作 ── React 页面 ── HTTP / SSE ──┐
                                     │
账号对话 ─ Agent Loop ─ 账号工具 ──────┤
                         部分经本机 HTTP ▼
                选题 / Skill / 生产 / 素材 / 发布
                         │
               SQLite 状态 + 本地文件与回执
                         │
            Codex / DeepSeek / 音视频及平台适配器
```

网页和 Agent 最终使用同一套业务数据及规则；现有 Agent 的部分工具调用本机 HTTP，并不是所有路径都直接调用 Python 服务。SQLite 管理业务记录和状态；Skill、图片、视频、任务快照和会话等还涉及本地文件。外部模型执行、数据库写入和文件写入不属于同一个事务。

## 找代码

| 想改什么 | 先看哪里 | 主要职责 |
| --- | --- | --- |
| 顶层页面与路由 | [`web/src/App.tsx`](../web/src/App.tsx) | 保留深链，按页面加载前端代码。 |
| 账号/栏目切换 | [`web/src/features/workspace`](../web/src/features/workspace) | URL、当前账号和栏目状态；不负责生产。 |
| 栏目操作与设置 | [`web/src/features/series`](../web/src/features/series) | 栏目工作区、设置、归属及删除交互。 |
| 账号 Agent 对话 | [`web/src/features/agent`](../web/src/features/agent) | 会话状态、流式消息和记录展示；执行事实来自后端。 |
| HTTP 接口 | [`creatoros/web`](../creatoros/web) | 把请求和响应接到业务服务，包含普通 API、SSE、文件等不同入口。 |
| Agent 和工具 | [`creatoros/agent`](../creatoros/agent)、[`creatoros/tools`](../creatoros/tools) | 上下文、模型循环、工具权限及计划；计划只观察生产模块提供的结果，不自己判定产物有效。 |
| 制作任务与执行 | [`creatoros/runs`](../creatoros/runs) | 任务、Run / Revision / Attempt、队列、执行权与状态；[production_tasks.py](../creatoros/runs/production_tasks.py) 管理拆分任务。 |
| 图片/视频交付 | [`creatoros/delivery`](../creatoros/delivery) | 冻结输入、接收和核验文件及凭据；不启动 Codex，也不代替人工验收。 |
| 生产和外部服务 | [`creatoros/integrations`](../creatoros/integrations) | Codex、Skill、图片视频和其他外部适配；当前仍有一些可继续拆分的共同依赖。 |
| 数据表与数据库 | [`creatoros/storage`](../creatoros/storage) | SQLite 模型、数据库连接和迁移。 |
| 评测和观察 | [`creatoros/evaluation`](../creatoros/evaluation)、[`creatoros/web/observation.py`](../creatoros/web/observation.py) | 汇总 Trace、业务状态和评测证据，不作为生产状态的权威来源。 |

## 以提交一次生产为例

网页提交时，先到 [`production_task_routes.py`](../creatoros/web/production_task_routes.py)，再到 [`ProductionTaskService`](../creatoros/runs/production_tasks.py)。账号 Agent 则由 [`tools/studio.py`](../creatoros/tools/studio.py) 选择并调用相应本机接口。具体选题、Skill、任务和账号的合法性由业务入口检查；任务、Run 和队列需要保持原有事务关系。

执行器调用 Codex 后，交付模块检查收到的产物与对应凭据，Run 服务再更新状态。**模型说完成、文件验收通过、人工批准、发布成功是不同的事。** 同一选题可拆成多个制作任务；失败或结果未知不能直接当成“没生产过”重新提交。

## 多个会话并行开发

让每个会话使用单独 worktree、分支、数据库、输出目录和端口。可先按功能分工：

- 前端会话负责对应 `web/src/features/...` 和交互测试。
- 后端会话负责具体业务模块、接口和数据库测试。
- 测试会话使用隔离数据和浏览器证据；修改公共 API 或持久化格式前先协调契约。

启动两套本机开发服务、构建及运行回归的具体命令见 [并行开发说明](../scripts/ISOLATED_DEV.md) 和 [浏览器回归说明](../web/E2E_RUNBOOK.md)。不要让两个会话同时安装或改写同一个 `node_modules`；外部账号、Codex 登录和用户级缓存不因为 worktree 分开就自动隔离。

## 还没有完全拆开的地方

目前仍是一个 FastAPI 宿主；`creatoros/web/app.py`、部分 Skill 及工具实现仍较大，交付模块也复用部分 `integrations` 中的 Skill、媒体和文件能力。工作树隔离解决开发实例和测试数据冲突，不代表外部服务已经隔离，也不代表所有前后端功能都能零冲突并行改动。后续按实际修改冲突和验证证据继续调整边界。
