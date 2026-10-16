# 发布与反馈 SPEC

## 自动反馈与账号看板（2026-10-16）

- CreatorOS 后台每 300 秒检查；仅已核验 published 且发布时间在最近 7 天的笔记自动采集。每篇成功采集间隔至少 1800 秒；失败持久化下次尝试，退避最多 6 小时。按账号保存开关，默认关闭；本次用户授权开启英语账号。服务关闭不采集，重启不补跑所有漏过的周期。
- 不调用 LLM，不注入固定账号上下文，不触发发布或重发。与现有发布共享 MCP 协调边界，自动读取遇到发布或人工操作让路；已经在途的读取有超时，不能承诺瞬间抢占。SQLite 网络外短事务；追加原有反馈快照，未知指标保持 NULL。
- 新增持久设置/每篇采集状态；后台生命周期随 Studio。失败不抹掉上次数据，不改发布状态；身份不符不得采集别人的数据。
- API：GET `/api/creators/{id}/analytics`；PUT 同路径 `/settings` body `{enabled:boolean}`；POST 同路径 `/refresh` 手动触发最近 7 天刷新（不等待长请求，返回同看板 DTO）。账号作用域校验。刷新重复请求复用后台任务。
- 看板 DTO：`creator_id, enabled, checking, checked_at, last_updated_at, next_check_at, heartbeat_seconds:300, refresh_seconds:1800, window_days:7, error, summary:{notes, sampled_notes, likes, favorites, comments, views, shares}, notes:[{request_id,run_id,series_id,series_name,title,post_url,published_at,last_updated_at,next_refresh_at,error,samples:[{measured_at,likes,favorites,comments,views,shares}]}]`。摘要仅为最近 7 天笔记最新累计值之和，任何缺失项对应合计为 NULL；不是账号全量后台统计。曲线按真实单篇累计快照、真实时间，不虚构历史。
- 验收：隔离数据库验证迁移、时间窗、30 分钟节流、失败退避、开关/重启、发布优先、账号隔离、关闭；真实前端开启英语账号，真实 MCP 只读采集 afraid 并核对数据库。原发布单 confirmed/submitted 仍各 1 次。
- 隔离自动测试：`tests.test_publication_feedback` 16/16，覆盖迁移零漂移、持久化/重启、时间窗、自动/手动竞争、超时取消、账号/作者不符、禁用、发布优先、忙时手动刷新反馈和手动恢复清理退避；计时/故障使用受控时钟与平台，不算真实平台性能结果。
- 关联回归：feedback HTTP、publication requests/HTTP、manual publication、account context/scope smoke 全通过；MCP 解析与发布核对单测 11/11。未改固定账号上下文或其工具能力。
- 自动读取整体超时 120 秒，失败退避；发布到来时，已在途读取不能瞬间抢占，但不再启动新的后台读取。开启后等下一次检查，重启时检查持久设置。手动刷新可绕过 30 分钟等待，但仍不得与发布并发；占用时明确提示稍后刷新。
- 看板优先使用 verified 事件中真实平台发布时间，缺失/损坏才回退旧凭证；最近 7 天窗口采用同一时间。不重写历史凭证，把旧提交开始时间伪装成平台发布时间的问题只在读取投影纠正。
- 真实验收：2026-10-16（Asia/Shanghai）从正式前端开启英语账号自动更新，未按手动刷新；21:14:22 定时检查启动，21:14:57 真实 MCP 读取 afraid 笔记并追加第 2 个快照：点赞 0、收藏 1、评论 0、阅读/分享 NULL。21:15:53 重启后开关保留且未立即重复采集，下一次到期为 21:44:57，实际执行落在其后的心跳。
- 与改动前 SQLite 备份逐表核对：creators、series、topics、content_runs、content_revisions、publication_requests、manual_publications 全部不变；confirmed/submitted/verified 事件仍各 1 次、外键检查无错误。只新增反馈设置、调度状态和真实快照；未重新发布。以上为单篇真实采集/恢复证据，不代表多账号平台全量采集已验证。

## 话题别名核对修复（2026-10-16，完成）

