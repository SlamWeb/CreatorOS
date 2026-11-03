# 后续扩展的接口建议（仅审阅，未实现）

2026-11-03，依据当前源码；这些建议没有新增上传、跨账号共享或脚本执行功能。现有模块化单体可继续承载这些需求，无须先引入通用工作流或微服务。

| 需求 | 已有落点 | 优先明确的接口与规则 |
| --- | --- | --- |
| 上传完整 Skill | `ProducerSkillCatalog.register_local` 已校验本地目录并注册；`SkillLibraryService` 已区分 `can_use` / `can_edit`、账号引用与候选版本；HTTP 现有 GitHub 安装、文本编辑和图片 assets 上传。不能把图片 asset 上传当作完整 Skill 压缩包上传。 | 一个“导入 Skill 包”应用入口负责解包、限制路径/大小/链接、校验正文和资源、记录来源/所有者及候选状态，再发布到 catalog 并建立当前账号引用。导入不能执行包内 scripts；文件落盘与数据库引用须有可恢复的提交凭据，失败不能留下看似可用的半份 Skill。网页和 Agent 走相同入口，上传不自动修改栏目默认或旧任务冻结版本。 |
| 跨账号 BGM 复用 | `MaterialStore` 已支持音视频上传/分享链接、音轨探测和摘要核验；素材 ID 由 `creator_id + checksum` 派生，`resolve` 强制归属校验。分析文件与账号素材绑定；生产通过任务素材摘要冻结。 | 先决定“显式复制到目标账号”还是“共享不可变字节 + 独立授权引用”。少量复用可先用前者，保留来源授权与 SHA256；确需共享存储时分开 blob 身份与账号授权，查询、分析、下载、冻结都核对授权。撤回共享不能悄悄改变已经冻结的生产输入；分析结果还需绑定音频摘要、裁切时间基和分析器版本，不能按名称或原账号 ID 直接套用。不要为复用而放宽现有 `resolve` 的归属校验。 |
| Agent 编排脚本及图片处理 | `AgentPlan` 已有受控工具白名单、依赖引用与持久步骤；`ProductionTask` / `Run` 管执行权与交付，`TaskExecutionSpec` / `freeze_execution_spec` 管版本和素材冻结；图片/视频各自验收。 | 将确定性的裁切/拼图等作为受管媒体处理操作：显式输入素材 ID/摘要、受限参数、执行器版本、独立输出和回执；原图不可原位覆盖。Agent 只选择业务操作并编排依赖，不通过任意 shell/path 绕过资源权限。任意用户脚本是另一信任等级，需单独执行隔离、能力/网络限制、超时取消、幂等和未知结果恢复；不要仅给模型一个“执行脚本”工具就算完成。计划观察业务结果，不能另建与 Run 冲突的成功状态。 |

共同优先事项是可恢复的文件/数据库提交、归属检查与写入的一致性、冻结版本和摘要、外部副作用的幂等/未知结果处理。先把这些规则放进能由网页、Agent 和测试共同调用的少量接口，再按真实需要拆执行进程；目录搬迁本身不提供这些保障。

当前同账号 BGM 分析在文件存在时会复用；它尚未按起点、时长、分析器版本区分缓存，除非强制重算可能沿用旧分析。视频任务会冻结音频素材或分享文本，但不会自动附带分析结果。账号 Agent 当前没有 BGM 上传或链接导入能力；聊天附件仅支持图片。分析器也依赖用户目录下的 Skill，worktree 本身不能隔离它。以上是实现边界，并非已完成的扩展。

本次核对的源码入口：`creatoros/integrations/producer_skills.py`、`creatoros/integrations/skill_library.py`、`creatoros/web/skill_routes.py`、`creatoros/web/skill_library_routes.py`、`creatoros/integrations/managed_materials.py`、`creatoros/web/material_routes.py`、`creatoros/agent/plans.py`、`creatoros/delivery/task_contracts.py`、`creatoros/delivery/task_execution.py`。本审阅未进行未来功能的安全或性能验证。
