# 产物交付与验收

`creatoros.delivery` 负责本地冻结输入、产物收件和确定性证据验收；它不创建或调度 Run，也不启动模型、批准或发布。Run、Revision、Attempt 与队列仍由 `creatoros.runs` 管理，任务和队列的事务边界不由此模块接管。

## 视频恢复工作区链接边界（2026-11-03）

同 Revision 恢复前检查旧 Attempt 与新 Attempt 工作目录中的每个目录和文件，拒绝符号链接、Windows junction/其他重解析点及特殊文件；不遍历链接目录，普通嵌套文件继续恢复。复制过程不跟随新出现的符号链接，并在复制后再次检查，发现链接时保留现场并停止。此为路径校验，检查和复制之间仍可能被外部并发进程替换文件或 junction；当前实现不是抗恶意并发修改的操作系统沙箱。

## 入口和职责

- `validate_artifact(directory, composition=..., production_protocol=..., execution_spec=...)` 是统一验收入口。它按已有 `legacy`、`native-v1` 或 `task-v1` 协议检查产物，返回图片或视频各自的 `ArtifactValidation`；不能把检查通过等同于内容质量或人工验收通过。
- `task_contracts` 与 `task_execution` 描述和核验冻结的 Skill、素材及任务输入。`runs.task_contracts` 和 `runs.task_execution` 保留兼容导入；等待依赖的 ProductionTask 仍可暂时没有 Run。
- `image_assets` 接收本次线程的生图完成事件并保存可核验库存；`image_materials` 保存宿主材料表与交付回执；`image` 接收图片清单并检查路径、顺序、文件、来源和 checkpoint；`image_recovery` 在同一 Revision 中从已核验证据恢复到新 Attempt，不启动生产回合。
- `video_receipt` 接收并持久确认视频交付，允许相同提交重放，并从前一 Attempt 恢复已接收文件；`video` 验证固定输入、回执、摘要和真实媒体编码及完整解码。无效或结果未知的交付仍按原有规则保留与处理，恢复不代表自动重渲染。
- `integrations.native_production`、`integrations.video_production` 负责 Codex 会话、提示词和执行，使用上述交付逻辑；旧的模块级导入保留兼容。Run 侧从交付结果更新权威状态，生产执行器不直接决定人工验收。

## 图片接收与明确提交

当 Codex 没拿到图片路径时，可用 `received:only` 请求当前已接收图片清单，宿主不会按“目前只看到一张”猜测并确认交付。Codex 应等已发出的生成结束，再按返回的实际路径明确提交并排序；查询不消耗交付修正次数。SDK 事件和工具请求分属不同队列，返回的清单只能证明当前已观察到的图片，不能证明后续没有其他事件。旧任务已经确认的别名回执按原摘要重放，不改写历史图片或自动重新生图。文件核验不证明图片满足内容要求，仍需人工验收。

新 `tool-v1` 图片提交在修改交付前独占创建并同步 `artifact-submission-intent.json`，记录会话、回合、输入和提交摘要，并清除新提交对应的旧完成状态。写入异常会锁存结果未知，停止该回合，同一 handler 不能覆盖已有回执；同一 Revision 恢复会先检查最新 Attempt 的 intent 和回执，即使 checkpoint 缺失也不能退回更早 Attempt。只有回执、checkpoint、intent 及最后一个已完成同会话同 ID 生产回合一致，且 checkpoint 的最终 `turn_completed` 已持久化时才可按既有规则离线恢复；单凭 worker 回合完成不能排除提交 checkpoint 已写入后报错。提交成功但最终完成状态尚未保存也保守停止核对。恢复新 Attempt 时先独占复制并 fsync intent 和 worker 回执，再进行可能写入 checkpoint 的恢复；中途失败不得退化为旧协议。已确认的相同 payload 重放仍返回原回执，旧无 intent 且回执可核验的 Attempt 保留原恢复语义。原图片和证据不删除；未确认以不可技术重试的 `image_delivery_unconfirmed` 报告。

可执行 `python -m creatoros.delivery.image_submission_check --attempt <原 Attempt 绝对路径>` 只读查看 intent、回执与回合证据的匹配状态；`completed_evidence` 只表示这些元数据匹配，不代替产物文件校验或人工验收，也不解除未知状态或重派。写入标记前崩溃可能没有标记；标记文件 fsync 不保证目录项在断电后持久，也无法发现已在外部运行的幸存进程。旧协议没有新增标记，不声称能补出旧未记录事件。

动态工具适配器在提交处理已经开始后遇取消、超时或异常时，先跨线程通知图片生产器锁存结果未知，再向 SDK 返回未知；取消 Future 不能撤销仍在执行的磁盘写入。即使迟到的 handler 完成写入或 SDK 回合返回完成，也不写入最终完成状态、不启动修复回合。工具请求在派发前已经取消时没有开始写入，保留普通取消语义。

图片 intent 在明确校验拒绝后仍保留，同一存活 handler 可在该标记下修正并更新提交摘要；进程中断或重建 handler 后保守要求核对。提交已保存但该回合尚未最终完成时，同一 handler 的相同 payload 仍可幂等重放，新 handler 须等待原 Attempt 的最终完成证据。未知通知早于 handler 创建 intent 时，宿主会尝试独占创建并 fsync 未知标记；如果磁盘无法创建标记，内存仍停止，但重启后不能保证从文件单独发现该未知状态，须结合 Run/Attempt 失败记录人工核对。文件 fsync 仍不保证目录项断电持久。

