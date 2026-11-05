"""Compare pinned historical tool views with the pre-change Git revision.

Local, read-only replay: no model calls, business tools, or user text output.
The fixed revision is trusted project source and is loaded only for comparison.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from creatoros.ai.context import estimate_tokens  # noqa: E402
from creatoros.tools.model_projection import project_tool_messages  # noqa: E402
from scripts.context_attribution_baseline import SAMPLES, analyze_sample  # noqa: E402

BASELINE_COMMIT = "beb790d"
SOURCE = "creatoros/tools/model_projection.py"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-root", type=Path,
                        default=ROOT / "data" / "creatoros-agent-sessions")
    args = parser.parse_args()
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE_COMMIT}:{SOURCE}"], cwd=ROOT, text=True, encoding="utf-8")
    baseline = ModuleType("historical_model_projection")
    exec(compile(source, f"{BASELINE_COMMIT}:{SOURCE}", "exec"), baseline.__dict__)
    sessions = args.sessions_root
    report = []
    for sample in SAMPLES:
        label, session_id, request_id = sample[:3]
        analyze_sample(sessions, *sample)  # Verify snapshot and trace prefix digests.
        path = sessions / session_id / "messages.request-trace" / f"{request_id}.json"
        messages = json.loads(path.read_text(encoding="utf-8"))["context"]["messages"]
        def tool_total(project):
            return sum(estimate_tokens(message.get("content", "")) for message in project(messages)
                       if message.get("role") == "tool")
        report.append({"label": label, "baseline_tool_content_estimate": tool_total(baseline.project_tool_messages),
                       "current_tool_content_estimate": tool_total(project_tool_messages)})
    print(json.dumps({"baseline_commit": BASELINE_COMMIT, "samples": report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
