# Production Skill sources

这些可编辑源文件不参与 CreatorOS Agent 或 Codex 全局 Skill 自动发现。
用户选择后，通过 ProducerSkillCatalog.register_local 登记进本地生产库；
每个 Run 冻结当时使用的文件，后续改动不会改写历史产物。

2026-10-02：english-word-scenes 从用户提供的六格英语图提炼教学与视觉方法。
不声称还原原作者 Prompt，不附带或重新发布参考图片。不固定特定词组、IP 或 PageSpec。
验收：Skill Creator quick_validate；一张真实六格图的隔离生产测试记录见
docs/single-thread-production/SPEC.md。