## 已完成视频的显式离线纠正（2026-11-02）

同 Revision 视频恢复遇到更近的 Attempt 目录链接或 Windows 重解析点时，以 `video_delivery_evidence_conflict` 停止；不能跳过该条证据回退到更早 Attempt。正常目录仍按原规则校验恢复。

图片、视频与重启证据检查按 `attempt-N` 的正整数编号从新到旧检查，不限定三位宽度，兼容已有 `attempt-001`。同一编号存在多个名称时拒绝猜测；旧 Attempt 为链接、重解析点或非目录也停止核对，不跟随目标。仅在已经检查到的较新合法 checkpoint 可确定恢复边界时，沿用原有的较新优先规则。

- 旧 `final-json-v1` 回执已完成、声明成功但视频路径无效时，回执本身不足以证明工作目录里哪个文件是交付。普通重试不会猜测或启动新回合来修复路径；若需要挽救，用户先明确选中原 Attempt 的 `work` 内相对 MP4 路径。`python -m creatoros.runs.video_correction --run-id ... --source-attempt-id ... --work-relative-mp4 ... --database-url ... --output-root ...` 默认只检查，显示原回执、视频属性、Run version 和 fingerprint。执行时再次提供相同参数并加 `--apply --expected-version <检查结果> --fingerprint <检查结果>`；这是会写入同一 Revision 新 Attempt 和 Run 状态的显式操作，数据库和输出目录应指向事先核实的目标。
- 仅允许当前账号下最新、可重试且因 `video_delivery_failed` 失败的 task-v1 视频 Attempt；冻结任务、Revision、当前 Run、thread、输入、宿主记录的已完成生产回合、原始回执与选定文件必须一致。原回执须明确声明成功且没有 error；缺少凭证、发生归属转移、版本/文件变化、取消或仍有排队/运行任务时拒绝。源路径不得穿越或经过链接。文件须完整解码且满足 H.264/AAC MP4 契约。实际交付的 title/text 来自原回执；所选路径及原文件摘要由单独的宿主纠正凭证记录，不伪装成 SDK 回执。原 Attempt 与原回执不改写，过程不启动 Codex。
- 检查结果的 fingerprint 绑定身份、来源文件和所选视频摘要，执行前再次检查；复制后和提交事务中再核验文件及当前归属，已有失败队列回执与验收成功在同一事务中更新。重复提交同一已验收纠正返回同一 Attempt。纠正产物以 `creatoros-video-artifact-v2` 摘要覆盖新增来源凭证，既有视频摘要仍用原 v1 算法。指纹只能固定检查时看到的本地证据，不证明检查前文件从未被修改，也不证明模型原本打算交付所选文件；用户仍需人工判断内容。
- `submit-tool-v1` 的已完成回合若没有已接收的提交回执，记为不可自动重试的 `video_delivery_unconfirmed`，保留现场人工核查；不能通过上述路径纠正补造提交回执。已有可核验的接收回执仍按原规则离线恢复。
- 若 worker 回执已经记录最后生产/修复回合完成，但 checkpoint 在保存完成状态前中断，恢复时仍会核对冻结输入、worker 协议、同一 thread 和最后回合；确认完成且没有接收回执时拒绝启动新 SDK 回合。worker 回执损坏或与 checkpoint 的会话不符时以 `video_delivery_evidence_conflict` 停止并保留证据，不声称该回合已完成；真实未完成的回合沿用原有显式恢复路径。

## 视频制作的明确失败回执（2026-11-03）

- `submit-tool-v1` 中，Codex 提交 `complete=false` 且 `video_path=null` 表示制作未完成。宿主把完整回执和其摘要写入原 Attempt 的 checkpoint，返回终态失败及原原因，不记为已接收视频、不消耗交付参数修正次数；同一个回执重放返回原失败，后续提交不能覆盖。`complete=true` 但路径缺失或无效仍是可修正交付错误，沿用次数限制。
- 持久化失败回执后，若尚未观察到与当前 thread 和 turn 均匹配的结束事件，宿主请求中断当前 Codex 回合；明确观察到 `completed`、`interrupted` 或 `failed` 终态则保留原失败原因，不把 SDK 对失败回合抛出的异常误认为断流。中断请求失败时明确报告停止未确认，不能声称已在途的外部调用被撤销。写回执发生异常时按结果未知处理，不声称已落盘或直接重试。
- 同 Revision 恢复时先核对冻结输入、回执摘要和已有 worker 回执身份：匹配的明确失败直接保留原原因，标为不可技术重试，不再启动 Codex；摘要损坏或 worker 会话不符则标记证据冲突。没有新增字段的旧 checkpoint 仍按原协议判断，旧 Attempt 和文件不改写。
- 新 `submit-tool-v1` 视频回合在派发前先以原子替换记录“结果未确认”标记；标记写入失败则不派发。进程中断、用户停止或失败回执写入时发生错误后，即使 worker 记为 interrupted，只要没有可核验的成功或失败回执，恢复也停止并提示在本任务页面核对，不自动启动新回合。已收到视频仍可离线恢复；如果崩溃发生在标记写入后但实际派发前，也会保守地要求核对。旧 checkpoint 没有标记时保留原有恢复语义。此记录采用现有原子替换，未提供断电级 fsync 保证。

## 历史证据

`native-v1` 的文件清单、摘要顺序和哈希算法以及视频已有摘要都属于持久化契约；新增证据不能未经版本化地并入旧摘要。不得为了迁移此代码重写历史回执、修改产物或重新生产。
