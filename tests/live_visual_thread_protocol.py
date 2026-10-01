"""Real same-thread multiple-turn SDK check. Text only; no images/research/DB."""
import asyncio
import json
from pathlib import Path
from tempfile import mkdtemp
from typing import Literal
from uuid import uuid4

from openai_codex import ApprovalMode, Sandbox
from creatoros.integrations.codex import CodexSdkProducer, ProductionModel, PRODUCTION_RULES, CODEX_MODEL, _production_client, _bounded_sdk
from time import monotonic
from creatoros.integrations.producer_skills import inherit_copy_permissions
from creatoros.integrations.production_progress import ProgressWriter


class Ready(ProductionModel):
    status: Literal["READY"]
    marker: str


async def check(directory):
    skill = directory / "SKILL.md"
    skill.write_text("---\nname: protocol-probe\ndescription: Text-only SDK multi-turn protocol probe.\n---\n"
                     "Return the requested READY JSON. No browsing, research, image generation or file writes.\n",
                     encoding="utf-8")
    inherit_copy_permissions(directory)
    producer = CodexSdkProducer.from_defaults()
    progress = ProgressWriter(directory, "visual", 2)
    marker = uuid4().hex
    deadline = monotonic() + 180
    async with _production_client(deadline) as codex:
        thread = await _bounded_sdk(codex.thread_start(model=CODEX_MODEL, cwd=str(directory), sandbox=Sandbox.read_only,
                                         approval_mode=ApprovalMode.deny_all, developer_instructions=PRODUCTION_RULES), deadline)
        receipts = []
        for order, prompt in enumerate([
            f"Text-only protocol check. Do not generate images, browse, research, or write files. "
            f"Return {{\"status\":\"READY\",\"marker\":\"{marker}\"}}. Remember this marker for the next turn.",
            "Text-only protocol check, no images, browsing, research or file writes. "
            "Return READY with the EXACT marker provided in the previous turn of this conversation."
        ], 1):
            progress.page("rendering", order, order - 1)
            result = await producer._execute_stage_async(prompt, directory, skill_name="protocol-probe",
                skill_path=skill, receipt_model=Ready, stage="visual", on_thread_started=None,
                cancel_event=None, thread=thread, progress=progress, response_name=f"probe_{order}", finish_stage=False)
            assert result.thread_id == thread.id and result.receipt.marker == marker
            receipts.append(result)
        progress.finish("completed")
        report = {"result": "passed", "thread_id": thread.id, "turns": 2, "model": CODEX_MODEL,
                  "effort": "xhigh", "images_generated": False,
                  "usage_cumulative": receipts[-1].usage.model_dump(), "evidence": str(directory)}
        (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)


def main():
    root = Path(__file__).parents[1] / "tmp"
    root.mkdir(exist_ok=True)
    asyncio.run(check(Path(mkdtemp(prefix="visual-thread-probe-", dir=root))))


if __name__ == "__main__":
    main()
