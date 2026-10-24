# 素材文件夹：跨 Run 收图

## Agent 素材管理与 ZIP（2026-10-24，隔离验收）

- 用户收窄范围：不设计生产任务包、不改 Codex 生产参数；当前账号 Agent 应能整理素材集并导出 ZIP，后续交给视频剪辑 Skill。既有生产与正式在途任务不动。
- 新增一个对象工具 `manage_media_collection`：list/get/create/update/remove/export，复用现有集合服务；账号会话由宿主注入 creator_id，跨账号路径和来源仍拒绝。get 按页返回图片引用与数量，不默认把百图或全账号历史注入上下文。写操作沿 request_id/CAS，不在失败或未知结果时自动重发。
- export 基于所见集合 version/snapshot，保存原字节有序 ZIP 为受管导出，返回 export_id、名称、图数、字节大小、checksum 与下载入口；不让模型指定本机路径，不新增生产流程。导出不修改来源、选片、原图、旧任务或 Skill。未来剪辑可按导出ID解析材料，本轮不声称已连接剪辑。
- 导出下载再次核验当前账号、集合和来源归属/未删除事实，冻结导出不因普通改名/重排变成别的内容；源移除/转移后拒绝继续下载。导出URL仅宿主从真实回执构造，普通回复不展示路径和摘要。
- 验收：隔离 SQLite/HTTP 的工具→API→ZIP、账号越权/删除/过期快照、导出重放/损坏、原记录/图像保护；真实前端 DeepSeek 从账号对话查素材并导出，实际点击下载核对 ZIP 内容，不用 Mock 证明模型选工具。正式服务有任务时不迁移或重启。
- 已实现单一对象工具；list支持名称大小写无关子串筛选，同名不自动挑选。ZIP保存于隔离/正式数据库同级的受管`<dbstem>.media-exports/<export_id>/`，同快照复用，改名/重排不覆写原包。原始回执保留导出ID/hash/大小/地址，模型只收到名称/图数/已保存摘要；宿主具名a/download，不把API地址当SPA跳转。
- `tests.test_media_collection_agent`最终10/10（25.155秒），真实loopback HTTP/SQLite覆盖工具CRUD/幂等/CAS、名称查找/同名、百图分页、ZIP冻结/选片/原字节、当前账号注入和越权、源转移/移除/冻结身份、坏ZIP、安全链接及raw/model分离。既有集合+宿主契约17/17、反馈/宿主9/9通过。TypeScript/隔离Vite构建通过，单次机械检测无发现；无新Codex调用或正式库迁移。
- 真实原前端→DeepSeek→工具→持久ZIP→实际点击下载→原页聊天保留→刷新，最终D轮1/1（19.2秒），两张既有真实英语产物ZIP逐张SHA256/顺序一致，原两条Run全列与原图不变、消费者未启动。模型3请求、输入44,973/输出236/cache hit44,416；仅list(name)与export两个工具调用，零工具失败。证据`tmp/media-agent-live-20261024-d/frontend-result.json`、`tmp/media-agent-20261024/browser-business-summary/`；不是生图质量或百图大文件性能评测。
- 原始探针不覆盖：A轮任务完成但list(name)被过严字段校验拒绝，补通用名称筛选；B轮功能完成但模型复述API路径；C轮隐藏URL后仍把export_id误写为入口，收敛模型投影为业务摘要后D轮确认。A/B/C的Trace与报告均保留。桌面/手机两轮截图确认准确ZIP入口可点、下载不关闭对话；宽桌面打开聊天后素材工作区较窄，底层卡片/按钮会换行，本轮不扩展成布局重构。

## 当前账号 CRUD 盘点与用户确认的后续边界

- 用户确认可新增Skill，也可从共享Skill微调成适配栏目的独立候选；专属且无其他账号引用才可删，共享仅解除本账号引用，不破坏共享原件。本轮没有把这项权限设计谎称为全部完成。
- 选题已有manage_topics完整list/get/create/update/remove；栏目已有查询/创建/双Skill组合更新及网页删除，尚缺模型侧删除和改名API；删除活动保护还需补授权队列/讨论/RSI。Skill已有创建草稿/入库/正文与图片候选编辑，中文显示名可写frontmatter，但现有registry没有版本owner，不能把同家族可访问当成账号私有。
- 后续新增owner必须从可信会话/草稿来源写入并纳入候选幂等身份，旧无owner版本不自动认领。栏目不允许无Skill/半套绑定，因此仍被栏目使用的共享Skill不能静默清空，需要显式替换或移除栏目。当前没有独立账号收藏引用表，不假装隐藏目录就是解绑。

