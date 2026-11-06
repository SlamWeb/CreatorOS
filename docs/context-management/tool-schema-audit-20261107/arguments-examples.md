# 十个工具：合成参数示例

示例已在优化前基线 `2720cfa` 与本次机械精简后的账号工具 Schema 上分别通过校验；如工具契约调整，请重新运行同目录 `examples.py` 校验当前版本。

与 [完整 Schema](ten-tools.md) 对应。以下均为**合成示意，不是实际调用**；只经模型可见 JSON Schema、账号绑定器及真实参数解析器检查，没有执行工具。ID、digest 和路径均为占位值，通过解析不代表对象存在、账号可访问、版本有效、操作获授权或请求一定成功。例如生产、编辑和发布准备需满足各自业务授权与状态规则。

带 `creator_id` 的账号绑定工具由宿主注入当前账号；下面展示模型提交的参数，故不包含该字段。归档路径必须来自当前会话的实际工具返回，示例路径不能直接使用。

## manage_topics

按 ID 只读选题：

```json
{
  "action": "get",
  "topic_id": "topic-example"
}
```

## manage_plan

只读计划：

```json
{
  "action": "get",
  "plan_id": "plan-example"
}
```

## start_content_run

启动指定选题的默认生产：

```json
{
  "action": "start",
  "topic_ids": [
    "topic-example"
  ]
}
```

## get_producer_skill

读取 Skill 正文：

```json
{
  "skill_id": "demo--0123456789abcdef",
  "path": "SKILL.md"
}
```

## update_producer_skill_file

按预期摘要更新 Skill：

```json
{
  "skill_id": "demo--0123456789abcdef",
  "path": "SKILL.md",
  "content": "示例 Skill 正文。",
  "expected_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

## manage_skill_library

查看当前账号的 Skill 库：

```json
{
  "action": "get"
}
```

## manage_material

列出账号音乐素材：

```json
{
  "action": "list",
  "offset": 0,
  "limit": 10
}
```

## get_creator_tasks

查看栏目失败任务：

```json
{
  "series_id": "series-example",
  "statuses": [
    "failed"
  ]
}
```

## prepare_publication

按 Run 准备发布资料：

```json
{
  "run_id": "run-example"
}
```

## read_file

精确搜索当前会话归档：

```json
{
  "path": "C:/example/session-archive/result.txt",
  "search": "目标受众"
}
```
