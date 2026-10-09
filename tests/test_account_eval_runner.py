"""Controlled SDK faults over the real HTTP/harness; not model quality scores."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from openai.types.chat import ChatCompletionChunk

from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.evaluation.run import run_e01
from creatoros.evaluation.store import EvalStore


def controlled_provider(mode="read"):
    provider = DeepSeekProvider(api_key="controlled-not-a-real-key", max_retries=0)
    provider.model = "controlled-sdk-fault"
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if mode == "error":
            raise RuntimeError("受控 SDK 连接故障，不应丢失已取得的证据。")
        if len(calls) == 1 and mode == "read":
            delta = {"tool_calls": [{"index": 0, "id": "call-read", "type": "function",
                "function": {"name": "list_creators", "arguments": '{"offset":0,"limit":100}'}}]}
            reason = "tool_calls"
        else:
            delta = {"content": "词汇实验室包含四格词汇和双语速记；当前仅查询目录，不生产。"}
            reason = None if mode == "truncated" else "stop"
        chunk = ChatCompletionChunk(id="controlled-chunk", created=0, model=provider.model,
            object="chat.completion.chunk", choices=[{"index": 0, "delta": delta, "finish_reason": reason}],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        return iter([chunk])

    provider.client.chat.completions.create = create
    return provider


class AccountEvalRunnerTests(unittest.TestCase):
    def test_actual_http_tool_loop_and_read_only_report(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "reports"
            report = run_e01(root, provider_factory=controlled_provider, execution_mode="controlled")
            self.assertEqual(report["execution_status"], "completed", report.get("error"))
            self.assertEqual(report["auto_status"], "passed", report["checks"])
            store = EvalStore(root)
            self.assertEqual(store.detail(report["run_id"])["status"], "needs_review")
            directory = root / report["run_id"]
            requests = json.loads((directory / "requests.json").read_text(encoding="utf-8"))
            transport = json.loads((directory / "transport.json").read_text(encoding="utf-8"))
            self.assertEqual(len(requests), 2)
            self.assertEqual([r["trace_request_id"] for r in requests], [r["trace_request_id"] for r in transport])
            self.assertEqual(report["usage"]["total_tokens"], 30)
            self.assertTrue(report["code_fingerprint"])
            self.assertEqual(json.loads((directory / "before.json").read_text(encoding="utf-8")),
                             json.loads((directory / "after.json").read_text(encoding="utf-8")))
            self.assertEqual(json.loads((directory / "external_attempts.json").read_text()), [])

    def test_truncated_stream_cannot_become_passed_despite_host_idle(self):
        with TemporaryDirectory() as temporary:
            report = run_e01(temporary, provider_factory=lambda: controlled_provider("truncated"), execution_mode="controlled")
            self.assertEqual(report["execution_status"], "failed")
            self.assertEqual(report["auto_status"], "failed", report["checks"])
            check = next(row for row in report["checks"] if row["id"] == "execution_completed")
            self.assertEqual(check["status"], "failed")

    def test_sdk_failure_retains_partial_evidence_and_failure_status(self):
        with TemporaryDirectory() as temporary:
            report = run_e01(temporary, provider_factory=lambda: controlled_provider("error"), execution_mode="controlled")
            self.assertEqual(report["execution_status"], "failed")
            self.assertNotEqual(report["auto_status"], "passed")
            self.assertEqual(EvalStore(temporary).detail(report["run_id"])["status"], "failed")
            directory = Path(temporary) / report["run_id"]
            for name in ("oracle.json", "requests.json", "transport.json", "trace.json", "messages.json", "before.json", "after.json"):
                self.assertTrue((directory / name).is_file(), name)
            self.assertTrue(json.loads((directory / "transport.json").read_text())[0].get("error_type"))

    def test_provider_setup_failure_preserves_seeded_world_and_user_ledger(self):
        def unavailable():
            raise ValueError("受控 Provider 初始化失败。")
        with TemporaryDirectory() as temporary:
            report = run_e01(temporary, provider_factory=unavailable, execution_mode="controlled")
            self.assertEqual(report["execution_status"], "failed")
            self.assertEqual(report["auto_status"], "needs_review")
            directory = Path(temporary) / report["run_id"]
            self.assertTrue(json.loads((directory / "before.json").read_text())["database"])
            self.assertTrue(json.loads((directory / "messages.json").read_text()))
            self.assertEqual(json.loads((directory / "requests.json").read_text()), [])
            self.assertEqual(EvalStore(temporary).detail(report["run_id"])["status"], "failed")


if __name__ == "__main__":
    unittest.main()
