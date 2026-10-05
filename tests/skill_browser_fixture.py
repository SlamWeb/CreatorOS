"""Actual local files for the isolated Skill-browser tests; never executes a script."""
from pathlib import Path

from PIL import Image


def seed_skill_files(catalog, root: Path):
    items = {}
    for role, name in (("mind", "inspector-mind"), ("production", "inspector-visual")):
        source = root / name
        (source / "assets").mkdir(parents=True, exist_ok=True)
        (source / "scripts").mkdir(exist_ok=True)
        (source / "references").mkdir(exist_ok=True)
        content = (
            f"---\nname: {name}\ndescription: 将新主题变成可复用的内容方法与图解。\n---\n\n"
            "# 内容与参考\n\n先理解问题，再按需读取参考。\n\n"
            "[查看脚本](scripts/example.py)\n\n![图解](assets/diagram.png)\n\n"
            "![不读取远程参考](https://example.invalid/private.png)\n\n"
            + "循序渐进，明确每一步回答的问题。\n\n" * 220
            + "完整 Skill 结尾标记\n"
        )
        (source / "SKILL.md").write_text(content, encoding="utf-8")
        (source / "scripts" / "example.py").write_text(
            "# 仅供查看，不应执行\nraise RuntimeError('SCRIPT_MUST_NOT_EXECUTE')\n", encoding="utf-8")
        (source / "references" / "readme.md").write_text(
            "# 文件关系\n\n[返回正文](../SKILL.md)\n\n参考来源与正文分开放置。\n", encoding="utf-8")
        (source / "manual.pdf").write_bytes(b"%PDF-1.4\nunsupported fixture")
        Image.new("RGB", (320, 180), "#cae4ff").save(source / "assets" / "diagram.png")
        items[role] = catalog.register_local(source, role=role)
    return items
