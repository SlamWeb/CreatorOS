# 单 thread 文件交付生产

状态：2026-10-02 已实现并完成真实一图试产；最后回归结果见下。

## 范围与原因

- 一份或一组本地 Skill 由同一个 Codex thread 完成；内容/呈现分工是任务说明，不再由两个 thread 和强制 PageSpec JSON 交接。
- 复用 ContentRun / Revision / Attempt、执行锁、事件进度、SocialContentPack、人工审批。先只支持图片交付，不实现通用视频管线或产物转 Skill Web 工具。
- 新输入保存 production_protocol=native-v1；缺少字段的历史输入仍走 legacy，不改旧快照、图片和审批摘要。新协议组合只检查角色/文件与最终图文契约，不按名称或 GitHub 来源白名单选择。

## 最小协议

- 开始时冻结选中的 Skill，显式提供 SKILL.md 绝对路径及 SkillInput；整个生产库不加入全局发现目录。沿用 gpt-6-luna / xhigh、禁用生产记忆和项目文档自动注入。不宣称已经屏蔽所有原有全局 Skill。
- Codex 在本 Attempt 的独立 work 子目录中用 workspace-write 工作；宿主 checkpoint/最终包存父目录。内容稿可以是 Markdown 或 Skill 自己的格式。
- Codex 持续写 work/delivery.json：title、text、hashtags、complete、artifacts。每项只包含 order、source_image_path、image_prompt、reference_assets、可选 content_file；不要求 PageSpec 或小白角色资源。
- 宿主持续核验当前 thread 的真实图片路径、解码与顺序，逐项保存到 partial-images；保存 input digest、thread id、图片 checksum 和 Prompt/内容证据。文件未就绪不计为完成。
- SDK 事件用于最近活动，已验收文件数用于产物进度；不能把工具调用次数作为图片数。最终成功要求 turn 完成、delivery.complete 与全套文件验收均成立。
- 同 Revision 技术恢复只读取同 Run 的已验收 checkpoint，并优先 resume 原 thread；没有 checkpoint 才新建。新选题、新 Revision 默认新 thread，显式返工说明不隐式带入别的任务。
- 新 Revision 可以显式携带上一 Revision 核验过的内容/Prompt/发布稿，不带旧 thread 或旧图路径；第一次 Attempt 请求固定，技术重试不因前稿变化而漂移。SDK thread 累计 usage 保存 checkpoint，各 Attempt 只记相对于起点的增量，离线完成导入不重复记模型消耗。
- 最多一次无生图的交付索引修复，不自动重跑付费生图。失败/取消后文件保留，重启不能伪造仍在生成；拒绝恢复输入变化或篡改文件。

## 本地测试 Skill

- 新增 english-word-scenes：从参考图提炼“易混词的具体场景对比 + 一组统一动漫分镜”，是一份端到端 Skill。口述/本地作者可直接登记本地目录，不必先 push GitHub。
- 源文件放 production-skills/，安装进 data/producer-skills 的版本/working/registry；不放 creatoros/skills 或用户全局技能目录。
- 只添加这份 Skill 到正式本地库，不改已有栏目。真实试产使用隔离 SQLite / 输出目录，1 张六格图，词组 look / see / watch / stare / glance / gaze，人工验收、不批准、不发布。

## 验收

1. 本地 Skill 登记/重放/修改/路径与资源校验；已有 GitHub 安装回归。
2. 单/双 Skill 同 thread 输入；非白名单组合可创建新协议 Run；旧 Run 协议不变。
3. delivery 增量落盘、坏图/跨任务路径/Prompt 缺失拒绝；中断恢复不重复已有图；缺索引修复不生图；取消/超时落终态。
4. 同一 HTTP/Agent/网页读取进度、部分图片和最终包；审批摘要覆盖 Skill 与生产证据。
5. 真实 SDK 一图试产 + 图片人工检查；恢复故障用本地注入，不反复付费生图。若额度/服务阻塞，记录真实状态，不宣布通过。

## 实现接线

- ContentRunInput.production_protocol: legacy（缺省历史）/ native-v1（新建）；服务允许测试显式选择 legacy。
- integrations/native_production.py 提供 produce_native(producer, **request)、load_checkpoint(directory, digest=None)、verified_pages(directory, checkpoint)、evidence_files(directory, composition=None)。load_checkpoint 从 production_request.txt 与 skills 子目录核验输入；返回 pages，每页具有 order/image_path/sha256/warnings/image_prompt/content 属性。
- runs.validate_artifact 增加 production_protocol 参数（默认 legacy），native-v1 必须核验并摘要 native checkpoint/Skill/内容证据。Web 单/双 Skill 都能显示 native 部分产物与 Prompt；不改变页面组件。
- legacy adapter 原样保留；新的组合快照使用 native-carousel-v1，仍冻结两份本地文件，不强制其内容产出格式。

