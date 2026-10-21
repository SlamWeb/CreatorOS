"""Real DeepSeek through isolated HTTP chat; no production or shared data writes."""
import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

import httpx

from creatoros.ai import DeepSeekProvider
from creatoros.ai.types import StreamEnd
from creatoros.web import chat as chat_module
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture


class RecordingProvider(DeepSeekProvider):
    def __init__(self, api_key):
        super().__init__(api_key, model="deepseek-flash", timeout_seconds=120, max_retries=0)
        self.calls = []
        self.client._client.event_hooks["request"].append(self.record_wire_options)

    def record_wire_options(self, request):
        body = json.loads(request.content)
        self.calls[-1].update(wire_effort=body.get("reasoning_effort"), wire_thinking=body.get("thinking"))

    def stream(self, context):
        messages, _ = context.to_request()
        sent = self._to_openai_messages(messages)
        self.calls.append({"effort": self.reasoning_effort, "actual_effort": self.actual_reasoning_effort,
            "assistant_messages": sum(m["role"] == "assistant" for m in sent),
            "assistant_with_reasoning": sum(m["role"] == "assistant" and "reasoning_content" in m for m in sent),
            "saved_reasoning_reused": sum(bool(m.get("reasoning_content")) for m in messages if m["role"] == "assistant")})
        for event in super().stream(context):
            if isinstance(event, StreamEnd):
                self.calls[-1].update(reasoning_returned=bool(event.reasoning_content),
                                     usage=event.usage.to_dict() if event.usage else None)
            yield event


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "ultra"))
    args = parser.parse_args()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY_missing")
    report = {"real_deepseek": True, "turns": [], "status": "running"}
    output = Path("tmp") / f"chat-context-reasoning-live-20261021-{uuid4().hex[:8]}" / "report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise SystemExit("report already exists; preserve the previous run")
    providers = []
    def factory():
        provider = RecordingProvider(api_key)
        providers.append(provider)
        return provider
    with TemporaryDirectory(prefix="chat-reasoning-live-") as temporary:
        root = Path(temporary)
        database, run_service, producer, _ = make_fixture(root)
        try:
            with patch.object(chat_module, "ACCOUNT_TOOLS", frozenset({"get_producer_skill"})):
                app = create_app(database=database, run_service=run_service,
                                 chat_root=root / "chats", chat_provider_factory=factory)
                with serve(app) as url, httpx.Client(base_url=url, timeout=180, trust_env=False) as client:
                    endpoint = "/api/agent/sessions"
                    doc = client.post(endpoint, json={"creator_id": "review-lab"}).json()
                    prompts = [
                        ("none", "只回复收到，不调用工具。"),
                        ("low", "请实际调用 get_producer_skill，读取绑定 Skill knowledge-to-carousel 的 SKILL.md，offset=0、limit=100，然后用一句中文概括。只读，不修改。"),
                        ("high", "请先准确计算从 k=1 到 137 的 floor(137/k) 的和，然后用一句中文给出数值以及刚才 Skill 的名称。无需再次读取文件。"),
                        ("max", "只用中文回复：已完成。无需调用工具。"),
                    ]
                    if args.effort:
                        prompts = [(args.effort, "只用中文回复：已完成。无需调用工具。")]
                    for effort, text in prompts:
                        request_id = str(uuid4())
                        payload = {"request_id": request_id, "text": text,
                                   "expected_version": doc["version"], "reasoning_effort": effort}
                        response = client.post(f"{endpoint}/{doc['id']}/turns", json=payload)
                        assert response.status_code == 202, response.status_code
                        deadline = monotonic() + 170
                        while monotonic() < deadline:
                            doc = client.get(f"{endpoint}/{doc['id']}").json()
                            if doc["status"] != "running":
                                break
                            sleep(.1)
                        else:
                            raise TimeoutError("isolated chat did not finish")
                        assert doc["status"] == "idle", doc.get("error")
                        assert doc["reasoning_effort"] == effort
                        budget = doc["context_usage"]
                        assert budget["measurement"] == "actual" and budget["context_window"] == 1_000_000
                        assert budget["input_tokens"] == providers[-1].calls[-1]["usage"]["input_tokens"]
                        assert budget["turn_id"] == request_id
                        refreshed = client.get(f"{endpoint}/{doc['id']}").json()
                        assert refreshed["context_usage"] == budget
                        assert client.post(f"{endpoint}/{doc['id']}/turns", json=payload).status_code == 202
                        changed = {**payload, "reasoning_effort": "high" if effort != "high" else "low"}
                        assert client.post(f"{endpoint}/{doc['id']}/turns", json=changed).status_code == 409
                        calls = providers[-1].calls
                        assert all(c["wire_effort"] == c["actual_effort"] for c in calls if c["actual_effort"] != "none")
                        if effort != "none":
                            assert all(c["assistant_messages"] == c["assistant_with_reasoning"] for c in calls)
                        report["turns"].append({"effort": effort, "calls": calls, "context_usage": budget})
                        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                        print(f"real_deepseek_effort={effort} requests={len(calls)} passed", flush=True)
                    if len(prompts) > 1:
                        assert any(e.get("name") == "get_producer_skill" and e.get("status") == "done" for e in doc["entries"])
                        assert any(c["saved_reasoning_reused"] for p in providers[2:] for c in p.calls)
                    assert producer.calls == 0
                    report.update(status="passed", producer_calls=0)
        except Exception as error:
            report.update(status="failed", exception_type=type(error).__name__)
            raise
        finally:
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            database.close()
    print(f"report={output.as_posix()}")


if __name__ == "__main__":
    main()
