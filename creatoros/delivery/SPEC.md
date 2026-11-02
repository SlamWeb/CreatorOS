# 产物交付与验收

`creatoros.delivery` 负责本地冻结输入、产物收件和确定性证据验收；它不创建或调度 Run，也不启动模型、批准或发布。Run、Revision、Attempt 与队列仍由 `creatoros.runs` 管理，任务和队列的事务边界不由此模块接管。

## 入口和职责

- `validate_artifact(directory, composition=..., production_protocol=..., execution_spec=...)` 是统一验收入口。它按已有 `legacy`、`native-v1` 或 `task-v1` 协议检查产物，返回图片或视频各自的 `ArtifactValidation`；不能把检查通过等同于内容质量或人工验收通过。
- `task_contracts` 与 `task_execution` 描述和核验冻结的 Skill、素材及任务输入。`runs.task_contracts` 和 `runs.task_execution` 保留兼容导入；等待依赖的 ProductionTask 仍可暂时没有 Run。
- `image_assets` 接收本次线程的生图完成事件并保存可核验库存；`image_materials` 保存宿主材料表与交付回执；`image` 接收图片清单并检查路径、顺序、文件、来源和 checkpoint；`image_recovery` 在同一 Revision 中从已核验证据恢复到新 Attempt，不启动生产回合。
- `video_receipt` 接收并持久确认视频交付，允许相同提交重放，并从前一 Attempt 恢复已接收文件；`video` 验证固定输入、回执、摘要和真实媒体编码及完整解码。无效或结果未知的交付仍按原有规则保留与处理，恢复不代表自动重渲染。
- `integrations.native_production`、`integrations.video_production` 负责 Codex 会话、提示词和执行，使用上述交付逻辑；旧的模块级导入保留兼容。Run 侧从交付结果更新权威状态，生产执行器不直接决定人工验收。

## 已完成视频的显式离线纠正（2026-11-02）

- 旧 `final-json-v1` 回执已完成、声明成功但视频路径无效时，回执本身不足以证明工作目录里哪个文件是交付。普通重试不会猜测或启动新回合来修复路径；若需要挽救，用户先明确选中原 Attempt 的 `work` 内相对 MP4 路径。`python -m creatoros.runs.video_correction --run-id ... --source-attempt-id ... --work-relative-mp4 ... --database-url ... --output-root ...` 默认只检查，显示原回执、视频属性、Run version 和 fingerprint。执行时再次提供相同参数并加 `--apply --expected-version <检查结果> --fingerprint <检查结果>`；这是会写入同一 Revision 新 Attempt 和 Run 状态的显式操作，数据库和输出目录应指向事先核实的目标。
- 仅允许当前账号下最新、可重试且因 `video_delivery_failed` 失败的 task-v1 视频 Attempt；冻结任务、Revision、当前 Run、thread、输入、宿主记录的已完成生产回合、原始回执与选定文件必须一致。原回执须明确声明成功且没有 error；缺少凭证、发生归属转移、版本/文件变化、取消或仍有排队/运行任务时拒绝。源路径不得穿越或经过链接。文件须完整解码且满足 H.264/AAC MP4 契约。实际交付的 title/text 来自原回执；所选路径及原文件摘要由单独的宿主纠正凭证记录，不伪装成 SDK 回执。原 Attempt 与原回执不改写，过程不启动 Codex。
- 检查结果的 fingerprint 绑定身份、来源文件和所选视频摘要，执行前再次检查；复制后和提交事务中再核验文件及当前归属，已有失败队列回执与验收成功在同一事务中更新。重复提交同一已验收纠正返回同一 Attempt。纠正产物以 `creatoros-video-artifact-v2` 摘要覆盖新增来源凭证，既有视频摘要仍用原 v1 算法。指纹只能固定检查时看到的本地证据，不证明检查前文件从未被修改，也不证明模型原本打算交付所选文件；用户仍需人工判断内容。
- `submit-tool-v1` 的已完成回合若没有已接收的提交回执，记为不可自动重试的 `video_delivery_unconfirmed`，保留现场人工核查；不能通过上述路径纠正补造提交回执。已有可核验的接收回执仍按原规则离线恢复。

## 历史证据

`native-v1` 的文件清单、摘要顺序和哈希算法以及视频已有摘要都属于持久化契约；新增证据不能未经版本化地并入旧摘要。不得为了迁移此代码重写历史回执、修改产物或重新生产。
