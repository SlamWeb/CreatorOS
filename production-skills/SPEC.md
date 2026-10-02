# Production Skill sources

这些可编辑源文件不参与 CreatorOS Agent 或 Codex 全局 Skill 自动发现。
用户选择后，通过 ProducerSkillCatalog.register_local 登记进本地生产库；
每个 Run 冻结当时使用的文件，后续改动不会改写历史产物。

2026-10-02：english-word-scenes 从用户提供的六格英语图提炼教学与视觉方法。
不声称还原原作者 Prompt，不固定特定词组或 PageSpec；角色参考以用户最新要求为准。
验收：Skill Creator quick_validate；一张真实六格图的隔离生产测试记录见
docs/single-thread-production/SPEC.md。

## 2026-10-02 试产反馈修正

- 原 Skill 没有 assets，且明确让模型另创人物；交付 Prompt 指定了短发绿外套女性和仅中文底部说明。换人和缺双语是 Skill 意图提炼错误，不是单 thread 的因果证据。
- 每格底部明确为英文解释/例句 + 对应中文，词标题不算英文解释。沿用用户参考图的人物，不再随机设计新角色。
- 用户上传原图保存在源目录和正式 working 副本的 assets/reference.jpg。要求读取并通过生图工具参考图片参数传入，交付记录实际图片路径；缺图/工具不支持时报告限制。只阅读 Skill 或把 SKILL.md 列入 reference_assets 不算传了参考图片。
- 图片只保存在本地，不随公开仓库推送；克隆代码后需自行提供 assets/reference.jpg。当前上传源 SHA256 为 6f1d61a00eed2101eb1d248e4e57b38f8b3ecde17f58b47393626cf94461d56d。
- 编辑现有 working 别名，不重新登记另一个 ID，不改账号/栏目、原始版本或已完成 Run 的冻结快照。新生产读取修改后的工作目录。
- 验收通过：quick_validate；真实本地原图解码为 1200×1837，源文件/working 资产 SHA256 一致；实际 freeze_skill 包含参考图且完整目录摘要一致，导入原件保持不变；smoke_local_producer_skills 与 smoke_producer_skills 通过。本轮零模型调用、不额外生图，角色复现和双语画面效果需下次新任务生成后人工验收；旧 Run 恢复仍保留旧快照。
