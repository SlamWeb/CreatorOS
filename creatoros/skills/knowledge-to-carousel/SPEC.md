# Knowledge to Carousel Skill SPEC

## 目标

- 将一个知识点转成面向零基础读者、可直接发布的小红书图片轮播。
- 借鉴 ELI5 的最小约束思想，不复制特定创作者的独特画风或作品。
- 让外部 Codex 作为端到端生产工具，把最终图片和 `SocialContentPack` 写入指定目录。

## 当前边界

- Skill 负责解释与视觉生产，不负责选栏目、选题、排期、审批、发布或效果反馈。
- 卡片数量不固定；只要求叙事完整、手机端可读和视觉一致。
- 当前不提供 Python Runner、固定模板、HTML 页面或图片生成脚本。

## 验收

- Skill Loader 可以发现 `knowledge-to-carousel`，完整正文仍按需加载。
- Skill 明确要求输出真实图片和 `social_content_pack.json`，并引用可独立阅读的文件契约。
- 首次真实运行以 Agent State、Runtime Context 和 Messages 为主题生成 5 张原创轮播图；Manifest 通过 `SocialContentPack.load()` 验证。
- 真实运营产物保存到本地 `outputs/`，默认不进入 Git；确认满意的展示样例后再单独复制到公开文档资源目录。