## 本轮边界（2026-10-24，隔离验收）

- 用户要求整图生成，不使用固定图层；本轮不生成、重做、修改 Skill、取消或迁移正在执行的生产。生产子单位与视频渲染留后续，不改 Topic/Run/Revision/Attempt 的执行语义。
- 素材集属于账号，可关联一个选题（虚拟文件夹，自动读取它所有 Run/版本），并引用同账号其他 Run。只保存来源、选片和顺序；不移动/覆盖原图，不把素材列表放进 Agent 固定上下文。第一步是 Web/API，不新增生产工作流。
- SQLite 新增 media_collections，使用显式 Alembic 迁移，仅隔离库升级；旧业务表与数据不回填。原图仍通过 StudioArtifacts 的准确 Run/Revision/页序和摘要读取；不接收任意本机路径。文件夹删除只删集合记录。
- API 前缀 `/api/creators/{creator_id}/collections`：GET 列表、POST 创建、GET/PATCH/DELETE `/{id}`；GET `/sources` 可选 topic_id；POST `/{id}/sort`；GET `/{id}/download?version=...&snapshot=...`。修改使用 expected_version；创建使用 request_id 幂等。动态新增图片追加在手动顺序后，选片/排序不改原产物。
- 创建：name、可选 topic_id、run_ids、request_id。PATCH：expected_version、可选 name/run_ids/order_ids/excluded_ids。详情：id/name/version/topic_id/run_ids/items/sources/snapshot_digest。items：id/run_id/run_title/revision_id/revision_number/order/checksum/width/height/url/included。sources：run_id/run_title/status/revision_id/revision_number/image_count/error（若有）。列表 items 至少含 id/name/version/topic_id。
- 账号与当前栏目/选题归属和冻结归属必须一致；删除选题、转移归属、无法验证文件均不能借集合越权读取。问题源有明确状态，不让一个坏源隐藏其他正常图片。ZIP 为一次明确选片快照，按序号命名，带来源清单；导出前核对 version/snapshot，拒绝旧快照，绝不自动生图。
- 图片URL使用集合自己的稳定代理 `/{id}/items/{run_id}/{revision_id}/{order}?checksum=...`，逐次校验当前集合成员和账号归属，仅读取目标图片，不因改顺序或新图追加失效。来源选择器只批量读SQLite元数据，不展开验证整个账号的所有历史图片；选中集合后才验证其实际材料。
- 选片/排序最小切片：默认保留收集到的图；支持勾选排除、手动前后移动、连续预览（默认停止，主动播放后可调速/停止）；轻量色彩/亮度相近排序由 Pillow 本地计算，只改引用顺序，不改像素，不承诺主体对齐或无闪烁。
- UI：账号工作区内容/数据旁新增“素材”，文件夹列表→来源选择→图片网格→预览/导出；Run 页提供按选题打开文件夹的入口。保持现有配色与 Logo，技术 ID 默认不显示，坏源/等待产物可见。正式前后端不得半套更新；有活跃/授权排队任务时不重启。

## 验收

1. 隔离 SQLite：同选题多个 Run/版本和额外同账号 Run 可收集；重复创建不重复集合；跨账号/转移/已删除拒绝；旧记录与图片哈希不变。
2. 等待中的来源产出后出现在同一文件夹；GET 不创建生产/不改旧业务数据；排除、顺序与刷新保持。
3. 精确图片 HTTP 可读，选片 ZIP 顺序/字节/来源一致；过期 version/snapshot、缺失/变更文件拒绝或明确不可用，不默默交付错图。
4. 真实隔离前端：创建、跨 Run 收图、选片、排序、预览、下载、刷新；桌面/手机一次截图检查。复用已有真实工件或本地确定性工件测试管理链路，明确不将受控生产当真实 Codex 质量证明。

## 使用与实现

- 入口：账号 → 素材 → 新建文件夹。可关联一个选题自动收集其所有 Run/历史版本，也可额外勾选同账号其他任务。产物页「素材文件夹」带入原选题，找到已有文件夹或打开创建表单，不自动写库。
- 「文件夹」是数据库里的图片引用集合，不是另一套原图目录。原图仍在原 Run/Revision/Attempt 目录；新任务完成后重新读取集合即可收图，未完成来源明确等待。
- 保留/排除与前后调序保存到集合；连续预览默认停止，预加载下一张再切换，切走页面暂停。ZIP 按 `0001.png` 等顺序命名并附 `manifest.json`，含准确来源、版本、原图校验和与选中数量。
- 相近排序是本地试验性算法：每张图计算 RGB 均值及 8×8 亮度，固定当前第一张，逐步找最近邻。只处理保留图片，排除项原序留在末尾。不分析故事，不修正人物位置，不承诺全局最优或完全消除闪烁；可手动改序并预览。
- 本轮没有新 Agent 工具、子任务生产模型、视频渲染、自动发布、固定图层或共享 Skill 改写。独立执行单位仍是原 Run，素材集合与生产生命周期分离。

