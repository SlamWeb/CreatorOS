# Codex 安装与栏目 Skill 绑定

## 已安装 Skill 在线编辑（2026-10-06）

- Skill Inspector 与 Agent 共用受管 `working/<id>` 文件浏览；新增文件更新仅接受已有文件清单中的 UTF-8 Markdown/文本，固定内置 Skill 不可写，versions 导入原件与历史 Run 快照不变。
- 列表/文本读取返回整个工作目录 SHA-256 `digest` 与 `editable`；`PUT /api/producer-skills/{id}/files/content` 接收 `{path, content, expected_digest}`，检查并发 digest、单文件 512 KiB 限制和合法 SKILL.md name/description frontmatter，使用同目录临时文件原子替换。编辑校验新增依赖 `PyYAML>=6,<7`，只对写入的 SKILL.md frontmatter 调用 `yaml.safe_load`；name/description 必须为合法字符串并满足现有 name/长度约束。为确保原有读取器显示一致，多行块标量会明确拒绝；普通引号字符串可保存。该依赖不参与全局 SkillLoader 行为。成功后 `describe()` 读到更新后元数据；过期 digest 返回 409 和 `current_digest`。
- CreatorOS Agent 的既有 `get_producer_skill` 支持 `list_files=true` 列表、传 `path` 读取单文件；新增 `update_producer_skill_file`。编辑工具只在用户明确授权后调用，一次仅保存一个文件；对话先告知共享 Skill 的其他绑定栏目也会受影响。账号 scope 仅允许读取/编辑绑定到当前账号栏目上的 Skill；跨账号拒绝。
- `tests.smoke_producer_skill_edit` 使用临时 SQLite、HTTP 和工作目录覆盖新旧 digest、合法/非法元数据、原子更新、来源原件/历史快照不变及跨账号拒绝；无需模型或正式库。Deepcode Python 下通过；现有读取/安装/生产快照回归待合并前顺序复跑。
- 首轮真实 Agent 报告 `tmp/live-producer-skill-edit-20261006-222156/report.json` 暴露旧行解析器未拒绝 YAML 冒号语法；已在历史报告中如实标记该校验缺口，不再把它作为 YAML 合法性证据。补充 safe_load 校验后以不含 YAML 特殊冒号的精确描述重跑一次通过：DeepSeek `deepseek-v4-flash`，12,194 tokens，四种工具调用及本地 HTTP 写入均成功，versions 与历史快照不变。最终报告：`tmp/live-producer-skill-edit-20261006-222632/report.json`。

## Skill 文件编辑与使用来源 · 2026-10-06（完成）

- SkillInspector 编辑 markdown/text 文件，使用目录 digest 做乐观并发；409 时保留页面草稿，读取当前正文供用户对照后再显式覆盖。关闭、Escape、切换文件均确认未保存草稿。
- 本地库 Skill 入口可把身份/路径/用户要求预填至现有 Agent 会话，不自动发送。Run 详情依据 `input_snapshot` 展示创建时冻结的 Skill 与摘要，可打开当前库版本；界面说明保存会影响后续 Run，历史 Run 不变。
- 验收：SkillInspector 隔离 Playwright 10/10 通过，相关账号聊天/Agent布局/回复Trace/工作流 5/5 通过，最终相邻回归 3/3 通过；`npm run typecheck`、`npm run build` 通过。1440×900 和 390×844 编辑态、Run 来源截图经人工复查，留档于 `web/tmp/skill-edit-ui-20261006/`。工作流测试只用隔离确定性 Producer，不调用真实模型或正式服务；不改正式库/已登记 Skill。

## 本地 Skill 直接登记（2026-10-02）

- `ProducerSkillCatalog.register_local(directory, *, role, source_note=None)` 允许本地作者直接登记含根级 `SKILL.md` 的目录，不需要 GitHub、Codex 安装器或模型调用。
- ID 根据规范化源目录身份和初始目录 digest 稳定派生；登记时把初始字节保存到 `versions/<id>`，沿用同一 registry/working 结构。重复登记同一源版本不会替换已编辑的 `working/<id>`；源文件有变化时生成另一个版本 ID。
- 元数据记录 `source_kind=local`、`github_url=null`、`commit=null` 和必填角色；可选记录 `source_note`。仍通过 `SkillLoader` 校验名称/描述，通过 `_inspect_checkout` 识别图片轮播声明。符号链接、越界路径、非普通目录/文件、超过 32 MiB 或 2000 项的输入拒绝。
- 本地登记不改变 GitHub `register()` / `SkillInstallService` 行为，不安装全局 Codex Skill；working 目录仍通过现有 catalog 读取与编辑。
- 验收：`smoke_local_producer_skills` 与 `py_compile` 通过，覆盖普通制作 Skill、mind Skill、源内容变更产生新 ID、原件/working 分离、重放不覆盖、坏 frontmatter 和符号链接资源拒绝；测试无模型调用。最初默认 Python 缺少 `rich`，随后使用项目 deepcode Python，`smoke_producer_skills` 既有 GitHub 安装/working 编辑/冻结回归通过，没有改环境依赖。
- 已按用户授权把 `english-word-scenes--bd89b5d9cb32a407` 登记到正式本地库的 working 目录；没有改现有栏目绑定，也没有写用户全局 Codex Skills。测试账号/选题/产物在 `tmp/native-production-trial-20261002-b` 隔离库。

## 本地工作副本（2026-10-01，当前行为）

- 下方版本锁定/篡改拒绝为历史行为。现在 `versions/<id>` 保留 Git 导入原件，`working/<id>` 是可编辑调用目录；`work/` 仍是安装任务临时工作区，不要混淆。
- `list_producer_skills` 返回当前名称/描述、`local_path`、当前 `digest` 与导入 `source_digest`。栏目可传目录条目或该本地路径；数据库保留稳定别名，编辑本地文件无需重新安装、改绑或 push。
- 首次执行时冻结，之后恢复/返工不读取新工作副本；历史 Run 优先沿用旧 Attempt 的实际文件。导入重放不覆盖已编辑副本。单/双 Skill 路径绑定、当前内容读取、恢复和旧 JSON 兼容均已回归；详见 integrations/runs SPEC。
- 可编辑不等于免校验：frontmatter、角色、当前生产契约、路径和资源依然检查。只是正文/描述修改不再要求等于导入 digest。不开放任意未登记路径，也不安装到全局 Codex 目录。

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
