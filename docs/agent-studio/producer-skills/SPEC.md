# Codex 安装与栏目 Skill 绑定

## 当前行为与接入验收（2026-09-11）

- 下文 2026-09-08 的 Codex 安装描述为历史方案。当前 `GitSkillInstaller` 直接下载 GitHub 文件并检查 `creatoros-output: social-content-pack.image-carousel` 声明；安装不调用模型，不消耗 Codex 额度。内容生产使用 Python SDK，选题调研仍使用 CLI。
- 本轮补齐真实 GitHub 安装 → 真实 DeepSeek 查询 → 宿主确认绑定 → 刷新读取绑定 → 过期确认拒绝的隔离 HTTP 验收；新旧 Run 的 Skill 快照由已有 smoke 回归覆盖。
- 未改前端和正式运营数据；本轮不生成图片。此验收不等同完整内容生产链路或 Agent Benchmark。
- 真实 GitHub 安装通过：提交 `718662d` 的内置轮播 Skill 注册为 `knowledge-to-carousel--88350dbe3dee58c2`。随后真实 DeepSeek 查询、HTTP 显式绑定、读取新绑定和过期确认 409 全部通过；证据在 `tmp/producer-skills-live-20260911-013424/result.json` 与 `studio-result.json`，未提交本地会话数据。
- `python -m tests.smoke_producer_skills` 通过：旧 Run 保留旧 Skill、新 Run 使用新版本、条件绑定、篡改拒绝和重复提交回归通过。此次为 HTTP/数据库与模型工具轨迹验收，没有新增浏览器视觉验收。

## 实现边界（2026-09-08）

- 延续七天准备目标：先统一 Web/Agent 的实际业务能力，再补选题研究和任务级 Eval；本轮只完成生产技能接入，不扩展发布、记忆或市场自动搜索。
- Codex 在独立 workspace-write 目录下载 GitHub 仓库、读取 Skill 并给出结构化回执；不执行仓库脚本、不安装依赖、不生成图片。不安装到用户全局 Skill 目录。
- CreatorOS 核验实际 Git origin/commit 和 Skill 文件，登记不可覆盖的版本 ID。原始文件与注册表放数据库旁的 producer-skills 目录，隔离库不共享正式安装。
- 安装 POST 返回任务句柄，GET 查询状态；停止/重启不自动重复付费。复用现有 Codex JSONL/超时/进程树回收。
- CLI/Web Agent 通过相同 Studio API 安装、查询；绑定由栏目页面显式确认，带旧 skill_name 条件更新。不让模型伪造“已绑定”。
- 已有 ContentRun 的 skill_name 是输入快照，新版本绑定只影响新 Run；生产器按版本 ID 查找文件，Manifest 记录实际使用 ID。内置 knowledge-to-carousel 向后兼容。
- 当前只接受图片轮播生产契约；非轮播技能可以登记，但不可绑定生产。Codex 的兼容判断仅是预检，不等于真实生产验收。

## Python SDK 接线（2026-09-09）

- Skill 的下载、Git commit/digest 固定和栏目绑定仍由 CreatorOS 完成；这一步不再把“安装 Skill”理解成安装到 Codex CLI 全局目录。
- ContentRun 默认通过 `CodexSdkProducer` 调用本机 Codex app-server，使用 `SkillInput(name, path)` 把已核验版本传给 Codex。CLI 仅保留在 topic research/兼容路径，不参与新的内容生产默认路径。
- SDK 使用 `gpt-5.6-luna` + `xhigh`，复用本机 ChatGPT Plus 登录态；若本机未登录或额度耗尽，Run 必须落为结构化 `codex_sdk_failed`/`codex_usage_limit`，不能伪造完成。

### 真实 SDK smoke（2026-09-09）

- 隔离 `openai-codex==0.147.0` 已确认 SDK 能看到本机 `chatgpt / plus` 登录态，并接受原生 `SkillInput`；本次真实 turn 因官方 usage limit 被拒，未触发生图或发布。

## 实际入口与存放

