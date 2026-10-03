# Artifact → Skill Workbench

## V3 本轮实施：Codex 直接写文件（2026-10-03）

- 四种提炼 Prompt 使用用户给出的四段原文，只追加来源、用户要求与输出路径；不显式调用 skill-creator，不向模型暴露内部 `legacy_end_to_end` 角色，也不要求模型返回整份 JSON Skill。
- 提炼与改稿使用 `gpt-6-sol/high`、fresh thread、workspace-write，在 CreatorOS 自己的 `catalog.root/extractions/jobs/<id>/draft/` 或本任务 `revisions/<request-hash>/draft/` 直接写文件。single 写 `skill/`，mind 写 `mind/`，visual 写 `visualize/`，pair 写后两者。不写用户全局 Codex skills。
- 宿主读取这些目录的 SKILL.md 与配套文件，沿用格式/路径/大小校验，生成不可变 `versions/vNNN/<role>/` 供现有网页读取；不再从模型 JSON 重建整个 Skill。assets、references 等配套文件保留到改稿、手动编辑、试产快照和确认入库。
- 改稿从当前完整文件快照开始，失败不移动当前版本指针；保留原稿与记录。页面/API 的 DTO、digest 确认、显式试产与显式入库不变。文件落在库的草稿区，不代表已经正式登记可组栏目。
- 验收：四种 Prompt/模式目录、缺文件/非法元数据/越界拒绝、配套文件编辑改稿保留、SDK 写权限且无强制 SkillInput/output_schema；一次真实图片提炼与改稿（不生图）验证实际文件、Trace、原资产与隔离入库。

### V3 验证结果

- `smoke_skill_draft_files` 通过：原生目录与 DTO 映射、缺失/无效 Skill 拒绝、纯文案默认文字目标、链接越界拒绝；配套 references 文件经版本发布、文件 API、手动编辑与确认入库保持不变。受控 SDK 证明只传 TextInput/LocalImageInput、cwd 为本次 draft、workspace-write、无 output_schema，读取实际文件而非解析最终回复。此部分为接线/故障注入测试，不声称模型质量。
- `smoke_skill_extraction`、`smoke_skill_extraction_tools`、`smoke_skill_workbench`、`smoke_web_agent`、`smoke_native_production`、`smoke_native_run_wiring` 通过；compileall 与 diff-check 通过。网页 DTO/组件未改，复用现有文件树与版本工作台；本轮未重复浏览器视觉验收。
- 真实 `gpt-6-sol/high` single 提炼、改稿、隔离入库通过，证据 `tmp/skill-extraction-live-b0ppu9ny/`。提炼 thread `01a10242-5a69-7543-aa73-627b06a5becd`，改稿 thread `01a10244-324b-76f2-b477-fcd1f610cdc8`。Codex 实际写 `draft/skill/SKILL.md` 与 assets/reference-01.jpg；普通说明作为最终回复，草稿版本 2，原图摘要与入库文件相同。追加只读证据重验通过，不重复付费请求。
- 提炼累计 usage input 198,494 / cached 166,272 / output 4,857；改稿 input 242,383 / cached 217,728 / output 4,967。这是各阶段多个内部模型调用的累计量，不是单次上下文长度。没有观察到生图、搜索或委派事件；未评价新题材试产质量，未改正式数据或全局 Codex Skill。
- 真实提炼约 122 秒、改稿约 113 秒，均在现有 180 秒期限内；不因此承诺所有作品都在此时间完成。不增加隐式重试；失败后的原文件和已有有效版本继续保留。

以下 V2 及更早结果是历史记录；提炼输出协议以 V3 为准。

## V2 本轮实施范围（2026-10-03）

