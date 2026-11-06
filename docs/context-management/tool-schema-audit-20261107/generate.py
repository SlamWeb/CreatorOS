"""Export the current model-visible account schemas without calling a model.

Run from the CreatorOS checkout using ``python docs/context-management/
tool-schema-audit-20261107/generate.py --output-dir <new-directory>``.
Only tool metadata is exported. No account data, model requests or credentials.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from creatoros.ai.context import estimate_tokens  # noqa: E402
from creatoros.context import RuntimeContext  # noqa: E402
from creatoros.tools import tools  # noqa: E402
from creatoros.tools.host_contract import ACCOUNT_BOUND_TOOLS, model_tool_schemas  # noqa: E402
from creatoros.web.chat import ACCOUNT_TOOLS  # noqa: E402


SELECTED = (
    ("manage_topics", "选题查询与编辑"),
    ("manage_plan", "多步骤计划"),
    ("start_content_run", "内容生产提交"),
    ("get_producer_skill", "读取 Skill"),
    ("update_producer_skill_file", "编辑 Skill 文件"),
    ("manage_skill_library", "账号 Skill 库"),
    ("manage_material", "账号音乐素材"),
    ("get_creator_tasks", "查询账号任务"),
    ("prepare_publication", "发布准备"),
    ("read_file", "会话归档回读"),
)


def export(output: Path):
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("输出目录必须不存在或为空，避免覆盖既有审计。")
    output.mkdir(parents=True, exist_ok=True)
    scope = RuntimeContext(ROOT, creator_id="synthetic-audit-owner", archive_only_reads=True,
                           allowed_tools=ACCOUNT_TOOLS)
    schemas = model_tool_schemas(tools, scope)
    names = {schema["function"]["name"]: schema for schema in schemas}
    if len(names) != len(schemas) or any(name not in names for name, _ in SELECTED):
        raise ValueError("工具名重复或指定工具缺失")

    ordered = sorted(schemas, key=estimate_tokens, reverse=True)
    table = ["| 排名 | 工具 | 估算 token | 宿主绑定账号 |", "|---:|---|---:|---|"]
    for position, schema in enumerate(ordered, 1):
        name = schema["function"]["name"]
        table.append(f"| {position} | `{name}` | {estimate_tokens(schema):,} | {'是' if name in ACCOUNT_BOUND_TOOLS else '否'} |")
    (output / "inventory.md").write_text(
        "# 当前账号 Agent 工具 Schema 占用\n\n"
        f"本次从注册表生成 {len(schemas)} 个工具，完整数组合计估算 **{estimate_tokens(schemas):,} tokens**；"
        "每工具单独估算相加与数组总数可能因序列化取整略有差别。\n\n"
        "此估算使用 `creatoros.ai.context.estimate_tokens`：序列化后 ASCII 字符约 4 个算 1 token、"
        "非 ASCII 字符按 1 个算 1 token，再取整；不是模型 tokenizer 或供应商实际 usage。"
        "它衡量工具声明体积，不代表这些内容都冗余，也不含 system、账号资料、历史和用户消息。\n\n"
        "范围为 `ACCOUNT_TOOLS`、`creator_id` 已绑定、`archive_only_reads=True` 的 Web 账号会话；"
        "宿主先筛选工具、重写特定 description、隐藏 `creator_id`、移除可机械还原的参数标题。"
        "`creator_id` 由 `bind_account_arguments` 为表中‘是’的工具注入；这不说明表中‘否’的工具没有账号权限校验。"
        "数据仅为工具元数据，不含真实账号或工具调用。\n\n"
        "选中的十个工具完整模型可见 JSON 见 [10 个工具完整说明](ten-tools.md)。\n\n"
        + "\n".join(table) + "\n", encoding="utf-8")

    intro = ["# 十个代表性工具：实际发给模型的完整 Schema", "",
             "以下是当前代码生成的 **Web 账号会话模型可见工具声明**，不是一次实际工具调用，也不是账号数据。"
             "每段完整保留 JSON 的描述、属性、类型、枚举、默认值、required、嵌套定义及可见字段；"
             "仅排版缩进，不改内容。工具 description 是给模型的用途和正负规则，parameters 是其可传参数及限制。",
             "", "账号已由宿主绑定的工具不会在这些模型可见 Schema 中显示 `creator_id`。"
             "宿主执行前会注入当前账号，模型若试图显式指定其他账号会被拒绝；其他工具的实际权限还由各工具和业务接口校验。"
             "`read_file` 仅展示账号会话的当前会话归档版本。", "", "## 导航", ""]
    for index, (name, label) in enumerate(SELECTED, 1):
        intro.append(f"{index}. [{label} · {name}](#tool-{name.replace('_', '-')})")
    intro.append("")
    for name, label in SELECTED:
        entry = names[name]
        intro += [f'<a id="tool-{name.replace("_", "-")}"></a>', f"## {name}", "", f"**{label}** · 估算 {estimate_tokens(entry):,} tokens · "
                  + ("宿主注入账号。" if name in ACCOUNT_BOUND_TOOLS else "不属于自动注入账号字段的七个工具。"),
                  "", "```json", json.dumps(entry, ensure_ascii=False, indent=2), "```", ""]
    (output / "ten-tools.md").write_text("\n".join(intro), encoding="utf-8")
    (output / "all-schemas.json").write_text(json.dumps(schemas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tools": len(schemas), "selected": len(SELECTED),
                      "estimated_schema_tokens": estimate_tokens(schemas), "output": str(output)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    export(args.output_dir)


if __name__ == "__main__":
    main()