## 实际验证（2026-10-24）

- `D:/Anaconda4.7g/envs/deepcode/python.exe -m unittest tests.test_media_collections -v`：最终 11/11，33.283 秒。覆盖跨 Run/多版本/后续追加、账号归属及冻结身份、删除/转移后拒绝、幂等/CAS、原记录保护、坏源、选片排序、ZIP 原字节及迁移零漂移。准确 100 图用本地小图夹具：排除 20 后仅对 80 图排序/导出，ZIP 连续编号、清单和原图字节一致。不是 100 张真实模型生产或大文件性能证明。
- 既有 `tests.smoke_studio_artifacts`、`tests.smoke_native_artifact_web` 通过，原单张下载/多图打包、历史版本、native 图片与摘要保护未退化。应用 TypeScript 和隔离 Vite 构建通过，既有主包大于 500 kB 警告未处理；一次 Impeccable 检测无发现。
- Playwright 使用 `tests.media_collections_live_server`：只读备份正式 SQLite 到全新 tmp 库并仅在副本迁移；不启动消费者，拒绝全部非集合写操作。引用已有英语账号真实产物两条 Run 的图片，不复制/改写原图，不调用 DeepSeek/Codex，不把管理链路测试说成生产质量评测。
- 网页实际路径：创建并关联选题、增加另一 Run、精确图片读取、双图连续预览/停止、排除与移动、刷新持久化、相近排序、下载 ZIP 逐项校验、删除文件夹后旧集合 URL 失效。另一个用例真实保存 PATCH 后丢弃响应，验证零自动重发/读回状态；另一窗口改名后，旧版本保存被拒绝。首次完整原路径 2/2（50.8 秒），见 `tmp/media-collections-20261024/browser-complete/`；之后补原正式行/原图哈希在线保护断言，结果另记，不覆盖首次记录。
- 已一批查看 1440×900 和 390×844 集合截图，名称、来源、选片、下载、移动按钮可辨认，无横向溢出；未做全站视觉重构。截图不是行为断言的替代。
- 增加原正式来源行/图片 SHA256 在线断言后重跑网页 2/2（42.4 秒），两条路径结束均确认原两条 Run 的全列值/原图哈希不变、消费者未启动；证据 `tmp/media-collections-20261024/browser-protection/`。未做正式全库逐行证明，不把并行真实生产导致的新记录误算为测试写入。
- 失败记录不删：最初路由接线前 8 项 HTTP 405；接线后 3 项 DELETE 415，补齐真实页面和测试请求的 JSON Content-Type。浏览器首轮 0/2：select 的 exact label 错误、服务器受控 checkbox 的即时 uncheck 断言错误；修测试后 1/2，剩余排序已发出但 5 秒保存提示断言过早，改为等待真实 POST 回执与状态，不放宽排序/持久化断言。证据分 `browser-first`、`browser-recheck`、`browser-complete` 保留。

## 正式部署边界

- 本轮只读维护检查发现 2 条活动生产、2 条活动 Attempt/授权队列及旧执行日志待核对，正式 PID 27724 仍工作。因此没有停服、迁移正式库、覆盖正式 dist、恢复旧任务或重跑生产。前后端代码和迁移需在空闲窗口成套加载；当前线上页面不宣称已出现「素材」。
- 用户验收最短路径（部署后）：账号 → 素材 → 建「坚持」文件夹 → 关联原选题、勾其他任务 → 选片 → 相近排序/手动前后移动 → 连续预览 → 下载所选 ZIP。

## 重跑命令（隔离）

```powershell
$env:CREATOROS_PYTHON = 'D:/Anaconda4.7g/envs/deepcode/python.exe'
npm --prefix web run build -- --outDir D:/CreatorOS/tmp/media-collections-20261024/dist
Push-Location web
$env:MEDIA_TEST_OUTPUT = '../tmp/media-collections-20261024/browser-new-run'
npx playwright test --config playwright.media.config.ts
Pop-Location
```

测试使用 8894，不复用/关闭正式 8765。默认来源是既有英语账号，需有两个未删除选题的已记录真实工件；缺失应失败，不自动生成补齐。
