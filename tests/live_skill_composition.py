"""Explicit real Codex text-only composition check; never invokes production.

Run: python -m tests.live_skill_composition --case all
Evidence stays in tmp/. No Studio database or installed Skills are changed.
"""
import argparse
import asyncio
import json
from pathlib import Path
from tempfile import mkdtemp
from time import monotonic

from openai_codex import ApprovalMode, Sandbox

from creatoros.integrations.codex import CODEX_EFFORT, CODEX_MODEL, PRODUCTION_RULES, CodexSdkProducer, _bounded_sdk, _production_client
from creatoros.integrations.native_production import (
    CHECKPOINT, Checkpoint, NativeProgress, request_digest, review_composition, skill_refs,
)
from creatoros.integrations.producer_skills import _digest, inherit_copy_permissions
from creatoros.integrations.visual_production import atomic_json


CASES = {
    "compatible": (
        "解释三个易混词，保留各词中英文说明。此 Skill 用 PageSpec 输出内容，不负责画风。",
        "本 Skill 只负责把内容转为漫画 Prompt，不自动生图。暖色动漫，版式按内容量安排。",
        "制作三个英语词的中英双语漫画图，最终需要真实图片。", "ready"),
    "adaptable": (
        "本次必须完整讲八个词，每个词都有英文解释和中文，不得省略任何一个词。",
        "默认使用一张六格漫画；这是默认偏好，可以调整分格或图片数以保证可读性。",
        "保留全部八个词和双语解释，不限定图片数。", "ready"),
    "conflict": (
        "本次必须八个词全部保留，每词独立占一个格，不能共用格。",
        "这个栏目的固定模板只能每张六格，不允许改成八格。",
        "必须只交付一张图片，恰好六格，同时必须完整放八个词，每词独立一格，不能共用格。",
        "needs_input"),
    "clarified": (
        "本次必须八个词全部保留，每词独立占一个格，不能共用格。",
        "这个栏目的固定模板只能每张六格，不允许改成八格。",
        "原要求只交付一张六格图，八个词每词独立一格。", "ready"),
}


async def check(root: Path, names: list[str]):
    producer = CodexSdkProducer.from_defaults()
    rows = []
    deadline = monotonic() + 600
    async with _production_client(deadline) as client:
        for name in names:
            mind, visual, brief, expected = CASES[name]
            directory = root / name
            (directory / "work").mkdir(parents=True)
            for role, body in (("mind", mind), ("production", visual)):
                folder = directory / "skills" / role
                folder.mkdir(parents=True)
                (folder / "SKILL.md").write_text(
                    f"---\nname: composition-{role}\ndescription: Isolated composition probe\n---\n\n{body}\n",
                    encoding="utf-8")
            request = json.dumps({"topic_title": "英语词义漫画", "topic_brief": brief,
                                  "series_description": "内容与呈现组合测试", "audience": "英语初学者",
                                  "revision_instruction": "取消原来的一张图限制，允许两张图；每图仍为六格，空余格可以留白。保留八词各占一格和双语。"
                                      if name == "clarified" else None}, ensure_ascii=False)
            (directory / "production_request.txt").write_text(request, encoding="utf-8")
            inherit_copy_permissions(directory)
            thread = await _bounded_sdk(client.thread_start(
                model=CODEX_MODEL, cwd=str(directory / "work"), sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all, developer_instructions=PRODUCTION_RULES), deadline)
            checkpoint = Checkpoint(input_digest=request_digest(directory), thread_id=thread.id,
                                    skill_digests={r: _digest(p.parent) for r, p in skill_refs(directory)})
            atomic_json(directory / CHECKPOINT, checkpoint)
            progress = NativeProgress(directory, checkpoint)
            review = await review_composition(thread, producer, directory, skill_refs(directory), request,
                                              progress, min(deadline, monotonic() + 180))
            progress.finish("completed")
            events = [json.loads(line) for line in (directory / "codex_trace.jsonl").read_text(encoding="utf-8").splitlines()]
            unexpected_tools = [e for e in events if e.get("item_type") in {"imageGeneration", "webSearch", "collabAgentToolCall"}]
            row = {"case": name, "expected": expected, **review.model_dump(), "thread_id": thread.id,
                   "usage": checkpoint.usage.model_dump(), "unexpected_tools": unexpected_tools,
                   "passed": review.status == expected and not unexpected_tools}
            rows.append(row)
            (root / "report.json").write_text(json.dumps(
                {"model": CODEX_MODEL, "effort": CODEX_EFFORT, "cases": rows},
                ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(row, ensure_ascii=False), flush=True)
    assert all(row["passed"] for row in rows), f"A case failed; inspect {root / 'report.json'}"
    print(f"skill_composition_live=passed cases={len(rows)} evidence={root}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["all", *CASES], required=True, nargs="+")
    choice = parser.parse_args().case
    temporary = Path(__file__).parents[1] / "tmp"
    temporary.mkdir(exist_ok=True)
    directory = Path(mkdtemp(prefix="skill-composition-probe-", dir=temporary))
    # Windows mkdtemp grants owner-only ACLs; Codex's sandbox account must
    # traverse the fixture root as well as the individual Skill directories.
    inherit_copy_permissions(directory)
    print(f"evidence={directory}", flush=True)
    asyncio.run(check(directory, list(CASES) if "all" in choice else choice))


if __name__ == "__main__":
    main()
