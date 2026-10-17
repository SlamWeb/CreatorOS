# CreatorOS Skill Loader SPEC

## YAML 读写统一（2026-10-18）

- 工程问题：旧 Loader 按行拆冒号并去引号，和编辑入口的真实 YAML 校验不一致；合法引号转义、冒号、注释、多行 description 会被误读或拒绝。
- `parse_skill_frontmatter(content) -> dict` 是共享读取边界，使用已声明依赖 PyYAML 的 `safe_load`；完整返回元数据，非法 YAML、非键值对象、字段类型或长度错误抛出中文 `ValueError`，不执行自定义 YAML 标签。
- `name` 仍是最长 64 字符的小写英文／数字／单连字符内部标识；`description` 为非空字符串、最长 1024 字符，支持标准折叠／保留换行标量。可选 `creatoros-display-name` 为最长 80 字符的非空单行名称，支持中文，不含控制字符；`Skill.display_name` 作为 content 后的可选字段随 discover/load 保留，不破坏旧五个位置参数或内部 name 的 Prompt 渲染。
- 验证只用临时目录与本地文本变换，不修改正式 Skill，不调用模型。项目解释器运行 `tests.smoke_skill_loader` 和 `tests.smoke_knowledge_to_carousel_skill` 均通过；前者覆盖保存器 Unicode 转义往返、引号／冒号／#、`>-`／`|`、非法类型／长度／标签、显示名校验，以及坏文件诊断不阻断其他发现。`git diff --check` 通过。

## 目标

- 递归发现项目 `creatoros/skills/**/SKILL.md`，读取 `name` 和 `description` 元数据。
- 在构建主模型请求时注入可用 Skill 清单；完整 Skill 正文由模型按 location 调用 `read_file` 按需读取，Python `load()` 仅作为内部读取接口保留。

## 边界

- YAML frontmatter 由共享安全解析器读取；必填 `name`、`description`，可选显示名称，其他元数据保留，不自动执行其含义。复用已有 PyYAML 依赖，不再维护另一套简化 YAML 语法。
- 当前不自动执行 Skill 脚本，不实现 `/skill:name` 命令，不持久化 Skill 激活状态。
- Loader 只处理 Skill 资源发现；Tool Registry 和 Python Runner 仍是独立层。

## 验收

- 无效 frontmatter 和重复名称产生诊断，不阻塞其他 Skill 加载。
- `route-and-answer` 被发现，元数据进入 `ModelContext`，完整正文可以按名称读取。

## Knowledge to Carousel

- `knowledge-to-carousel` 只负责把一个已选知识点生产为图片轮播，不参与栏目、选题、排期或发布决策。
- 主 `SKILL.md` 保持短小，只固定零基础受众、视觉优先、少字、原创性和最终文件输出；详细 Manifest 契约按需读取。
- 本轮不加入 Runner 或渲染脚本，先验证 Codex 能否依靠 Skill 与现有 `SocialContentPack` 完成端到端生产。
