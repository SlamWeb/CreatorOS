# CreatorOS Skill Loader SPEC

## 目标

- 递归发现项目 `creatoros/skills/**/SKILL.md`，读取 `name` 和 `description` 元数据。
- 在构建主模型请求时注入可用 Skill 清单；完整 Skill 正文只通过 `load()` 或显式调用按需读取。

## 边界

- 当前只支持简单 YAML frontmatter 的 `name`、`description` 两个字段，不引入新的 YAML 依赖。
- 当前不自动执行 Skill 脚本，不实现 `/skill:name` 命令，不持久化 Skill 激活状态。
- Loader 只处理 Skill 资源发现；Tool Registry 和 Python Runner 仍是独立层。

## 验收

- 无效 frontmatter 和重复名称产生诊断，不阻塞其他 Skill 加载。
- `route-and-answer` 被发现，元数据进入 `ModelContext`，完整正文可以按名称读取。
