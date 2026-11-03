"""Read-only inspection of an image submission's durable evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .files import safe_file
from .image import CHECKPOINT, SUBMISSION, SUBMISSION_INTENT, Checkpoint, accepted_submission


def inspect_submission(directory: Path) -> dict:
    """Inspect local evidence only; never dispatch, accept, or modify files."""
    directory = Path(directory).absolute()
    intent, receipt = directory / SUBMISSION_INTENT, directory / SUBMISSION
    intent_present = intent.exists() or intent.is_symlink()
    receipt_present = receipt.exists() or receipt.is_symlink()
    result = {"status": "none", "intent_present": intent_present,
              "receipt_present": receipt_present, "receipt_matches_checkpoint": False,
              "matching_completed_turn": False}
    if not intent_present and not receipt_present:
        return result
    result["status"] = "unconfirmed"
    try:
        checkpoint = Checkpoint.model_validate_json(safe_file(directory, directory / CHECKPOINT, 8_000_000).read_text(encoding="utf-8"))
        saved = accepted_submission(directory, checkpoint) if checkpoint.submission_sha256 else None
        result["receipt_matches_checkpoint"] = bool(saved)
        if not intent_present:
            result["status"] = "legacy_receipt" if saved else "unconfirmed"
            return result
        intent_value = json.loads(safe_file(directory, intent, 32_000).read_text(encoding="utf-8"))
        if (not isinstance(intent_value, dict) or intent_value.get("protocol") != "image-submission-intent-v1"
                or intent_value.get("thread_id") != checkpoint.thread_id
                or intent_value.get("input_digest") != checkpoint.input_digest
                or not isinstance(intent_value.get("turn_id"), str) or not intent_value["turn_id"]
                or not isinstance(intent_value.get("payload_sha256"), str)):
            return result
        if not saved or saved.get("payload_sha256") != intent_value["payload_sha256"] or saved.get("turn_id") != intent_value["turn_id"]:
            return result
        worker = json.loads(safe_file(directory, directory / "worker_receipt.json", 256_000).read_text(encoding="utf-8"))
        turns = worker.get("turns") if isinstance(worker, dict) else None
        last = turns[-1] if isinstance(turns, list) and turns else None
        result["matching_completed_turn"] = bool(worker.get("protocol") == "creatoros-worker-v1"
            and worker.get("thread_id") == checkpoint.thread_id and isinstance(last, dict)
            and last.get("id") == intent_value["turn_id"]
            and last.get("phase") in {"production", "delivery_repair"} and last.get("status") == "completed")
        if result["matching_completed_turn"]:
            result["status"] = "completed_evidence"
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", required=True, type=Path, help="absolute path to the original Attempt")
    args = parser.parse_args()
    if not args.attempt.is_absolute() or not args.attempt.is_dir() or args.attempt.is_symlink():
        parser.error("--attempt must be an existing absolute non-link directory")
    print(json.dumps(inspect_submission(args.attempt), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
