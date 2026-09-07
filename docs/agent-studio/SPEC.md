# Agent → Studio 统一生产入口

## 本轮目标（2026-09-07）

- 用户已授权代写。Agent 查询真实账号/栏目/选题，通过本地 Studio API 提交生产；Web 与 Agent 看到同一 ContentRun。
- 复用已有 HTTP 目录、创建/执行/查询 Run 接口，不增加状态机或第二个执行器。
- 新增 `python main.py --agent` 客户端入口，在获取数据库/生产锁之前分流；旧独立菜单保留。支持 `--studio-url`，默认 http://127.0.0.1:8765。
- 新增 list_creators、list_creator_series、list_series_topics、start_content_run、get_content_run。查询保留分页与真实 ID；启动只接收 topic_id。
- 旧 produce_content_pack 保留底层兼容接口，但从默认模型 tools 隐藏；默认生产走 ContentRun。

## 行为边界

- 新建/尚未尝试的 queued Run 可提交；已运行、已完成、失败、中断或返工版本只返回现状，不隐式恢复或重做。
- 请求不等待生图；结果区分本次 accepted 与当前 status，附可打开 Run URL。忙碌、版本竞争和未知网络结果不自动重试写入。
- HTTP 客户端只连接显式本机地址，不自动回退直接生产。服务未启动时给出启动命令。
- 目录与 Run 数据来自服务端，不让 LLM 重复填写 Creator/Series/title，不将完整产物或 Trace 默认塞入上下文。
- 本轮不接 Web Copilot、不增加审批/恢复 Tool、不改运营计划类型、Skill 绑定或发布。

## 验收

- 隔离 SQLite + HTTP 验证目录分页、关联、重复启动、busy、完成不重跑、错误响应、未知网络结果和 CLI 客户端不抢锁。
- 本地受控 Producer 仅用于故障/并发测试；真实 DeepSeek Agent 查询并提交、真实 Codex 生产另行验证，保存隔离产物与用量，不写正式库、不发布。
- 运行相关回归，更新 SPEC/启动说明，commit/push。

## 状态

- 已完成：CLI Agent 通过 Studio HTTP 提交同一 ContentRun；默认模型不可再调用绕过 Run 的旧生产工具。旧内部调用兼容保留。

## 验证记录（2026-09-07）

- `tests.smoke_agent_studio` 通过：真实 HTTP + 隔离 SQLite 验证目录、重复提交、busy、版本冲突、失败/中断/取消/返工不隐式重启、未知网络结果不重试；Studio 持锁期间，实际 CLI `--agent` 子进程可进入和退出。
- 受控 Producer 只用于状态/并发测试；MockTransport 只注入断网和丢响应，不作为真实生产证据。
- `tests.live_agent_studio` 通过：真实 DeepSeek 依次调用目录工具与 start_content_run，真实 Codex 生成两张 MCP 图片，Run 到达 `awaiting_approval`；重复提交仍是一条 Run、一次生产尝试。没有批准或发布。
- 隔离证据目录：`tmp/agent-studio-live-20260907-204240/`（未提交），包含 studio.db、outputs、report.json、producing.png 和 completed.png。Run：`38e594d6-1787-471e-9926-0892d947cab5`。未修改正式运营库。
- 浏览器真实验收：生产中和完成页均截图检查；重开隔离数据库后仍显示同一 Run、两张可读取图片、文案和待批准状态。
- DeepSeek 共 5 轮，累计输入 21,185、输出 781 tokens，缓存命中 16,384；Codex 输入 244,546（缓存 199,296）、输出 4,653 tokens。这是单条真实样本，不是性能 Benchmark。
- 回归通过：smoke_codex_producer、smoke_studio_run_api、smoke_agent_skill_selection、smoke_agent_task_tracking、smoke_agent_navigation、smoke_agent_events、smoke_cli_menu、smoke_runtime_context、smoke_route_and_answer_skill；compileall 与 git diff --check 通过。

## 后续观察，不在本轮扩张

- 本次 Agent 在查询目录之前额外读取两份 Skill，可作为工具路径效率评估样本；先记录，不据此引入新的路由框架。
- 文件验收不等于内容正确：本次文案出现一个错字，应由用户验收/返工处理，不能将 awaiting_approval 宣称为内容质量通过。
- 下一步再让 Web 对话复用 Agent 的同一工具与业务链路；本轮不宣称 CLI/Web 对话及 Session 已完全统一。
