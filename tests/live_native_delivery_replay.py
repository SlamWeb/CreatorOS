"""Replay an existing single-Skill failed delivery in tmp; no inference or new images."""
import argparse
import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.codex import CodexSdkProducer
from creatoros.integrations.native_production import CHECKPOINT, Checkpoint, Delivery
from creatoros.integrations.native_recovery import verify_history
from creatoros.integrations.visual_production import atomic_json
from creatoros.integrations.worker_protocol import _write
from creatoros.runs.artifacts import validate_artifact
from creatoros.skills.loader import SkillLoader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    source = args.directory.resolve()
    receipt = asyncio.run(verify_history(source))
    root = Path(tempfile.mkdtemp(prefix="native-delivery-replay-", dir=PROJECT_ROOT / "tmp"))
    previous = root / "revision-001/attempt-001"
    shutil.copytree(source, previous)
    cp = Checkpoint.model_validate_json((previous / CHECKPOINT).read_text(encoding="utf-8"))
    for page in cp.pages:
        page.image_path = str(previous / "partial-images" / Path(page.image_path).name)
    atomic_json(previous / CHECKPOINT, cp)
    delivery = Delivery.model_validate_json((previous / "work/delivery.json").read_text(encoding="utf-8-sig"))
    for item in delivery.artifacts:
        item.reference_assets = [str(previous / Path(ref).relative_to(source))
            if Path(ref).is_absolute() else ref for ref in item.reference_assets]
        if item.content_file and Path(item.content_file).is_absolute():
            item.content_file = str(previous / Path(item.content_file).relative_to(source))
    atomic_json(previous / "work/delivery.json", delivery)
    _write(previous / "worker_receipt.json", receipt)
    payload = json.loads((previous / "production_request.txt").read_text(encoding="utf-8"))
    skill_dir = previous / "skills/single"
    skill = next(iter(SkillLoader([skill_dir]).discover()))
    producer = CodexSdkProducer.from_defaults()
    with patch("creatoros.integrations.native_production._production_client",
               side_effect=AssertionError("Offline recovery must not invoke Codex")):
        produced = producer.produce_to(directory=root / "revision-001/attempt-002",
            production_protocol="native-v1", skill_name=skill.name, skill_directory=skill_dir, **payload)
    validation = validate_artifact(produced.directory, production_protocol="native-v1")
    report = {"status": "passed", "source": str(source), "output": str(produced.directory),
              "thread_id": receipt["thread_id"], "card_count": validation.card_count,
              "sdk_history_verified": True, "new_inference_calls": 0, "new_image_calls": 0,
              "artifact_digest": validation.artifact_digest}
    _write(root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
