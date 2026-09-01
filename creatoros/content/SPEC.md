# Social Content SPEC

## 当前实现

- `SocialContentPack` 是 CreatorOS 与外部 Codex 内容生产任务之间的最小文件契约。
- v1 只描述一篇小红书图片轮播，不覆盖视频、PDF、长文或多平台变体。
- 一篇内容允许 1～N 张有序卡片；栏目 Skill 决定建议数量，模型不写死六张。
- Pack 同时保存结构化卡片脚本、最终图片相对路径、发布文案和必要来源。
- CreatorOS 从约定目录读取 `social_content_pack.json`，并确认每个图片文件存在。
- `CodexProducer` 的 receipt mode 由 CreatorOS 依据固定身份字段创建 Manifest；Codex 返回的只是一份生产回执，不能自行决定最终目录或覆盖 Creator/Series/Topic 身份。

## 当前边界

- 不实现 Creator、Series、Topic List、Codex SDK、调度、审批或小红书发布；当前只通过 Codex CLI 的非交互模式生产。
- 不实现 staging、revision、哈希、数据库、对象存储或多平台适配。
- 示例目录只验证文件契约；示例 SVG 不代表最终视觉模板。

## 验收

- 严格 Pydantic 校验拒绝额外字段、重复/跳号卡片和不安全的图片相对路径。
- 示例使用 3 张卡片，证明数量不是固定六张。
- Loader 能读取 Manifest，并在引用图片不存在时明确失败。
- `social_content_pack_smoke=passed cards=3`：示例读取、可变卡片数量、连续编号、安全相对路径和缺图失败均通过。
