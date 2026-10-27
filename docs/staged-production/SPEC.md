# 多阶段作品制作

## 2026-10-27 Draft

用户授权按已讨论顺序重构：账号可用 Skill 库、栏目常用池/阶段默认、每次任务明确 Skill 与输入输出；同一选题批量生图收集后，ZIP+BGM+剪辑要求交新 Codex thread 产视频；最后持久依赖自动续接。旧记录/图片/RSI不改，不自动重跑。保持现有配色。

## 契约与分工

- 账号库与栏目池保存 Skill 家族引用，不复制文件；栏目池是常用子集，不是账号权限来源。旧绑定作为生图默认兼容读取。版本选择必须冻结正文和全部资源，候选可显式试用。
- 新执行规范 TaskExecutionSpec：kind=image|video，skills 为准确 skill_id/digest/role(primary|supporting) 列表，materials 为宿主解析和冻结的材料。角色和产物类型分离，旧 Mind/production 记录继续可读；主 Skill 必须符合本任务输出能力，辅助 Skill 不强求可独立生产。
- ProductionTask 沿用一个 Topic 下独立 Run 的模型；新增 kind、definition_json、dependency_json、dispatch_state、last_error，用于等待材料、一次提交和恢复。依赖只指向本选题明确选中的生图任务，不按全账号历史推断；等待中没有 Run。已成功 Run 不重跑。
- 模型/HTTP 任务输入只接受对象 ID：任务 kind、title、brief、所选 Skill 及 digest，video 的 collection_id/export_id、bgm_material_id 和 depends_on。不能由模型传主机路径。宿主准备固定 ZIP/音频后才生成执行规范；没有输入时明确等待或报错。
- 素材上传先支持 MP3/常见音频或视频文件；分享链接作为 BGM 来源受管解析，实际取得的文件要冻结。链接无法解析时明确要求上传文件，不声称已具备音乐。
- 视频首次使用新 Codex thread，技术恢复续接同一视频任务；共用 Run/Revision/Attempt、队列、租约和事件，按 kind 分派准备/执行/验收。MP4 不伪装成图片 SocialContentPack；需要真实解码、时长、尺寸、音轨及摘要校验。
- 无通用工作流画布；自动衔接是持久后台任务，不让聊天 LLM 反复轮询。可人工选片后提交，也可显式授权等待既定生图任务完成后自动导出并剪辑。失败/缺图不静默跳过。

## 验证边界

已授权验收边界：真实隔离 HTTP+SQLite 账号库/池/任务/导出接口，现有旧记录和文件保护；故障注入测重复请求、等待/恢复与跨账号拒绝；真实前端 DeepSeek 提交，真实 Codex 使用图片/BGM制作短 MP4，页面播放/下载。受控测试不替代真模型证据。正式服务更新前只读检查运行与排队，忙则不动；有迁移先备份并按维护流程验证。

## 步骤

1. 账号/栏目 Skill 引用与统一任务输入，隔离验证并提交。
2. 视频材料、执行/交付/页面，真实链路验证并提交。
3. 持久依赖续接、权限/幂等回归、旧分支适配收敛并提交。
