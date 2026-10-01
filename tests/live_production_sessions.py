"""Real SDK stage/session probe; isolated files, no images or operational writes."""
import asyncio
import json
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from creatoros.integrations.codex import CodexSdkProducer, ProductionModel
from creatoros.integrations.producer_skills import ProducerSkillCatalog, freeze_skill, _digest, inherit_copy_permissions
from creatoros.integrations.skill_pair import StoryboardReceipt, mind_prompt


class Ready(ProductionModel):
    result: Literal["READY"]


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


async def check(directory):
    project = Path(__file__).parents[1]
    catalog = ProducerSkillCatalog(project / "data/producer-skills", project)
    selected = {record["role"]: record for record in catalog.list()
                if record["name"] in ("knowledge-to-storyboard-deep", "xiaobai")}
    refs = {}
    for role in ("mind", "production"):
        source = catalog.locate(selected[role]["id"])
        target = directory / "skills" / role
        freeze_skill(source, target, _digest(source))
        refs[role] = (selected[role]["name"], target / "SKILL.md")
    inherit_copy_permissions(directory)
    producer = CodexSdkProducer(project_root=project, generated_images_root=directory)
    request = json.dumps({"topic_title": "消息队列是什么：入门接口探针", "audience": "完全零基础",
                          "topic_brief": "仅做两页内容小样检验接口：先解释消息和队列是什么，再介绍生产者与消费者如何交接。查阅一份官方入门来源即可。本次不是完整面试内容，不做生图。"}, ensure_ascii=False)
    content = await producer._execute_stage_async(
        mind_prompt(refs["mind"][0], request), directory, skill_name=refs["mind"][0],
        skill_path=refs["mind"][1], receipt_model=StoryboardReceipt, stage="mind",
        on_thread_started=None, cancel_event=None,
    )
    assert len(content.receipt.pages) >= 2, "Probe must deliver the requested content, not a waiting placeholder"
    (directory / "storyboard.json").write_text(content.receipt.model_dump_json(indent=2), encoding="utf-8")
    visual = await producer._execute_stage_async(
        "@xiaobai 本轮仅测试内容交接，只读取指定 Skill，不调用生图、不制作图片。下面是完整 pages；请只返回 JSON {\"result\":\"READY\"}。\n"
        + content.receipt.model_dump_json(), directory, skill_name=refs["production"][0],
        skill_path=refs["production"][1], receipt_model=Ready, stage="visual",
        on_thread_started=None, cancel_event=None,
    )
    assert content.thread_id != visual.thread_id
    session_root = Path.home() / ".codex" / "sessions"
    for thread_id, skill_ref in ((content.thread_id, refs["mind"]), (visual.thread_id, refs["production"])):
        ledgers = list(session_root.rglob(f"*{thread_id}*.jsonl"))
        assert len(ledgers) == 1, "Cannot verify real session memory isolation"
        raw = ledgers[0].read_text(encoding="utf-8")
        assert "MEMORY_SUMMARY BEGINS" not in raw, "Global memory was injected"
        assert "image_gen__imagegen" not in raw, "Probe must not invoke image generation"
        observed = [text for line in raw.splitlines() for text in strings(json.loads(line))]
        skill_text = skill_ref[1].read_text(encoding="utf-8").strip()
        assert any(skill_text in text.replace("\r\n", "\n") for text in observed), "Stage Skill was not actually read"
    report = {"result": "passed", "mind_thread": content.thread_id, "visual_thread": visual.thread_id,
              "memory_summary_injected": False, "images_generated": False,
              "mind_skill_read": True, "visual_skill_read": True, "page_count": len(content.receipt.pages)}
    (directory / "probe_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


def main():
    # Keep probe evidence for inspection; this is not an official ContentRun.
    root = Path(__file__).parents[1] / "tmp"
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix=".sdk-session-probe-", dir=root) as staging:
        inherit_copy_permissions(Path(staging))
        directory = Path(staging) / "result"
        directory.mkdir()
        try:
            asyncio.run(check(directory))
        finally:
            saved = root / ("sdk-session-probe-" + uuid.uuid4().hex[:12])
            directory.rename(saved)
            inherit_copy_permissions(saved)
            print("evidence=" + str(saved), flush=True)


if __name__ == "__main__":
    main()
