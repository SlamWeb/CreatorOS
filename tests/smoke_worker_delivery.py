"""Regression for real completed-image failures; local files, no paid image calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.integrations.native_production import (
    CHECKPOINT, ingest, load_checkpoint, recover_checkpoint, request_digest,
)
from creatoros.integrations.worker_protocol import record_task, record_thread, record_turn
from creatoros.integrations.visual_production import atomic_json
from tests.smoke_native_production import create_pair, prepare_attempt, image, make_delivery, assert_value_error


def main():
    with TemporaryDirectory(prefix="worker-delivery-") as tmp:
        root = Path(tmp)
        catalog, pair = create_pair(root)
        directory = root / "run/revision-001/attempt-001"
        cp = prepare_attempt(directory, pair, catalog)
        generated = root / "generated"
        first, second = [generated / cp.thread_id / name for name in ("first.png", "edited.png")]
        image(first)
        image(second, (150, 40, 60))
        make_delivery(directory, cp.thread_id, first)
        ingest(directory, cp, generated)
        preview = Path(cp.pages[0].image_path)
        preview_bytes = preview.read_bytes()
        # Exact reported bug #1: the same content.md is completed after image delivery.
        (directory / "work/page.md").write_text("Final content and verified sources.", encoding="utf-8")
        ingest(directory, cp, generated)
        assert cp.pages[0].content == "Final content and verified sources."
        # Exact reported bug #2: worker chooses its edited image and records edit prompt.
        make_delivery(directory, cp.thread_id, second, prompt="initial prompt; IPA correction")
        ingest(directory, cp, generated)
        assert cp.pages[0].source_image_path == str(second)
        assert preview.read_bytes() == preview_bytes
        assert Path(cp.pages[0].image_path) != preview
        valid_checkpoint = (directory / CHECKPOINT).read_bytes()
        # A half-written/bad revision does not destroy the last valid preview.
        make_delivery(directory, cp.thread_id, first, content_file="../outside.md")
        assert_value_error(lambda: ingest(directory, cp, generated))
        assert (directory / CHECKPOINT).read_bytes() == valid_checkpoint
        make_delivery(directory, cp.thread_id, second, prompt="initial prompt; IPA correction")
        ingest(directory, cp, generated)
        cp.turn_completed = True
        atomic_json(directory / CHECKPOINT, cp)
        make_delivery(directory, cp.thread_id, first)
        assert_value_error(lambda: ingest(directory, cp, generated))
        # Completed SDK + newer valid work: offline recovery, no worker/model needed.
        cp.turn_completed = False
        atomic_json(directory / CHECKPOINT, cp)
        record_task(directory, kind="production", scope={"pack_id": "test"},
                    input_ref="production_request.txt", deliverable="work/delivery.json")
        record_thread(directory, cp.thread_id)
        record_turn(directory, "turn-real", "production", "completed")
        recovered_dir = directory.parent / "attempt-002"
        prepare_attempt(recovered_dir, pair, catalog)
        recovered = recover_checkpoint(recovered_dir, request_digest(recovered_dir), generated)
        assert recovered and recovered.turn_completed
        assert recovered.pages[0].source_image_path == str(first)
        assert json.loads((recovered_dir / "work/previous-delivery.json").read_text())["complete"]
        assert load_checkpoint(recovered_dir)
        # Last failed SDK turn must not be overridden by an earlier success / files.
        record_turn(directory, "turn-failed", "delivery_repair", "failed")
        # For receipt predicate itself, planning success and failed latest turn are false.
        from creatoros.integrations.worker_protocol import completed_delivery_turn
        assert not completed_delivery_turn(directory, cp.thread_id)
        record_turn(directory, "turn-plan", "composition", "completed")
        assert not completed_delivery_turn(directory, cp.thread_id)
    print("worker_delivery_smoke=passed mutable_preview immutable_final offline_recovery sdk_failure")


if __name__ == "__main__":
    main()
