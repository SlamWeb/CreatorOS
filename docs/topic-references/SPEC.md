# 任务专属图文参考

## 2026-10-18 · 已实现并验证

- 用户目标：上传1–20张参考图，默认只借鉴内容，可显式同时参考呈现；「扩展选题」返回候选，「直接制作」跳过候选生成直接排队。原图、顺序和原始要求完整保留给Codex，不能只交接标题，不写共享Skill。
- Web与账号Agent共用系列归属校验、参考存储及调研/生产服务；Agent仅能使用当前会话已有附件，不接受模型选择的本地路径。复用research_series_topics / start_content_run，不新增模型可见工具。
- 原图和不可变要求保存在数据库同级的任务参考目录；SQLite Topic仅保存reference_id，Run/Revision冻结参考元数据，Attempt独立references文件夹参与checkpoint摘要。文件篡改/跨栏目引用拒绝，不自动重生图。旧无图任务保持原输入/摘要与接口兼容。
- 图片与文字是参考数据，不是越过账号/文件/发布权限的指令。默认沿用栏目Skill呈现；显式参考呈现只覆盖可调整偏好，硬冲突需澄清。生产仍人工验收，不自动批准或发布。
- Given 多图与中文要求 When 扩展后入队生产 Then 候选及新Run携带同一参考组，Codex实际收到全部LocalImageInput；Given 直接制作 When重复提交或响应丢失 Then同请求只建同一Topic/Run/队列项，不调用候选调研。
- Given A账号会话 When引用B栏目或B附件 Then拒绝且零业务写入；Given重启/技术恢复 Then从冻结输入恢复，同图哈希不变；Given普通无图调研/生产 Then旧路径不退化。
- 验证分层：隔离SQLite/文件、真实loopback HTTP、真实前端下载/上传交互；SDK边界故障注入单列。另做低频真实Codex多图调研和单图生产接线验证，不写正式数据、不发布、不修改旧Eval成绩。真实模型限制如实记录。
- UI沿用现有栏目调研区、浅色/蓝色按钮和缩略图；支持多选/拖拽/预览/删除，上传失败保留其他成功项，提交失败保留要求与稳定请求ID，切栏目不串素材。桌面1440×900/模拟手机390×844验证。

## 落盘和通信

- `<数据库名>-topic-references/uploads/` 按栏目namespace保存上传，`bundles/`保存有序原图与input.json；支持PNG/JPEG/WebP、单图4MiB、每组最多20图。GIF明确拒绝，不静默转码。共享Skill目录保持不变。
- 新迁移0013仅增加Topic.reference_id。候选专用选择/通用queue均保存引用；新Run/Revision冻结TaskReferences，生产Attempt/references再冻结原字节并纳入native摘要。正文、原图、顺序、内容/呈现选项参与身份，不能只凭标题恢复。
- Agent参考输入另保存source_user_text：从宿主绑定turn读取人类原话（最多8000字符），与模型整理instructions一起交付；不依赖模型完整转述。相同会话/素材/要求固定请求，下一轮查询/重试也不偷偷新开生产；有意重画仍走显式fresh。
- 公共Run保留参考元数据/要求/哈希，不返回image.path；模型ToolResult投影不加载全部生产快照。工具参数不接受本地路径，实际附件按当前会话、参考组按当前栏目双重校验。动态素材不进入账号固定上下文。

## 受控回归（不算模型质量成绩）

