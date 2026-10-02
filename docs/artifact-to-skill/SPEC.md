# Artifact → Skill V1

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

每次提炼 fresh Codex thread，使用现有 gpt-6-luna/xhigh SDK 登录态与独立上下文配置；实际传入 LocalImageInput。只要求文本 Skill 草稿，不联网调研、不生图。180 秒有限等待，允许取消，无隐式重试。
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

- `tests.smoke_skill_extraction` 通过：四模式、真实图片字节保存、显式确认、幂等/冲突、HTTP 门禁、失败/取消/重启、草稿篡改与部分登记重试。SDK 由受控提炼器替换用于故障注入，不消耗模型。
- `tests.smoke_skill_extraction_tools`、`tests.smoke_web_agent` 通过：工具注册/参数/Studio transport/路径边界，未调用真实 DeepSeek 对话。
- 两次真实 `gpt-6-luna/xhigh` 图片输入：首次验证 SDK/资产/登记成功，但检查发现新制作 Skill 缺少既有绑定能力声明；补宿主声明后再次通过，包含 producible/resolve 检查。第二次 thread `01a0fd37-5aa6-7fb2-88c1-09cedc16c3c2`，input 20,061 / output 3,081（reasoning 2,248）/ cached 0。没有观察到生图、搜索、委派工具；本轮不评估复现产物质量。
- 第二次本地证据 `tmp/skill-extraction-live-ygrr_l5m/`：report.json、request/instructions/response、trace/usage、两份 SKILL.md 和原图 assets；不提交参考图片或本地任务数据。
- 前端 typecheck/build 通过；定向 Playwright 4/4（上传、刷新、确认、失败、取消、响应丢失重试）通过，样式修复后截图主路径再次通过；桌面 1440×900 与手机 390×844 实际看图，无横溢出。CUA 独立浏览器 attach 超时，未将其计作交互成功；交互证据来自 Playwright Test，而非截图推断。
- `smoke_producer_skills`、`smoke_series_composition_service` 通过。producer-skills 首次在其既有 10 秒安装夹具等待处超时并触发临时目录清理错误，独立重跑通过；未修改安装逻辑或等待值。新增 shutdown 故障注入最初错误地重启已关闭 executor，改为新 app 夹具后通过；异常 cleanup 仍关闭其他服务。
- 代码复核补 Web 文件读取边界、图片有界读取、digest/expected_digest 描述和异常关闭清理回归。没有真实 DeepSeek 多轮对话、本轮新图片生产或质量复现实验。

## 使用与续验

重启 `python -m creatoros.web`，进入「Skill」→「从作品提炼」→ 上传图片/选模式 → 开始提炼 → 查看草稿 → 确认加入 Skill 库。需要调规则时编辑入库后展示的本地目录；不直接改冻结草稿。通过现有入口手动组合/绑定栏目，然后自行选择选题试产。
真实探针：`python -m tests.live_skill_extraction --image <参考图片绝对路径>`，独立 tmp 目录、不绑定栏目、不生图。
故障：关闭服务后 unfinished running 会在下次启动标为 interrupted；不自动续跑。详细错误在本任务 error.txt，用户另起任务重试。产出质量、试产评估、网页内编辑 SKILL.md、视频/网页抓取均暂缓。