- 默认 single，保留 pair/mind/visual；支持 1–6 图与/或 source_text（最多 20,000 字符）。完整 Skill/呈现 Skill 保留原图 assets，Mind 不自动复制原图，避免不必要视觉影响。
- 工作台：来源上传与缩略图/文案 → 提炼 → 文件导航（SKILL.md、assets）与 Markdown 预览/编辑 → 保存草稿或输入要求由 Codex 改稿 → 预填“沿用参考作品的内容试做，保留内容与呈现特点。”可编辑选题 → 主动试用 → 满意后确认入库。换 IP 本轮不实现。
- 所有 Skill 均留在 CreatorOS catalog.root/extractions 草稿区，入库为 catalog 本地副本，不写全局 Codex skills；试用时显式 SkillInput 与冻结路径。正式 creator/series/topic/run 表零写入。
- 编辑 POST /{id}/draft：expected_digest、skills；改稿 POST /{id}/revise：request_id、expected_digest、instruction；试用 POST /{id}/trials：request_id、expected_digest、topic。新增任务操作只串行占本提炼 service 的 worker，取消沿用 /cancel；试用/改稿可见 operation。
- 文件 GET /{id}/files?role=...&path=...；试用文件 GET /{id}/trials/{trial_id}/cards/{order}。get Job 增加 files（role/path/kind/url）、suggested_topic、revision、operation、trials（id/status/digest/topic/error/cards/progress/thread_id）。旧 Job 可兼容读取。
- 草稿直接编辑和改稿产生新 digest；保存/试用/入库均校验用户所见版本。运行中禁编辑；改稿失败保留此前有效草稿。旧结果保留、标明其 digest 与当前是否一致；同 request_id 同参数重复调用不重复付费。
- 文本目标可入库但不冒充图片能力；图片试用仅完整/呈现或双 Skill 图片目标。当前生产仍为 gpt-6-luna/xhigh；提炼/改稿为 gpt-6-sol/high。
- 复用 native-v1 Producer/Checkpoint/Progress，不自动重画或试用，不自动批准/发布；浏览器返回/刷新查询原任务。仅用户点击试用后调用真实生产。
- 验收：隔离 HTTP 编辑/改稿/试用/旧摘要/失败/取消/重启/重复请求/文件范围与完整资产；真实无生图 Codex 提炼/改稿；受控浏览器完整操作和桌面/手机视觉检查。用户后续用抖音作品做真实生图质量验收，本轮不擅自消耗完整生图批次。

### 当前存储与操作

`catalog.root/extractions/jobs/<id>/` 保存来源与请求、原始响应、Trace；`versions/vNNN/<role>/` 是每次保存的新草稿，包含 SKILL.md 与 assets。`revisions/<request-hash>/` 是改稿记录；`trials/<request-hash>/skill-snapshot/` 固定本次用的 Skill，`revision-001/attempt-001/` 保存生产进度、checkpoint、Prompt、图片和 SocialContentPack。确认入库才由 catalog 创建正式 working 副本。

Skill 页「从作品提炼」提供文件树、Markdown 预览/编辑、参考图查看、改稿要求与可编辑试产选题；旧试产仍保留，但草稿摘要改变后标为旧稿结果。取消/服务重启不自动重试，也不伪装继续生成。本轮暂不提供试产恢复按钮；中断后保留已落盘图片，用户显式新试产会开启新任务，并可能重新消耗生图额度。

本地未保存编辑按任务 ID 在 sessionStorage 暂存，刷新/侧栏导航后可以恢复；服务器摘要变化时保留编辑并提示冲突，不静默覆盖。保存/明确丢弃/重新载入清理暂存。当前浏览器会话结束不保证暂存仍在，提交服务器草稿才是持久化；存储不可用时提示先保存。未保存编辑时禁止另开提炼，避免已经付费提交却被任务切换保护拦回旧稿。

仅 SKILL.md 直接编辑；assets 可查看，替换 IP 本轮明确不做。Mind-only 和纯文字草稿可提炼/编辑/入库，但当前生产器仅支持图片轮播，因此不能单独试产；Mind 入库后可在栏目组合制作 Skill。试用结果不是提炼质量自动评分，是否满意由用户判断。

### V2 后端及真实接口验证（2026-10-03）

