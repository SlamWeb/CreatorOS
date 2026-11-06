"""Freeze/run isolated guarded business suite with explicit gateway settings.

Sidecar records experiment-only settings missing from the existing manifest.
Never overwrite a batch; no live request without --live. The guard is not a
network sandbox. Model and Judge calls are billable.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from dotenv import dotenv_values

from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.web.chat import ACCOUNT_TOOLS


ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true")
    p.add_argument("--batch", required=True)
    p.add_argument("--mode", choices=("legacy", "discovery", "discovery-fast"), required=True)
    p.add_argument("--cases", default="B05,B06,B07,B10")
    p.add_argument("--env-file", type=Path, default=Path("D:/CreatorOS/.env"))
    p.add_argument("--ffmpeg-dir", type=Path, default=Path("C:/Users/13779/.codex/worktrees/creatoros-plan-live-e2e/CreatorOS/tmp/ci-tools/ffmpeg"))
    a = p.parse_args()
    if not a.live:
        print("No model calls without --live"); return
    if not a.ffmpeg_dir.joinpath("ffprobe.exe").is_file():
        raise SystemExit("ffprobe missing")
    env = os.environ.copy()
    env.update({k: v for k, v in dotenv_values(a.env_file).items() if v is not None})
    env["PATH"] = str(a.ffmpeg_dir) + os.pathsep + env.get("PATH", "")
    env["CREATOROS_TOOL_PROTOCOL"] = "legacy" if a.mode == "legacy" else "discovery"
    env["CREATOROS_TOOL_FASTPATH"] = "1" if a.mode == "discovery-fast" else "0"
    batch = ROOT / "data/agent-benchmark-v2" / a.batch
    if batch.exists():
        raise SystemExit("batch already exists; create a new one")
    base = [sys.executable, "-m", "creatoros.evaluation.benchmark.run"]
    freeze = subprocess.run(base + ["freeze", "--dataset", "docs/agent-eval/tool-calling-v1/cases.json",
        "--batch", a.batch, "--cases", a.cases, "--nonvideo-guard"], cwd=ROOT, env=env)
    if freeze.returncode:
        raise SystemExit(freeze.returncode)
    scope = RuntimeContext(ROOT, creator_id="fixture-account", session_file=ROOT / "tmp/gateway-schema-fixture/messages.json",
                           archive_only_reads=True, allowed_tools=ACCOUNT_TOOLS)
    original = model_tool_schemas(tools, scope)
    schemas = original if a.mode == "legacy" else ToolGateway(scope, original, enable_direct=a.mode == "discovery-fast").model_schemas()
    configuration = {"protocol": env["CREATOROS_TOOL_PROTOCOL"], "fastpath": env["CREATOROS_TOOL_FASTPATH"],
        "note": "Existing benchmark manifest does not freeze these environment variables; this sidecar is explicit experimental metadata.",
        "wrapper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": a.cases.split(","), "python_version": sys.version,
        "ffprobe_sha256": hashlib.sha256(a.ffmpeg_dir.joinpath("ffprobe.exe").read_bytes()).hexdigest(),
        "intended_tool_schema_sha256": digest(schemas), "tool_schema_estimate": estimate_tokens(schemas),
        "intended_tool_names": [schema["function"]["name"] for schema in schemas]}
    with (batch / "experiment-config.json").open("x", encoding="utf-8") as out:
        json.dump(configuration, out, ensure_ascii=False, indent=2)
    proc = subprocess.run(base + ["run", "--batch", a.batch, "--workers", "1"], cwd=ROOT, env=env)
    # Record actual model-visible tools, including incomplete or missing evidence.
    observed = []
    for record_dir in sorted(path for path in batch.glob("records/*") if path.is_dir()):
        request_file = record_dir / "requests.json"
        try:
            rows = json.loads(request_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            observed.append({"request_file": str(request_file), "error_type": type(exc).__name__,
                             "matches_intended": None})
            continue
        if not isinstance(rows, list) or not any(isinstance(row, dict) and row.get("purpose") == "agent" for row in rows):
            observed.append({"request_file": str(request_file), "error_type": "NoAgentRequests",
                             "matches_intended": None})
            continue
        for row in rows:
            if not isinstance(row, dict):
                observed.append({"request_file": str(request_file), "error_type": "InvalidRequestRow",
                                 "matches_intended": None})
                continue
            if row.get("purpose") != "agent":
                continue
            context = row.get("context")
            actual = context.get("tools") if isinstance(context, dict) else None
            observed.append({"case_id": row.get("case_id"), "request_id": row.get("request_id"),
                "tool_schema_sha256": digest(actual) if isinstance(actual, list) else None,
                "tool_names": [s.get("function", {}).get("name") for s in actual] if isinstance(actual, list) else None,
                "matches_intended": digest(actual) == digest(schemas) if isinstance(actual, list) else None})
    with (batch / "observed-tool-schemas.json").open("x", encoding="utf-8") as out:
        json.dump({"requests": observed, "source": "benchmark records/*/requests.json",
                   "complete": bool(observed) and all(r["matches_intended"] is not None for r in observed),
                   "all_match": bool(observed) and all(r["matches_intended"] is True for r in observed)},
                  out, ensure_ascii=False, indent=2)
    raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