- 实际已发布的 afraid 笔记被平台将「英语学习」映射为「英语学习打卡」，原核对按输入话题字符串替换，留下别名尾巴导致正文误判。只修核对，不重新确认或调用发布工具。
- 将平台追加在正文末尾的 `#名称[话题]#` 与正文分开；仅在话题数与提交数量相同、剩余完整正文一致时接受标签名称变化。作者、标题、图片数量和自动核对的发布时间窗不放宽；正文中间的同类字符不能随意删除。Trace 保存输入/实际话题，避免声称标签逐字相同。
- 发布状态与指标仍保存在发布凭证/反馈快照表，栏目选题仅有必要的 published 状态。点赞收藏不注入每轮固定账号上下文；页面和 Agent 按需查询现有发布工具，不增加动态 System Prompt。
- 验收：纯本地文本正负例及隔离服务零重发回归；部署后仅对已知笔记核对、刷新真实指标并检查 SQLite，不走 confirm/publish。
- 验证通过：文本核对 5 项 + MCP 解析 6 项；发布状态 smoke、HTTP 权限/幂等 smoke、旧人工发布 smoke。别名恢复用例断言 unknown → published、仅一条凭证、刷新指标且外部发布计数不增加。
- 用户已在前端确认发布；本次通过正式服务的 reconcile 只读核对真实笔记 `6ad217e60000000018012572`，发布单 `126d9764-3a9e-4267-bfe0-dec4e3c157d3` 为 published，选题 published，Run 仍 approved。确认/提交事件各 1 次，发布凭证 1 条；未再次调用 confirm/publish。
- 2026-10-16 20:41（Asia/Shanghai）真实反馈快照已写入 SQLite：点赞 0、收藏 1、评论 0，阅读/分享未知 NULL。网页发布面板显示真实链接和该快照。以上为一篇真实发布与手动刷新闭环，不代表反馈驱动的自动运营已实现。

## 受控 MCP 发布（2026-10-16）

当前实施最小方案：发布预览单 → 本人在前端确认 → 单次外部派发 → 核对真实笔记 → 手动刷新反馈。以下历史“只手动发布”边界被此节取代，其原有手动登记/指标数据保持兼容。

- SQLite 新增 `publication_requests` 与 `publication_accounts`。一条 Run 最多一个发布单；同一真实平台账号的相同有序图片摘要、文案、标签、可见范围也唯一。真实账号以平台 ID 绑定，不以昵称绑定。首次绑定发生在用户确认事务中，预览不是授权。
- 发布预览允许尚待验收产物，但确认必须验证当前已批准 Revision、完整产物摘要、图片字节、当前平台身份、预览 digest。文字/账号/版本变化令旧确认摘要失效。确认通过 UI-only 路由，模型不得代替用户确认。
- 原子事务将 draft 改为 sending 并记录用户确认后，才启动后台外部调用。网络调用不占用 SQLite 事务。双击、跨会话重复确认、响应丢失都复用原单；无法确定是否已发送时，保持 unknown，禁止自动重发。
- confirmed/sending 后冻结内容。确认时复制图片到隔离于生产产物的 `.publication-snapshots/{id}/{digest}` 并校验字节。模型不能指定任意文件路径；发布图片只从已验收产物解析。
- 状态：draft → sending → verifying → published / unknown。启动发现 sending/verifying 转为 unknown；关闭服务取消执行也留下 unknown。不是 exactly-once 保证：上游 MCP 没有幂等键，也没有稳定的发布回执 ID。
- 上游“发布完成”只表示需要核对，不直接登记成功。自动核对要求唯一匹配的真实作者、完整标题/正文、图片数量、发布时窗；没有时间或存在歧义不判成功。用户手填笔记链接时仍核对作者/完整文案/图片数量。平台压缩图片，当前**不宣称逐像素验收**。
- 核对完成在同一事务写既有 `manual_publications` 凭证及 Topic published。历史手动凭证不会被重新解释为远端已核验。反馈仅手动刷新，追加既有 metrics；取不到的指标为 NULL，不填 0。无定时发布、自动互动、后台定时抓取。
- 错误输出不透传 Cookie/HTTP头/上游任意日志；简短状态供前端，状态事件保留在发布单供 Trace 查看。

### 验证边界

真实读取账号与工具连接由集成验收执行；隔离用例故障注入替代真实发布（发布不可逆）。不允许测试脚本点击正式前端发布确认。用户随后明确授权代为验收 afraid，主代理检查图文并通过真实前端批准第 1 版；该授权不等于发布授权。真实发布闭环只能在用户本人确认后验证，不能将隔离模拟记为真实发布成功。

### 当前验证记录（2026-10-16）