## 结果

- 本地登记已提交 `048202b`：正式库仅新增 `english-word-scenes--bd89b5d9cb32a407`，未改现有账号/栏目。原件及可编辑 working 副本保持分离，没有写用户全局 Skills。
- 真实试产：`tmp/native-production-trial-20261002-b`（独立 SQLite/产物），Run `075614dd-2080-451d-96a8-c81c33208d84`，单 thread `01a0fcc1-b13c-7e22-92aa-078eff99330c`，gpt-6-luna / xhigh。约 276.5 秒，一张 1024×1536 六格真图，2,430,973 bytes，进入 awaiting_approval；一次正常交付，无索引修复、无额外重画、无批准或发布。六词为 look/see/watch/stare/glance/gaze，内容稿保存词典链接，Prompt 由生产器报告，不声称拿到图像服务内部改写后的 Prompt。
- SDK 报告本线程累计 input 459,058、cached 405,248、output 11,632、reasoning 7,783；input 是多次模型调用累计，不是单次上下文长度。原始结果保存在 production_session.json/native_checkpoint.json/codex_trace.jsonl；未提交私有运行材料。
- 浏览器真人式操作（隔离 8876）：打开验收页 → 展开内容稿与 Prompt → 放大图片 → 返回 → 刷新，同一产物仍待批准且文件检查通过。原图已目视检查：六格与文字清晰、角色和视觉风格一致；不把这等同于语言教学有效性评估。HTTP 负例覆盖坏 checksum、冻结 Skill 被改后拒绝，以及查询不增加 Run version。
- 试产 init 脚本第一版漏传 series.description，尚未调用模型就失败；补齐后在新隔离目录重建，第一目录保留作诊断。旧目录没有正式数据，也没有付费产物。
- 尚未证明单 thread 质量/速度优于双 thread；只有单 Skill 一图的真实 SDK 测试，双 Skill同线程与故障恢复用受控 transport。未完成严格全局 Skill 可见性隔离：显式路径、禁记忆/项目文档不等于屏蔽所有已安装全局 Skill。单线程也不等于图像并发。
- 逐项验收的图片才计入部分进度；制作中 partial 页面暂只显示图片，最终产物可看内容与 Prompt。本轮保留旧 DTO，无主观质量自动打分。
- 恢复时已验收图直接按 checksum 复用；同一 thread 已生出但来不及 checkpoint 的图，可从保留的原工作稿修复索引后重新走路径/解码/Prompt 核验，不强制为同一结果再付费重画。原始未核验索引从不直接变成最终包。

### 复跑与查看（PowerShell，仓库根）

```powershell
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.live_native_production status --directory tmp/native-production-trial-20261002-b
& 'D:\Anaconda4.7g\envs\deepcode\python.exe' -m tests.live_native_production serve --directory tmp/native-production-trial-20261002-b --port 8876
```

`serve/status` 不生产；新的收费试产需对新 tmp 目录显式 `init` 然后 `produce`。失败/中断的同 Revision 可显式再次 `produce`，恢复校验好的文件；已经待批准的任务不应重复生产。现有 Studio 服务需要重启才加载新代码。

### 自动回归

- `smoke_native_production`：单/双 Skill 原生输入、同 thread、部分失败/同 Revision resume、已完成页不重画、一次索引修复、取消/超时、输入/图片/资源篡改、路径逃逸、BOM、累计 usage 转 Attempt 增量、离线恢复零用量。
- `smoke_native_run_wiring`：任意本地双 Skill，无 GitHub/character.png 限制；旧输入协议不变；新 Revision 显式前稿与重试上下文冻结。
- `smoke_native_artifact_web`：最终与部分产物、Prompt/内容、坏 checksum、Skill 篡改、ZIP 摘要、只读 version 稳定。
- 回归通过：local/Git producer skills、production_sessions、production_progress、content_run_service、pair_production、receipt_recovery、series_guards、studio_executor、studio_partial_cards、studio_production_progress、studio_run_api、visual_checkpoint、studio_api、topic CRUD。历史受控 Producer 夹具明确选择 legacy，不把假图片当 native 真交付测试。
- Web typecheck/build、受控 Playwright `production-progress.spec.ts + studio-workflow.spec.ts` 10/10 通过（34.8 秒），包括内容单字段/Prompt 单字段/均空回归；桌面与 390px 手机截图在 tmp/testresults。最小 UI 修复：不能因为 Skill 没交固定格式内容稿就把已保存 Prompt 隐藏。生产时 partial 仍仅图片，未暗中扩 DTO。
- 既存警告：Starlette/httpx 兼容层弃用提示、Vite 大 chunk 提示。没有为本轮更改依赖；自动回归无调研、生图或发布费用。