- 隔离 HTTP/SQLite 测试通过：默认 single、图文/纯文案、文件读取与越界拒绝、编辑新版本/旧摘要拒绝、改稿失败保留、试产冻结草稿、重复请求不重复调用、取消与服务重启中断；确认前 catalog 不新增正式 Skill，运营 Creator 数据为零。图片试产使用受控 Producer，未声称真实生图通过。
- 真 `gpt-6-sol/high` 图片输入提炼 + 改稿 + 隔离入库通过，证据 `tmp/skill-extraction-live-z7djp3r2/`；改稿 thread `01a10204-d1da-7f02-93cc-546168797dd9`，草稿 revision 2，原图复制/格式验证/图片生产能力声明通过。提炼 usage input 54,159 / cached 25,344 / output 2,364；改稿 input 56,233 / cached 45,056 / output 2,220。没有生图/搜索调用，未评估迁移生产质量，不改正式数据。
- 第一次真实调用被 SDK 拒绝：新增默认字段未包含于严格 schema 的 required；修复为顶层与 DraftSkill 的全部属性必填后重跑成功。业务 HTTP 输入仍保留默认值兼容旧调用。真实测试脚本现分别统计提炼与改稿 usage/trace，不能把前一阶段 usage 当作整轮总量。
- `smoke_skill_extraction`、`smoke_skill_workbench`、`smoke_skill_extraction_tools`、`smoke_web_agent`、`smoke_native_production`、`smoke_native_run_wiring` 六项通过；compileall/typecheck/build 通过。Vite 的单 bundle 超 500kB 提示非阻断，本轮未做整站分包。
- 视觉修复：ready 后大上传表单曾把工作台推到首屏之外，现默认折叠为「新作品」；frontmatter 曾被 Markdown 误解析为大标题，现以小号代码区展示。参考图与试产图均应验证 naturalWidth 大于 0，不能仅凭 img 标签存在通过；浏览器的受控图片/提炼不代表真实图片质量。
- 定向 Playwright 7/7 通过：上传/文件树/实际参考图加载/刷新/入库、编辑后刷新恢复及旧试产版本标记、原文与要求分开提交、Codex 改稿请求、响应丢失复用请求、提炼失败、显式取消。改稿不自动保存或试产；试产图片增加实际加载断言。浏览器 API 与图片为受控夹具，后端 HTTP/SQLite 另有真实服务隔离测试，模型接线由上述真实 Sol 探针证明。
- 已检查 1440×900 与 390×844 的 ready 和 trial 截图：文件树/正文分层、手机堆叠、按钮与滚动范围无阻断；无横向溢出。证据 `web/test-results/skill-extraction-*/skill-*.png`（忽略目录）。执行命令：`$env:CREATOROS_PYTHON='D:\Anaconda4.7g\envs\deepcode\python.exe'; cd web; npx playwright test skill-extraction.spec.ts --timeout=20000 --max-failures=1`。

以下保留为 V1 历史记录；与 V2 冲突处以以上当前设计为准。

## Scope (2026-10-02)

从参考图片提炼可复用 Skill。默认 Mind + Visualize 两份，也支持仅 Mind、仅 Visualize、单一完整 Skill。
本轮接通能力，不承诺提炼质量；不生成新图、不绑定栏目、不覆盖现有 Skill、不注册到全局 Codex。
Web 与 Agent 调用同一服务。草稿可预览，明确确认才入 CreatorOS Skill 库，入库后沿用本地可编辑副本。

## Contract