- Agent 新增 install_producer_skill、get_skill_install、list_producer_skills，Web/CLI 共用 StudioClient，不连接 GitHub MCP。
- 栏目页面「管理生产 Skill」：输入链接提交后台安装 → 查看兼容性与 Git 版本 → 选择并确认绑定。只通过 Web 确认绑定，本轮不开放模型直接改绑。
- 同链接重复提交返回已有任务。失败/中断仅在 retry=true 的明确操作后新开 Attempt；旧 workspace/Trace 保留。已安装版本不自动更新，需要提供新 commit 的 tree 链接，安装后再确认改绑。
- 默认目录 data/producer-skills：jobs 保存任务状态，work 保存独立 Codex workspace/JSONL，versions 保存已核验 Git 文件，registry 保存元数据。不是用户全局 Skill 安装，不改变其他 Codex 项目的技能。
- 外部版本以 name--versionhash 存入现有 Series.skill_name，ContentRunInput 已有快照自动保留该 ID；无需新迁移。内置 knowledge-to-carousel 保留原先仓库文件读取兼容行为。
- GitHub URL 支持仓库根或 tree/ref/path；ref 目前不支持带斜杠的分支名，遇到多 Skill 仓库请指定具体路径。只安装 Git 已提交文件，拒绝越界/符号链接和超过 32 MiB 的 Skill 包；不自动安装依赖。
- 官方调用约定参考 https://learn.chatgpt.com/docs/non-interactive-mode 。复用已有 JSONL 解析、output schema、ProcessTree 和取消/超时回收；安装单独 workspace-write，生产仍保持原权限。

## 验收计划

- 隔离 SQLite：安装目录/元数据、重复提交、冲突、坏路径、非轮播拒绝、绑定及旧 Run 快照保留。
- 真实 Codex 下载一个已知公开 Skill，验证回执/文件/commit；真实 DeepSeek 确认可见安装查询工具。不生图、不发布、不写正式业务数据。
- 浏览器检查安装状态及确认绑定；关联回归、typecheck/build、更新记录、commit/push。

## 验证结果

- smoke_producer_skills 通过：真实临时 Git/SQLite 测试 URL/文件核验、动态 Prompt、条件绑定、旧/新 Run 快照、非轮播拒绝、篡改拒绝、重复提交和显式重试。故障注入用本地受控 Installer，不冒充真实模型。
- 真实 Codex 下载 SlamWeb/CreatorOS 的 knowledge-to-carousel，实际 commit 5764c1df7d70c553b7545414a7c008ef1b4b779c。首次回执多了 source/ 前缀导致核验拒绝；修复受控前缀兼容后，用同一次真实 JSONL 和 Git 文件复验通过，没有为路径修复再次调用模型。
- 真实安装证据：tmp/producer-skills-live-20260908-113359/result.json、producer-skills/work/*/codex_trace.jsonl。Codex input 255332 / cached 225792 / output 3444；安装任务误读父仓库开发规则增加了无关工作，已加入独立 Git 边界与安装 AGENTS，隔离边界通过本地测试，尚未再次付费测量节省量。
- live_skill_studio 通过真实 localhost + DeepSeek：查询已安装 Skill，确认实际调用 list_producer_skills，回答引导用户确认绑定；HTTP 重复安装提交返回同一既有结果，没有新增 Codex 调用。证据 studio-result.json 和 creatoros-agent-sessions。
- 本次 DeepSeek 两次模型调用合计 input 3497 / output 449；没有用模型描述替代实际工具轨迹验证。
- 浏览器使用同一隔离库的真实安装结果：展开面板、选择 Git 版本、显式确认、刷新后仍保留；390px 画面已检查且无横向溢出，修复原生 select 的白色样式并复验。正式账号、栏目、选题没有修改。
- 回归 smoke_codex_producer、smoke_content_run_service、smoke_studio_operations、smoke_studio_api、smoke_studio_run_api、smoke_web_agent、smoke_agent_studio 通过；前端 typecheck/build、Python compileall 通过。
- 补充 smoke_studio_process 和 smoke_studio_executor 通过：真实本地进程树超时/关闭回收、忙碌/认领/恢复/晚到结果回归通过。临时浏览器及 8893 测试服务已关闭。
- 本轮未调用生图/发布；验证了绑定传入 Prompt 和 Manifest 的代码接线，不声称完成新 Skill 的真实图片生产质量验收。

## 下一步（七天准备主线）

1. 按栏目的定位/受众/绑定 Skill，让 Codex 返回候选选题列表；CreatorOS Preview 后确认入队，复用已有 Topic/Operation，不另建队列。
2. 选择一个选题走既有 ContentRun → 产物 → 人工验收，形成可演示最小闭环。
3. 将真实使用中的指代、批量选择、重复提交、失败恢复整理为小型任务 Benchmark；先评估最终状态是否正确与 Token Cost，再按 badcase 优化工具和上下文。暂不扩张发布、复杂记忆或市场自动搜索。
