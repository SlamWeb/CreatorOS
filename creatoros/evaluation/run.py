"""Explicit CLI: run E01 against the real account harness, in a fresh local world."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import httpx

from creatoros.ai.types import StreamEnd
from creatoros.config import PROJECT_ROOT
from creatoros.context import RuntimeContext
from creatoros.session.context_trace import read_trace
from creatoros.session.request_trace import RequestSnapshots, redact
from creatoros.tools.studio import list_creators
from creatoros.web.chat import AgentChatService, ACCOUNT_TOOLS
from tests.agent_studio_support import serve

from .fixture import E01Fixture, FIXTURE_VERSION
from .grader import grade_e01


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(redact(value)[0], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


class CapturedProvider:
    """Observe both public Provider entrypoints without changing context or tools."""
    def __init__(self, provider, path, session_file=None):
        self.provider, self.path, self.requests = provider, path, []
        self.session_file = session_file
        self.transport = []
        completions = provider.client.chat.completions
        original_create = completions.create
        def create(**kwargs):
            record = {"trace_request_id": self.requests[-1].get("trace_request_id"),
                      "request": deepcopy(kwargs), "public_outputs": [], "finish_reason": None, "usage": None}
            self.transport.append(record)
            write_json(path.with_name("transport.json"), self.transport)
            def public(response):
                doc = response.model_dump(mode="json")
                # No reasoning_content, HTTP headers, auth or provider internals.
                choices = [{"index": choice.get("index"), "finish_reason": choice.get("finish_reason"),
                    "output": {key: (choice.get("delta") or choice.get("message") or {}).get(key)
                               for key in ("content", "tool_calls", "role")}}
                    for choice in doc.get("choices", [])]
                record["public_outputs"].append({"choices": choices, "usage": doc.get("usage")})
                for choice in choices:
                    if choice["finish_reason"] is not None:
                        record["finish_reason"] = choice["finish_reason"]
                if doc.get("usage") is not None:
                    record["usage"] = doc["usage"]
            try:
                response = original_create(**kwargs)
            except Exception as error:
                record["error_type"] = type(error).__name__
                write_json(path.with_name("transport.json"), self.transport)
                raise
            if not kwargs.get("stream"):
                public(response)
                write_json(path.with_name("transport.json"), self.transport)
                return response
            def chunks():
                try:
                    for chunk in response:
                        public(chunk)
                        yield chunk
                except Exception as error:
                    record["error_type"] = type(error).__name__
                    raise
                finally:
                    write_json(path.with_name("transport.json"), self.transport)
            return chunks()
        completions.create = create

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def begin(self, context, method):
        messages, tools = context.to_request()
        started = [row for row in trace_rows(self.session_file) if row.get("event") == "started"] if self.session_file else []
        record = {"trace_request_id": started[-1]["request_id"] if started else None,
            "method": method, "context": {"messages": deepcopy(messages), "tools": deepcopy(tools),
            "max_output_tokens": context.max_output_tokens}, "events": [], "finish_reason": None, "usage": None}
        self.requests.append(record)
        write_json(self.path, self.requests)
        return record

    def stream(self, context):
        record = self.begin(context, "stream")
        try:
            for event in self.provider.stream(context):
                record["events"].append({"type": type(event).__name__, **asdict(event)})
                if isinstance(event, StreamEnd):
                    record["finish_reason"] = event.finish_reason
                    record["usage"] = event.usage.to_dict() if event.usage else None
                yield event
        except Exception as error:
            record["error_type"] = type(error).__name__
            raise
        finally:
            write_json(self.path, self.requests)

    def complete(self, context):
        record = self.begin(context, "complete")
        try:
            result = self.provider.complete(context)
            record["response"] = result.to_message()
            record["usage"] = result.usage.to_dict() if result.usage else None
            return result
        except Exception as error:
            record["error_type"] = type(error).__name__
            raise
        finally:
            write_json(self.path, self.requests)


def trace_rows(path):
    rows, cursor = [], 0
    while True:
        page = read_trace(path, cursor, 100)
        rows.extend(page["items"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            return rows


def collect(root, session_file, provider, fixture, execution, before, after, probe):
    errors = []
    messages, trace = [], []
    try:
        if session_file is not None:
            messages = json.loads(session_file.read_text(encoding="utf-8"))
            trace = trace_rows(session_file)
    except (OSError, ValueError) as error:
        errors.append({"source": "session", "error_type": type(error).__name__})
    store = RequestSnapshots(session_file) if session_file else None
    snapshots = []
    for request_id in dict.fromkeys(row.get("request_id") for row in trace if row.get("sent") is True):
        try:
            snapshots.append(store.read(request_id))
        except (OSError, ValueError) as error:
            errors.append({"source": "snapshot", "request_id": request_id, "error_type": type(error).__name__})
    archives = {}
    for path in session_file.parent.rglob("*") if session_file else []:
        if path.is_file() and ".tool-results" in str(path):
            try:
                archives[path.relative_to(session_file.parent).as_posix()] = path.read_text(encoding="utf-8")
            except (OSError, ValueError) as error:
                errors.append({"source": "archive", "error_type": type(error).__name__})
    final = next((message.get("content") or "" for message in reversed(messages)
                  if message.get("role") == "assistant" and not message.get("tool_calls")), "")
    evidence = {"oracle": fixture.oracle(), "requests": provider.requests if provider else [],
        "transport": provider.transport if provider else [], "snapshots": snapshots,
        "messages": messages, "trace": trace, "archives": archives, "before": before, "after": after,
        "probe": probe, "execution": execution, "external_attempts": fixture.external_attempts,
        "collection_errors": errors, "final_answer": final}
    for name, value in evidence.items():
        if name == "final_answer":
            (root / "answer.txt").write_text(redact(value)[0], encoding="utf-8")
        else:
            write_json(root / (name + ".json"), value)
    return evidence


def source_fingerprint():
    paths = sorted((*PROJECT_ROOT.joinpath("creatoros").rglob("*.py"),
                    PROJECT_ROOT / "docs/agent-eval/cases.json"))
    hashes = {path.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return hashes, digest


def run_e01(output_root=None, *, provider_factory=None, execution_mode="live", timeout_seconds=180):
    dataset = json.loads((PROJECT_ROOT / "docs/agent-eval/cases.json").read_text(encoding="utf-8"))
    case = next(row for row in dataset["cases"] if row["id"] == "E01")
    root = Path(output_root or PROJECT_ROOT / "data/agent-eval").resolve() / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    started, clock = now(), monotonic()
    hashes, fingerprint = source_fingerprint()
    write_json(root / "source_hashes.json", hashes)
    report = {"schema_version": 1, "run_id": root.name, "case_id": "E01", "dataset_id": dataset["dataset_id"],
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip(),
        "fixture_version": FIXTURE_VERSION, "started_at": started, "finished_at": None,
        "code_fingerprint": fingerprint, "dataset_sha256": hashes["docs/agent-eval/cases.json"],
        "execution_mode": execution_mode, "execution_status": "failed", "model": {"name": "unavailable", "provider": "DeepSeek"},
        "usage": None, "elapsed_seconds": 0, "error": None, "review": None, "manual_checks": case["manual_checks"]}
    fixture, captured = None, None
    before, after, probe, doc, session_file = None, None, None, None, None
    phase = "fixture"
    try:
        def factory():
            nonlocal captured
            captured = CapturedProvider((provider_factory or AgentChatService._provider)(), root / "requests.json", session_file)
            report["model"] = {"name": getattr(captured, "model", type(captured.provider).__name__),
                               "provider": type(captured.provider).__name__}
            return captured
        fixture = E01Fixture(root / "fixture", factory)
        phase = "environment"
        with serve(fixture.app) as base, httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
            session = client.post("/api/agent/sessions", json={"creator_id": fixture.creator_a})
            session.raise_for_status()
            doc = session.json()
            report["session_id"] = doc["id"]
            session_file = fixture.root / "sessions" / doc["id"] / "messages.json"
            context = RuntimeContext(project_root=fixture.root, studio_url=base, allowed_tools=ACCOUNT_TOOLS,
                archive_only_reads=True, creator_id=fixture.creator_a, agent_session_id=doc["id"])
            before = fixture.state()
            phase = "model"
            submission = client.post(f"/api/agent/sessions/{doc['id']}/turns", json={
                "request_id": str(uuid4()), "expected_version": doc["version"], "text": case["steps"][0]["text"]})
            submission.raise_for_status()
            deadline = monotonic() + timeout_seconds
            while True:
                response = client.get(f"/api/agent/sessions/{doc['id']}")
                response.raise_for_status()
                doc = response.json()
                if doc["status"] != "running":
                    break
                if monotonic() >= deadline:
                    raise TimeoutError("E01 超过观察时限，停止隔离会话，不自动重试。")
                sleep(0.2)
            phase = "evidence"
            # Separate controller probe. It never enters the model's conversation.
            tool_result = list_creators(offset=0, limit=100, context=context)
            probe = {"name": "list_creators", "arguments": {"offset": 0, "limit": 100},
                     "content": tool_result.content, "is_error": tool_result.is_error}
            after = fixture.state()
            evidence = collect(root, session_file, captured, fixture, doc, before, after, probe)
            report.update(grade_e01(evidence))
            terminal = next((check for check in report["checks"] if check["id"] == "execution_completed"), {})
            report["execution_status"] = "completed" if terminal.get("status") == "passed" else "failed"
            if report["execution_status"] == "failed":
                report["error"] = {"kind": "model", "message": doc.get("error") or "缺少完整模型结束信号或结束证据；不能仅凭宿主 idle 认定完成。"}
            usage = [request["usage"] for request in captured.requests] if captured else []
            if usage and all(item is not None for item in usage):
                report["usage"] = {key: sum(item[key] for item in usage) for key in ("input_tokens", "output_tokens", "total_tokens")}
            report["session_id"] = doc["id"]
    except Exception as error:
        evidence = {}
        if fixture is not None:
            try:
                after = fixture.state()
                evidence = collect(root, session_file, captured, fixture, doc, before, after, probe)
            except Exception as collection_error:
                write_json(root / "collection_error.json", {"type": type(collection_error).__name__})
        report.update(grade_e01(evidence))
        report["execution_status"] = "failed"
        report["error"] = {"kind": phase, "message": redact(str(error))[0]}
        write_json(root / "error.json", {"type": type(error).__name__, **report["error"]})
    finally:
        if fixture is not None:
            fixture.close()
        report.update(finished_at=now(), elapsed_seconds=round(monotonic() - clock, 3))
        names = sorted(path.name for path in root.iterdir() if path.is_file() and path.suffix in {".json", ".txt"}
                       and path.name not in {"report.json", "review.json"})
        report["evidence_files"] = [{"name": name, "label": name} for name in names]
        write_json(root / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["E01"], required=True)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    report = run_e01(args.output_root)
    print(json.dumps({key: report[key] for key in ("run_id", "case_id", "auto_status", "execution_status", "usage", "error")}, ensure_ascii=False))
    print(f"查看：/eval?case=E01&run={report['run_id']}；自动通过仍需人工复核。")
    raise SystemExit(1 if report["execution_status"] == "failed" or report["auto_status"] != "passed" else 0)


if __name__ == "__main__":
    main()