- 新15条store/native受控SDK测试、6条真实loopback HTTP/SQLite集成、3条独立协议审查共24条通过：原字节/去重顺序、路径/跨账号/附件边界、默认内容与可选呈现、实际LocalImageInput对象、候选交接、Checkpoint/篡改拒绝/离线恢复、共享Skill不变、旧Preview token/入队回执/Agent旧幂等键兼容。
- 协议审查注入“服务已接受但HTTP响应丢失”：错误带request_id、确定性topic_id与回执入口；下一用户turn重放同Run、仅一次生产，已完成回执accepted=false，不误说重新入队。暂存占用故障只重试文件发布，不重调模型。
- 原生产队列10项+fresh9项+新store15项联合34/34通过；content_run_service、native_run_wiring、studio_artifacts、native_artifact_web、account_scope、chat_image_actions、reference_research、topic_research/SDK/observation、operation_plan关联smoke通过。编译/build通过，既有依赖弃用/大bundle提醒未扩大整改。
- 新4条前端+旧7条共11/11通过（47.7秒）：真实图片上传/冻结/HTTP/SQLite与实际下载式浏览器交互，研究/生产器为明确受控夹具。桌面1440×900、模拟手机390×844截图已看，`.impeccable/review/topic-references/`；单次detector无发现。不声明实体手机或浏览器全版本覆盖。
- 浏览器前三轮各10/11失败原样保留：sr-only上传input被旧宽度规则撑宽（产品CSS），受控Producer写文件前未mkdir（测试），测试GET证据被SPA吞（测试）。分别修复并全组重跑，`tmp/topic-references-{initial,confirm,final,regression}-20261018/`。测试曾误写冻结RuntimeContext字段，修为dataclasses.replace；没有改成功判据。
- 最终联合回归：浏览器15/15通过（1.5分钟），包含参考图4项、旧调研/批量入队/批量生产7项、图片下载3项及原工作流1项；后端24/24再次通过（44.5秒），reference_research、operation_plan、topic_research_sdk smoke再次通过。浏览器仍使用明确受控模型夹具，不能计作真实模型质量成绩。
- 本轮没有中断用户8765服务；使用功能需重启原服务并刷新页面，正常启动时应用数据库迁移0013。正式运营数据与共享Skill未参与验收。

## 真实前端 → DeepSeek/Codex

- 独立普通tmp目录继承工作区ACL，未使用正式数据库/Skill。可复跑入口 `python -m tests.live_topic_references --serve --root tmp/新的目录 --port 空闲端口`；`web/e2e/live-topic-references.spec.ts`默认跳过，只有显式设置CREATOROS_LIVE_REFERENCE_ROOT才付费。无自动批准/发布。
- B轮真实浏览器2/2通过（3.8分钟）：Web两张原图→直接制作→真实Codex原生一张图片→awaiting_approval，1Revision/1Attempt/1独立thread，没有调研候选POST；账号Agent实际DeepSeek→research_series_topics两附件→Codex联网调研ready，无新生产。工件 `tmp/reference-live-20261018-b/`、浏览器证据 `tmp/topic-references-live-round-b-20261018/results/`。生产Run `291f89ce-cd38-4d13-91ec-ac34e53f920a`，用时132719ms，Codex累计input198913/cached163328/output10157/reasoning5198 tokens；这些是多轮累计、缓存是输入子集，不当作价格或整轮DS用量。
- A轮首测0/2：隔离测试Skill漏产物声明，生产/调研均被正确拦截，未进入Codex；修测试配置，并为直接制作补保存Topic前的Skill预检。原证据 `tmp/reference-live-20261018-a/`、`tmp/topic-references-live-first-20261018/results/` 保留，不冒充实际生产失败或成功。
- C轮追加“用户原话”验证failed：Codex已返回，固定`.tmp`替换批次JSON遇WinError5，导致failed。旧错误、batch、SDK事件/用量均保留：`tmp/reference-live-20261018-c/`及`tmp/topic-references-live-round-c-failed-20261018/`；SDK用量文件记录input140792/cached100224/output938/reasoning540，不能因batch.attempts为空声称没花额度。仅调研状态改用项目已有atomic_write_text（独占临时文件＋有界Windows占用重试），无自动重跑。
- 修复后D轮真实前端Agent多图调研1/1通过（1.6分钟）：批次 `6e080dc9d7fe4a3e86ab0c16bbd00a87` ready，SDK实际2图，135字符用户原话逐字保存且进入research_request.txt；没有Topic/Run写入或Skill修改。input254144/cached198400/output1715/reasoning879为Codex累计；原始DS会话与Trace在 `tmp/reference-live-20261018-d/`，浏览器最终证据在 `tmp/topic-references-live-results/`。
- 不改历史Eval、通过率或正式内容；以上是本功能接线验收，不是选题质量、风格稳定性或所有冲突自动识别的统计评估。