- `POST /api/skill-extractions/uploads`：`{name, data_base64}` → `{id, name, url}`。PNG/JPEG/WebP，单图最多 4 MiB；不接任意路径 URL。
- `POST /api/skill-extractions`：`{request_id, upload_ids, mode, instruction}` → Job。mode 为 `pair|mind|visual|single`；1–6 张图，instruction 可空；request_id 幂等，重复请求不重复调用 Codex，参数不同返回 409。
- `GET /api/skill-extractions` → `{items: Job[]}`；`GET /api/skill-extractions/{id}` → Job。
- `POST /api/skill-extractions/{id}/save`：`{expected_digest}` → Job。ready 才可入库，同摘要重复保存返回原结果。
- `POST /api/skill-extractions/{id}/cancel`：`{}` → Job。停止本任务，不影响生产/其他会话。
- Job：`id, request_id, mode, instruction, status, created_at, updated_at, uploads, thread_id, error, note, skills, digest, saved_skills, progress`。
  - status：`running|ready|saved|failed|interrupted`。
  - skills：`[{name, role, skill_md}]`，role 与已有 catalog 一致：mind / production / legacy_end_to_end。
  - saved_skills：既有 catalog describe DTO 数组；未保存时为空。
  - progress：可空，沿用 production progress 安全事件投影；不造百分比。

## Execution and storage

每次提炼 fresh Codex thread，请求 gpt-6-sol/high SDK 登录态与独立上下文配置；实际传入 LocalImageInput。只要求文本 Skill 草稿，不联网调研、不生图。180 秒有限等待，允许取消，无隐式重试。
宿主写 SKILL.md 并复制真实参考图到 assets/reference-NN.ext，模型不得决定文件路径；记录原请求、响应、thread、usage、事件与草稿摘要。
位置：与数据库对应的 producer-skills/extractions/ 下；不引入新数据库模型。启动将遗留 running 标成 interrupted，不伪装仍在执行。一次只运行一个提炼任务。
通过既有 register_local 入库；默认不存在绑定、生产、发布副作用。pair 按固定目录幂等注册，部分入库失败可再次确认同草稿，不生成新任务。
制作类草稿由宿主补 `creatoros-output: social-content-pack.image-carousel` 声明，与现有单 Skill 绑定兼容；声明是当前图片产物目标，不是质量结论，不要求固定 PageSpec 或页数。
参考中的偶然内容/格数/页数不是全局硬规则；提炼结论是可编辑推断，不声称还原原作者 Prompt。

## UI and tools

Skill 页新增「从作品提炼」入口；上传缩略图、模式、可选要求 → 提炼 → 可见状态/取消 → 查看 Skill → 确认加入 Skill 库。返回或刷新查询原任务，不自动重提。错误可见且可另起新任务。
Agent 提供 extract_skills_from_artifact、get_skill_extraction、save_extracted_skills、cancel_skill_extraction；提炼与确认分开。CLI extract 接受项目内本地 image_paths（沿用敏感路径规则）或已有 upload_ids，二选一；Web Agent 保留 archive_only_reads 边界，只接受已有 upload_ids。不允许从文本自动推断确认。get 不传 ID 列出最近任务。浏览器 Skill 页直接上传不受项目内路径限制；页面已提交的任务先查询，不重复提交。

## Acceptance

1. 隔离图片上传、四模式、幂等/冲突、坏图/越界/失败/取消/重启、确认摘要及重复入库测试。
2. 单次真实 Codex 图片输入，检查可解析 Skill、真实 assets、thread/usage、无生图，保存到隔离库。
3. 浏览器走上传→提炼→预览→入库，刷新零重复提交；错误/取消路径；桌面与手机截图。受控提炼用于 UI 故障注入，不冒充模型质量验收。
4. 不改正式数据；更新结果，commit/push。

## Results

### 提炼工作台补充决策（历史提案，V2 已实施其选定范围）

- 用户要求将提炼独立强度设为 high，生产/调研不变。仅配置与 smoke 验证，本次不重复模型质量测试；此前真实通过的是 xhigh。
- 草稿需支持查看完整 Skill、编辑、用户主动试用。试用在隔离草稿区执行，不先入正式库；满意后用户确认才入库供栏目组合。后来用户明确暂缓替换 IP。
- IP 身份、画风、表达形式分别描述，换 IP 不默认改内容方法或版式。每次修改/替换资产产生新摘要，旧试用标记为旧版本，不宣称新稿已验证。
- 本轮仅向用户展示新提炼 Prompt 提案，尚未替换运行 Prompt 或实现工作台。

### 2026-10-03 后续设计 Draft：多模态作品 → 可检查草稿 → 确认入库

