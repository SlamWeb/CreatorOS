# CreatorOS Skill Loader SPEC

## 目标

- 递归发现项目 `creatoros/skills/**/SKILL.md`，读取 `name` 和 `description` 元数据。
- 在构建主模型请求时注入可用 Skill 清单；完整 Skill 正文由模型按 location 调用 `read_file` 按需读取，Python `load()` 仅作为内部读取接口保留。

## 边界

- 当前只支持简单 YAML frontmatter 的 `name`、`description` 两个字段，不引入新的 YAML 依赖。
- 当前不自动执行 Skill 脚本，不实现 `/skill:name` 命令，不持久化 Skill 激活状态。
- Loader 只处理 Skill 资源发现；Tool Registry 和 Python Runner 仍是独立层。

## 验收

- 无效 frontmatter 和重复名称产生诊断，不阻塞其他 Skill 加载。
- `route-and-answer` 被发现，元数据进入 `ModelContext`，完整正文可以按名称读取。

## Knowledge to Carousel

- `knowledge-to-carousel` 只负责把一个已选知识点生产为图片轮播，不参与栏目、选题、排期或发布决策。
- 主 `SKILL.md` 保持短小，只固定零基础受众、视觉优先、少字、原创性和最终文件输出；详细 Manifest 契约按需读取。
- 本轮不加入 Runner 或渲染脚本，先验证 Codex 能否依靠 Skill 与现有 `SocialContentPack` 完成端到端生产。
