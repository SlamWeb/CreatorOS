import json
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.context_attribution_baseline import analyze_sample


class HistoricalBaselineTest(unittest.TestCase):
    def test_fixed_request_uses_only_prior_usage_and_does_not_expose_content(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "session"
            snapshots = session / "messages.request-trace"
            snapshots.mkdir(parents=True)
            request_id = "a" * 32
            secret_content = "private-user-text-must-not-appear"
            snapshot = {
                "request_id": request_id,
                "redacted": False,
                "context": {"tools": [{"type": "function", "function": {"name": "manage_topics"}}], "messages": [
                    {"role": "user", "content": secret_content},
                    {"role": "assistant", "content": "", "reasoning_content": secret_content,
                     "tool_calls": [{"id": "call-1", "name": "manage_topics", "arguments": '{"x": "y"}'}]},
                    {"role": "tool", "tool_call_id": "call-1", "content": secret_content},
                ]},
            }
            snapshot_bytes = json.dumps(snapshot).encode("utf-8")
            (snapshots / f"{request_id}.json").write_bytes(snapshot_bytes)
            def row(identity, amount):
                return {"event": "finished", "request_kind": "main", "request_id": identity,
                        "turn_id": identity, "usage": {"input_tokens": amount}}
            lines = [row("b" * 32, 10), row(request_id, 20), row("c" * 32, 100)]
            trace_lines = [(json.dumps(line) + "\n").encode("utf-8") for line in lines]
            (session / "messages.context-trace.jsonl").write_bytes(b"".join(trace_lines))
            snapshot_hash = hashlib.sha256(snapshot_bytes).hexdigest()
            trace_hash = hashlib.sha256(b"".join(trace_lines[:2])).hexdigest()
            result = analyze_sample(root, "fixture", "session", request_id, snapshot_hash, trace_hash)
            self.assertEqual(result["actual_input_tokens"], 20)
            self.assertEqual(result["actual_cumulative_input_tokens"], 30)
            self.assertEqual(result["recorded_main_requests_through_target"], 2)
            self.assertNotIn(secret_content, json.dumps(result))
            parts = result["estimated_detail"]["parts"]
            self.assertGreater(parts["history_reasoning"], 0)
            self.assertGreater(parts["tool_arguments"], 0)
            self.assertGreater(parts["tool_results"], 0)
            self.assertEqual(sum(parts.values()), result["recomputed_estimated_input_tokens"])
            self.assertEqual(result["estimated_detail"]["tool_results_by_tool"]["manage_topics"], parts["tool_results"])
            with self.assertRaisesRegex(ValueError, "snapshot digest mismatch"):
                analyze_sample(root, "fixture", "session", request_id, "bad", trace_hash)
            with self.assertRaisesRegex(ValueError, "trace prefix digest mismatch"):
                analyze_sample(root, "fixture", "session", request_id, snapshot_hash, "bad")

    def test_missing_pinned_request_fails(self):
        with TemporaryDirectory() as temporary:
            with self.assertRaises(FileNotFoundError):
                analyze_sample(Path(temporary), "missing", "session", "a" * 32, "bad", "bad")


if __name__ == "__main__":
    unittest.main()
