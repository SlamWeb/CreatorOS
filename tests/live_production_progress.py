"""Low-cost authenticated SDK probe for safe live production progress metadata."""
import asyncio
import json
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.codex import CODEX_EFFORT, CODEX_MODEL, CodexSdkProducer, ProductionModel
from creatoros.integrations.producer_skills import inherit_copy_permissions


class ProbeReceipt(ProductionModel):
    status: Literal["READY"] = Field(description="Must be exactly READY")


def main():
    evidence = Path(tempfile.mkdtemp(prefix="sdk-production-progress-", dir=PROJECT_ROOT / "tmp"))
    skill_path = evidence / "skill" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: progress-probe\ndescription: Return a fixed readiness receipt for an SDK smoke check.\n---\n"
        "For this probe, respond with exactly this JSON object: {\"status\": \"READY\"}. "
        "You may read this specified Skill file. Do not use other tools, browse, search, research, "
        "generate images, or write files.\n",
        encoding="utf-8",
    )
    inherit_copy_permissions(evidence)

    producer = CodexSdkProducer.from_defaults()
    run = asyncio.run(producer._execute_stage_async(
        'This is a text-only local SDK smoke check. Read only the specified attached Skill as needed, '
        'then return its exact JSON. Do not use other tools, browse, search, research, generate images, '
        'or write files.',
        evidence,
        skill_name="progress-probe",
        skill_path=skill_path,
        receipt_model=ProbeReceipt,
        stage="production",
        on_thread_started=None,
        cancel_event=None,
    ))

    assert run.receipt.status == "READY"
    assert CODEX_MODEL == "gpt-6-luna" and CODEX_EFFORT == "xhigh"
    progress = json.loads((evidence / "production_progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "completed"
    events = [json.loads(line) for line in (evidence / "codex_trace.jsonl").read_text(encoding="utf-8").splitlines()]
    observed = [event for event in events if event["stage"] == "production"]
    assert any(event["type"] == "item.started" and event.get("at") for event in observed)
    assert any(event["type"] == "item.completed" and event.get("at") for event in observed)
    assert any(event["type"] == "turn.completed" for event in observed)
    assert not {"webSearch", "imageGeneration"}.intersection(
        event.get("item_type") for event in observed if event["type"] in {"item.started", "item.completed"}
    )
    print(json.dumps({
        "result": "passed",
        "thread_id": run.thread_id,
        "model": CODEX_MODEL,
        "effort": CODEX_EFFORT,
        "usage": run.usage.model_dump(),
        "progress_status": progress["status"],
        "observed_event_types": sorted({event["type"] for event in observed}),
        "evidence_dir": str(evidence),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