以下尚未实现；当前仍为图片输入 V1。模型先按用户要求改为 gpt-6-sol/xhigh，生产/调研不动。

本轮模型验证：`tests.smoke_skill_extraction_tools` 通过；真实图片提炼/隔离入库通过，thread `01a101ab-d290-7d02-a57c-748b1ff8db79`，证据 `tmp/skill-extraction-live-8ykbmmox/`。input 54,038 / cached 25,344 / output 3,932（reasoning 2,676）；无生图/搜索事件，未评价迁移生产质量，未改正式数据。

1. 输入：单图、按阅读顺序排列的一组图、粘贴文案，允许图片与文案组合；参考内容与用户提炼要求分开保存。先复用 1–6 图/4MiB 限制，文本建议 20,000 字符，超限显式提示而非截断；不加网页抓取。一个组默认是一件作品，不把相邻页当成多个独立证据。
2. 四种模式不变：内容方法、呈现方式、两者分别、完整 Skill。呈现方式包括视觉，也包括文字口吻/节奏/组织方式；纯文案不能凭空推断角色画风。缺少视觉证据时列待确认项，不替用户编造风格。
3. Codex 一次分析并拟草稿，显式使用 skill-creator。先解释值得复用什么、哪些只是样例内容、哪些是推断，再输出可检查的完整 Skill 草稿。无需另建多 Agent 框架或机械要求每份 Skill 有多份参考文件。
4. 用户查看：每份卡片仅展示名称、用途、将保留的规则、未固定的可变项/待确认推断；可展开完整 SKILL.md 与参考资产。用户可直接编辑草稿或说“不要固定六格、保留双语”请求修改。修改生成新草稿版本，不自动入库。
5. 唯一正式门槛：“确认生成并加入 Skill 库”。确认绑定用户看到的草稿摘要/版本；正式文件由宿主从确认稿落盘，不能确认后再让 LLM 悄悄改写。复用现有 save、digest、register_local 幂等流程；草稿隔离区不算正式库。未确认的结果不能绑定栏目、生产或发布。
6. 原图按用户选定的参考用途保留为资产；原文与出处作为追溯材料，不默认要求照抄。不将品牌/角色身份自动当作可任意复用授权。正文精简；真正生图需要的参考资产必须传给图像模型。
7. 两份 Skill 的分工：内容决定讲什么和教学/叙事关系，呈现决定如何表达；分页/分格仅在内容量与呈现容量间协调，不把示例布局强制成内容数量。用户明确要求优先，无法调和时展示冲突，不默默截内容。
8. 不统一强塞 PageSpec；输出能力根据提炼目标声明。当前 host 一律补 image-carousel 的逻辑必须改为只对真实图片目标声明。纯文本 Skill 可先保存为不可用于当前图片生产的能力，不能为了绑定成功谎报图片能力；文本生产器另行实施。
9. Trace 复用原任务：源文件/文本、用户要求、模型、thread、草稿版本、修改、确认摘要、正式 Skill 路径。一次提炼独立上下文；改稿可延续该提炼线程，但完整确认稿须持久化，不依赖线程记忆。
10. 验收：图/组图/文案/混合四例；未确认库不变；改稿后旧摘要确认 409；重复确认不重复入库；正式文件对应确认稿（宿主能力元数据也需预览）；取消/失败不入库；文字目标不冒充图片目标。提炼质量另用新题材试产验证，不默认耗费生图额度。

参考：Creator Skill Generator 的 analyst→writer 与来源依据（https://github.com/yashwanth-3000/creator-skill-generator）；Design Skill Generator 的可选提炼维度（https://github.com/weareoxd/design-skill-generator）；Visual Style PPT 的风格草案/Style Lock（https://github.com/irenerachel/visual-style-ppt-skill）。借流程，不复制固定文件数、长模板或多 Agent 框架。

### 2026-10-03 提炼指令精简与模型切换

