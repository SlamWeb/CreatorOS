"""Export parser-checked synthetic argument examples; never execute tools."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from creatoros.context import RuntimeContext  # noqa: E402
from creatoros.tools import tool_registry, tools  # noqa: E402
from creatoros.tools.host_contract import ACCOUNT_BOUND_TOOLS, bind_account_arguments, model_tool_schemas  # noqa: E402
from creatoros.web.chat import ACCOUNT_TOOLS  # noqa: E402


EXAMPLES = (
    ("manage_topics", "按 ID 只读选题", {"action": "get", "topic_id": "topic-example"}),
    ("manage_plan", "只读计划", {"action": "get", "plan_id": "plan-example"}),
    ("start_content_run", "启动指定选题的默认生产", {"action": "start", "topic_ids": ["topic-example"]}),
    ("get_producer_skill", "读取 Skill 正文", {"skill_id": "demo--0123456789abcdef", "path": "SKILL.md"}),
    ("update_producer_skill_file", "按预期摘要更新 Skill", {"skill_id": "demo--0123456789abcdef", "path": "SKILL.md", "content": "示例 Skill 正文。", "expected_digest": "a" * 64}),
    ("manage_skill_library", "查看当前账号的 Skill 库", {"action": "get"}),
    ("manage_material", "列出账号音乐素材", {"action": "list", "offset": 0, "limit": 10}),
    ("get_creator_tasks", "查看栏目失败任务", {"series_id": "series-example", "statuses": ["failed"]}),
    ("prepare_publication", "按 Run 准备发布资料", {"run_id": "run-example"}),
    ("read_file", "精确搜索当前会话归档", {"path": "C:/example/session-archive/result.txt", "search": "目标受众"}),
)


def render():
    scope = RuntimeContext(ROOT, creator_id="synthetic-audit-owner", archive_only_reads=True,
                           allowed_tools=ACCOUNT_TOOLS)
    visible = {item["function"]["name"]: item["function"]["parameters"]
               for item in model_tool_schemas(tools, scope)}
    # jsonschema is part of the local test environment. Validate the actual
    # visible JSON Schema and then the real account binder and parser.
    from jsonschema import Draft202012Validator
    lines = ["# 十个工具：合成参数示例", "",
             "与 [完整 Schema](ten-tools.md) 对应。以下均为**合成示意，不是实际调用**；"
             "只经模型可见 JSON Schema、账号绑定器及真实参数解析器检查，没有执行工具。"
             "ID、digest 和路径均为占位值，通过解析不代表对象存在、账号可访问、版本有效、操作获授权或请求一定成功。"
             "例如生产、编辑和发布准备需满足各自业务授权与状态规则。", "",
             "带 `creator_id` 的账号绑定工具由宿主注入当前账号；下面展示模型提交的参数，故不包含该字段。"
             "归档路径必须来自当前会话的实际工具返回，示例路径不能直接使用。", ""]
    for name, label, arguments in EXAMPLES:
        Draft202012Validator(visible[name]).validate(arguments)
        bound = bind_account_arguments(name, json.dumps(arguments, ensure_ascii=False), scope)
        parsed = tool_registry[name].parse_arguments(bound)
        if name in ACCOUNT_BOUND_TOOLS and parsed.get("creator_id") != scope.creator_id:
            raise ValueError(f"账号绑定失败: {name}")
        lines += [f"## {name}", "", f"{label}：", "", "```json",
                  json.dumps(arguments, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("输出路径已存在")
    content = render()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"validated {len(EXAMPLES)} examples; no tools executed")


if __name__ == "__main__":
    main()
