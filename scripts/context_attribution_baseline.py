"""Reproduce the historical context baseline without executing any tools or models.

The selected session snapshots are local, ignored business data. This script
prints numeric metadata only; it does not copy their prompts or tool content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from creatoros.ai.context import ModelContext  # noqa: E402
from creatoros.session.context_trace import detailed_breakdown  # noqa: E402


# Pin the request, rather than selecting the latest, so later session activity
# cannot silently change the historical baseline. Labels describe observed tool
# activity, not an assertion about all of the user's intent.
SAMPLES = (
    ("topics_and_tasks", "cb1cdd58-2f46-499a-a3bc-88eed41c4525", "22fb46f495414d99bd4b20a033b029aa", "5354dccc47c69e0c2748363f496a1ab3ab3aead8e4c03ec121952c5ddc369c47", "430c5ff6105303591d9e4bf1c204582ddbaa7826ff233b0a19de7afe22c8316d"),
    ("plan_management", "efdd6fd9-b508-46d2-9991-2b085dca688d", "8e5d6195830b44abb04617d7b5d876b9", "072c30bf954f0e09a3c1bd26e92e31ce49b5f28e9600259d7849d76646fdfe32", "c379d02438fc4335048b3ed6db168e8b36866fd7e40cb2906ec89249b431eb7f"),
    ("skill_extraction", "dc932608-3961-40ca-8575-a86a5eea926b", "1b2d3a45df534af3a7596cddf4b1aadc", "ae7149e4737efe6e076e983d62295f658cc4c140f7b0ecc65017333b2ca5001f", "618154ce38cfa47cdbaf0c93dd3952d04781ee3d482753341570205b06c9e4aa"),
    ("skill_file_edit", "161b46ed-f656-486f-9606-fa661e12cbc6", "e56c9d58620a41719c67132ebf8cbfb2", "f29732e28ed367ae31287eafd7ede0cfd3c5ad09b82d36158d21a97ebd85cb30", "b01b4372526518c3e26301495f9e15c97e92ef7e7249ddf9f2bb36e274ab70cd"),
    ("mixed_short", "4b785545-0507-4f0a-a949-99a567e46b1e", "9dc3b0d3524a422b8c4eb2aca5ae6f93", "2cff77dadb7b6fb989cc40733884227a9f93d85a6667b3930cf6ab9f4482e948", "cccfa268489bee968810560b11c6ddea5c2ed4919273eb47652159720c7e4d77"),
    ("topic_lists", "55375df3-03d1-4e4b-b843-754d3c310ea1", "05944e85ee1f4764a3e9f7656d101e97", "105261704769b08799ad270119fbb4eb32872279f4e85cf11127c6ad5ef2ec53", "020b7441b07061c524c2b407b79e7ae5b9c315523c1fe09c0e73a0a5683c5c27"),
)


def analyze_sample(root: Path, label: str, session_id: str, request_id: str,
                   expected_snapshot_sha256: str, expected_trace_prefix_sha256: str) -> dict:
    session = root / session_id
    trace_path = session / "messages.context-trace.jsonl"
    snapshot_path = session / "messages.request-trace" / f"{request_id}.json"
    snapshot_bytes = snapshot_path.read_bytes()
    snapshot_sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
    if snapshot_sha256 != expected_snapshot_sha256:
        raise ValueError(f"snapshot digest mismatch: {label}")
    snapshot = json.loads(snapshot_bytes)
    if snapshot.get("request_id") != request_id:
        raise ValueError(f"snapshot request id mismatch: {label}")
    context = snapshot["context"]
    model_context = ModelContext.from_messages(context["messages"], context["tools"])
    details = detailed_breakdown(model_context, kind="main")

    rows = []
    target = None
    prefix_hash = hashlib.sha256()
    for line in trace_path.read_bytes().splitlines(keepends=True):
        prefix_hash.update(line)
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # A crashed writer may leave an incomplete line. Do not invent data.
            continue
        if row.get("event") != "finished" or row.get("request_kind") != "main":
            continue
        usage = row.get("usage") or {}
        if not isinstance(usage.get("input_tokens"), int):
            continue
        rows.append(row)
        if row.get("request_id") == request_id:
            target = row
            break
    if target is None:
        raise ValueError(f"finished main request with usage not found: {label}")
    if prefix_hash.hexdigest() != expected_trace_prefix_sha256:
        raise ValueError(f"trace prefix digest mismatch: {label}")
    usage = target["usage"]
    return {
        "label": label,
        "session_id": session_id,
        "request_id": request_id,
        "snapshot_sha256": snapshot_sha256,
        "trace_prefix_sha256": prefix_hash.hexdigest(),
        "snapshot_redacted": snapshot.get("redacted"),
        "model": target.get("model"),
        "started_at": target.get("started_at"),
        "recorded_main_requests_through_target": len(rows),
        "recorded_turns_through_target": len({row.get("turn_id") for row in rows if row.get("turn_id")}),
        "actual_first_input_tokens": rows[0]["usage"]["input_tokens"],
        "actual_input_tokens": usage["input_tokens"],
        "actual_output_tokens": usage.get("output_tokens"),
        "actual_cache_hit_tokens": usage.get("cache_hit_tokens"),
        "actual_cumulative_input_tokens": sum(row["usage"]["input_tokens"] for row in rows),
        "historical_trace_estimated_input_tokens": target.get("estimated_input_tokens"),
        "recomputed_estimated_input_tokens": sum(details["parts"].values()),
        "estimated_detail": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-root", type=Path, default=ROOT / "data" / "creatoros-agent-sessions")
    args = parser.parse_args()
    try:
        samples = [analyze_sample(args.sessions_root, *sample) for sample in SAMPLES]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Baseline unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"schema_version": 1, "samples": samples}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