- 按用户要求仅提炼改为 `gpt-6.1-sol/xhigh`；生产/调研维持 `gpt-6-luna/xhigh`，不自动 fallback。
- Prompt 仅注入所选模式的职责，移除 YAML 格式教学；显式传入本机 `skill-creator` SkillInput 与可读取路径，宿主仍负责草稿保存及校验。目标是迁移到新主题的生产能力，不是原图复现。
- 本地四模式服务 smoke 与定向工具 smoke 通过；新增模式隔离、模型隔离与资源路径检查。修正工具测试中已过期的上传 ID 文案断言。输出 schema 的 role 枚举也仅开放当前模式，双 Skill 请求不再提供单 Skill 选项。
- 真实图片探针失败：当前 ChatGPT 登录的 SDK 服务端返回 400：`The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.` 证据 `tmp/skill-extraction-live-0l2_ei2h/`。未生成草稿、未生图、未改正式数据；不能宣称 Sol 或 skill-creator 实际提炼成功。待该调用路径支持请求型号后，用文末原命令续验。

- `tests.smoke_skill_extraction` 通过：四模式、真实图片字节保存、显式确认、幂等/冲突、HTTP 门禁、失败/取消/重启、草稿篡改与部分登记重试。SDK 由受控提炼器替换用于故障注入，不消耗模型。
- `tests.smoke_skill_extraction_tools`、`tests.smoke_web_agent` 通过：工具注册/参数/Studio transport/路径边界，未调用真实 DeepSeek 对话。
- 两次真实 `gpt-6-luna/xhigh` 图片输入：首次验证 SDK/资产/登记成功，但检查发现新制作 Skill 缺少既有绑定能力声明；补宿主声明后再次通过，包含 producible/resolve 检查。第二次 thread `01a0fd37-5aa6-7fb2-88c1-09cedc16c3c2`，input 20,061 / output 3,081（reasoning 2,248）/ cached 0。没有观察到生图、搜索、委派工具；本轮不评估复现产物质量。
- 第二次本地证据 `tmp/skill-extraction-live-ygrr_l5m/`：report.json、request/instructions/response、trace/usage、两份 SKILL.md 和原图 assets；不提交参考图片或本地任务数据。
- 前端 typecheck/build 通过；定向 Playwright 4/4（上传、刷新、确认、失败、取消、响应丢失重试）通过，样式修复后截图主路径再次通过；桌面 1440×900 与手机 390×844 实际看图，无横溢出。CUA 独立浏览器 attach 超时，未将其计作交互成功；交互证据来自 Playwright Test，而非截图推断。
- `smoke_producer_skills`、`smoke_series_composition_service` 通过。producer-skills 首次在其既有 10 秒安装夹具等待处超时并触发临时目录清理错误，独立重跑通过；未修改安装逻辑或等待值。新增 shutdown 故障注入最初错误地重启已关闭 executor，改为新 app 夹具后通过；异常 cleanup 仍关闭其他服务。
- 代码复核补 Web 文件读取边界、图片有界读取、digest/expected_digest 描述和异常关闭清理回归。没有真实 DeepSeek 多轮对话、本轮新图片生产或质量复现实验。

## 使用与续验

重启 `python -m creatoros.web`，进入「Skill」→「从作品提炼」→ 上传图片/粘贴文案、选模式 → 开始提炼 → 在文件树查看 SKILL.md 与 assets → 直接编辑并保存或要求 Codex 改稿 → 修改预填选题后主动试产 → 满意后确认加入 Skill 库。入库之后通过既有界面组合/绑定栏目；此时继续修改正式 Skill 可编辑展示的本地工作副本，不修改历史快照。
真实无生图探针：`python -m tests.live_skill_extraction --image <参考图片绝对路径> --revise`，独立 tmp 目录、不绑定栏目、不生图；测试提炼、改稿与隔离入库。
故障：关闭服务后遗留操作下次启动标为 interrupted，不自动续跑。详细错误在本任务/改稿/试产 error.txt；修改要求后用户显式新请求。提炼质量与真实试产需后续用户验收；文字试产、换 IP、视频/网页抓取暂缓。