- `python -m tests.smoke_publication_requests` 通过：空库迁移与 ORM 零漂移；未批准拒绝、预览变化拒绝、错误账号拒绝、批准后图片字节变化拒绝、双确认只派发一次、跨 Run 相同内容拒绝、账号绑定拒绝错号、超时 unknown 不重发、错误作者/文案/缺时间不能判成功、人工链接核对、指标未知 NULL、重启中断转 unknown。
- `python -m tests.smoke_manual_publication` 回归通过；原有人工登记和回填保留，已有发布单不得借人工登记接口绕过核对。
- MCP 集成代理执行了真实只读 `identity` 和笔记详情读取，稳定账号 ID、发布时间、标题正文、图片数量可解析；实际缺失的分享/阅读仍为未知。这不是发布验收。
- 尚未执行任何真实发布、未点击正式内容的确认按钮；完整真实发布闭环等待用户本人前端确认。
- `python -m tests.smoke_publication_http` 通过：隔离 HTTP/SQLite 真实接线，错账号拒绝、Agent 无确认权、未批准拒绝、重复确认一派发、未知结果不重发。平台写入用故障注入，不计真实发布。
- `python -m unittest tests.test_publication_mcp_client` 6/6 通过；`python -m tests.smoke_account_scope` 回归通过；`pip check` 通过。
- 正式前端批准 afraid Run `8b62175e-b1b4-44fc-997b-37efd231bae3` 第 1 版，准备发布单成功。标题缩短为「同是害怕，这4个英语词怎么选？」；原图与原产物文件不改。预览账号为真实登录的「小帅先生」，当前待用户发布确认。


## 用户目标

沿现有 `ContentRun` 完成一次真实的“产物验收 → 人工发布 → 效果回填”。发布由用户在自己的小红书账号完成；CreatorOS 只记录发布凭证与后续手填指标，不调用平台发布/分析接口。

## 最小契约

- 仅 `approved` Run 可登记一条小红书发布记录，绑定已批准的 Revision 与产物 digest；要求笔记链接，重复提交相同记录幂等，冲突记录拒绝。
- 发布记录后把 Topic 标记 `published`，不把 `ContentRun.approved` 误写成平台发布状态。
- 一条发布记录可追加多次指标快照，记录采集时间和阅读、点赞、收藏、评论、分享；未提供的指标保持未知，不当作 0。重复请求以 request_id 去重。
- Run Inspector 展示登记入口、真实链接和指标历史；不自动抓取、自动发布或根据指标自动改选题。
- 数据库是发布与反馈真相，`outputs/` 仍只存图片/文案/生产证据。

## 验收

隔离 SQLite 从 Alembic 升级；非 approved/错版本/错 digest/坏链接拒绝且零写入；重复发布不产生第二条；指标负数拒绝、相同 request_id 不重复；重启后通过 HTTP 读取；浏览器完成批准后的登记、刷新及回填。正式库只在启动迁移，不写入虚构发布或指标。

## 实施与验证（2026-09-30）

- Alembic `20260930_0007` 新增一条 Run 对一条人工发布凭证、可多次追加的指标快照；发布凭证绑定已批准的 Revision/digest，Topic 在同事务标记 published，Run 状态仍 approved。此模块不调用任何平台 API。
- Run Inspector 在批准后提供受摘要校验的图片/文案 ZIP、手填小红书笔记链接和分时指标；已登记时显示“已发布”，未登记时明确“尚未发布”。指标未填保持 NULL。链接仅校验格式与允许的域名，不验证平台笔记真实存在。
- `python -m tests.smoke_manual_publication`：隔离迁移与 ORM 零 drift、未批准/错摘要/坏链接拒绝、ZIP 5 图、重复登记及指标 request_id 幂等、刷新可读取，均通过。现有 `smoke_studio_artifacts` 通过。
- Playwright `studio-workflow.spec.ts` 隔离全流程 1 passed：批准→登记模拟链接→回填→刷新；桌面/390px 截图已检查且手机无横向溢出。隔离链接不是真实小红书发布，也没有真实数据。
- 正式数据库在启动前复制备份至忽略的 `tmp/creatoros-before-manual-publication-20260930.db`；随后服务启动迁移到 0007。正式消息队列 Run 仍 `awaiting_approval`、6 张图、无 publication，不自动批准、发布或回填。

## 后续边界

- 下一步由用户逐张验收真实产物并手动发布，回到同一 Run 填真实笔记链接与 T+1/T+7 数据；届时才可称第一条真实闭环完成。
- 本轮不做链接真实性抓取、自动发布、平台授权、自动指标抓取或反馈驱动选题；Run 事件时间线尚未把人工发布/指标表并入统一 Trace，当前在面板单独呈现。
