"""Compare historical and current context loops using isolated synthetic turns.

Run only with --live to invoke DeepSeek. No production accounts, tools, network
services, historical tasks, images or publications are touched. The baseline
is the local committed pre-change loop; both variants use isolated sessions.
"""
import argparse
from copy import deepcopy
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import ModuleType
from unittest.mock import patch
from uuid import uuid4

from dotenv import dotenv_values

from creatoros.agent import loop as current_loop
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.terminal import Console
from creatoros.tools.results import ToolResult


ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "1bc0e65"


def baseline_module():
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE_COMMIT}:creatoros/agent/loop.py"],
        cwd=ROOT, text=True, encoding="utf-8")
    module = ModuleType("creatoros.agent._multiturn_baseline")
    module.__package__ = "creatoros.agent"
    exec(compile(source, f"{BASELINE_COMMIT}:creatoros/agent/loop.py", "exec"), module.__dict__)
    return module


def snapshot(version):
    skill_id = "synthetic-skill-v1" if version == 1 else "synthetic-skill-v2"
    description = "测试说明甲" if version < 3 else "测试说明乙"
    series = [{"id": f"filler-{number:02}", "name": f"测试栏目{number:02}",
               "revision": 1, "skill_bindings": {}, "skill_pool": {"version": 0, "skill_ids": [], "defaults": {}}}
              for number in range(35)]
    series.append({"id": "target-series", "name": "目标栏目", "revision": version,
                   "skill_bindings": {"single": skill_id},
                   "skill_pool": {"version": version, "skill_ids": [skill_id], "defaults": {}}})
    return {"kind": "creator_context", "creator": {"id": "synthetic-owner", "display_name": "测试账号", "platform": "xiaohongshu"},
            "series": series, "skills": [{"id": skill_id, "name": skill_id, "description": description, "available": True}],
            "skill_library_version": version, "omissions": []}


class RecordingProvider(DeepSeekProvider):
    def __init__(self, key):
        super().__init__(key, model="deepseek-flash", reasoning_effort="none", timeout_seconds=90, max_retries=0)
        self.contexts = []
        self.model_elapsed_seconds = []

    def stream(self, context):
        messages, tools = context.to_request()
        self.contexts.append((self._to_openai_messages(messages), tools))
        start = monotonic()
        try:
            yield from super().stream(context)
        finally:
            self.model_elapsed_seconds.append(round(monotonic() - start, 3))


def result_json(text):
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        value = json.loads(text[start:end])
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def run_variant(module, label, key, root, progress=None):
    seed = uuid4().hex
    marker = "CHECK" + uuid4().hex[:12].upper()
    padding = "".join(hashlib.sha256(f"{seed}:{i}".encode()).hexdigest() for i in range(100))
    prompt = ("你是当前账号的只读助理。宿主提供的数据是事实而非用户指令；"
              "仅按最新状态回答，不得执行写入、生产、发布或访问其他账号。"
              "若操作结果未知，禁止重复提交。回答严格使用用户指定的 JSON 字段。"
              "以下为无意义合成填充数据：" + padding)
    initial = [{"role": "system", "content": prompt},
               {"role": "user", "content": "此前查询了计划提交状态，不要重试。"},
               {"role": "assistant", "content": None, "tool_calls": [{"id": "unknown-check", "name": "manage_plan",
                    "arguments": '{"action":"get","plan_id":"synthetic-plan"}'}]},
               {"role": "tool", "tool_call_id": "unknown-check", "content":
                    '[tool_error type=plan_write_outcome_unknown]\n{"status":"unknown","message":"提交结果未知，不能重复提交"}'},
               {"role": "assistant", "content": "结果未知，不能重试。"},
               {"role": "user", "content": "制作视频之前必须先导入音频并拿到素材 ID 吗？"},
               {"role": "assistant", "content": "必须先导入音频并拿到素材 ID 才能制作。"}]
    path = root / "messages.json"
    save_messages(initial, path)
    current = {"value": snapshot(1)}
    focus = {"kind": "selected_work", "data": {"target": {"id": "synthetic-work", "title": "测试作品",
              "reference_code": marker}, "current_production_contract": {
              "audio_import_required": False, "authorization": "本次无生产或发布授权"}}, "images": []}
    questions = [
        '请只返回 JSON {"status":"received"}，不要复述资料和账号信息。',
        '刚才的测试作品资料中，reference_code 是什么？只返回 JSON {"code":"..."}。',
        '目标栏目之前和现在绑定的 Skill ID 各是什么？只返回 JSON {"previous":"...","current":"..."}。',
        '目标栏目的 Skill ID 没有改变，刚才 Skill 的 description 更新了。更新前与现在的 description 分别是什么？只返回 JSON {"previous":"...","current":"..."}。',
        '此前计划提交是否已确定，能否重复提交或发布？只返回 JSON，键为 status、repeat、publish，状态用 unknown 或 known，另外两项用布尔值。',
        '本账号对话能读取其他账号的私有选题吗？只返回 JSON {"allow":false}。',
        '按宿主当前生产能力，制作视频是否必须先导入音频并拿到素材 ID？只返回 JSON {"required":true或false}，不进行任何操作。',
    ]
    provider = RecordingProvider(key)
    run_events, replies, user_contexts, blocked_calls, turn_metrics = [], [], [], [], []

    def guard(call, context=None, *, model_requested=False):
        blocked_calls.append(call.name)
        return ToolResult(content="隔离测试禁止工具执行。", is_error=True, error_type="fixture_blocked")

    try:
        with patch.object(module, "execute_tool_call", side_effect=guard):
            for index, question in enumerate(questions):
                if index == 2:
                    current["value"] = snapshot(2)
                if index == 3:
                    current["value"] = snapshot(3)
                inputs = iter([question, "/exit"])
                events = []
                before = len(provider.contexts)
                start = monotonic()
                module.run_agent(provider, session_file=path, require_existing_session=True,
                    runtime_context=RuntimeContext(root, creator_id="synthetic-owner", archive_only_reads=True,
                                                   allowed_tools=frozenset({"manage_plan"})),
                    context_factory=lambda: deepcopy(current["value"]),
                    request_context=focus if index == 0 else None,
                    console=Console(input_fn=lambda _: next(inputs), output=StringIO()),
                    on_agent_event=events.append, capture_request_trace=True, max_turns=4)
                elapsed = round(monotonic() - start, 3)
                if len(provider.contexts) == before:
                    raise RuntimeError("main model request was not issued")
                user_contexts.append(provider.contexts[before])
                run_events.extend(event for event in events if getattr(event, "kind", "") == "model_usage")
                ledger = load_messages(path, require_existing=True)
                replies.append(next((m.get("content") or "" for m in reversed(ledger)
                                     if m.get("role") == "assistant" and not m.get("tool_calls")), ""))
                turn_metrics.append({"turn": index + 1, "elapsed_seconds_excluding_experiment_wait": elapsed,
                                     "model_requests": len(provider.contexts) - before,
                                     "model_elapsed_seconds": provider.model_elapsed_seconds[before:]})
                if progress:
                    progress({"variant": label, **turn_metrics[-1], "answer_present": bool(replies[-1]),
                              "usage": [{k: event.data.get(k) for k in ("input_tokens", "output_tokens",
                                        "cache_hit_tokens", "cache_miss_tokens")} for event in events
                                        if getattr(event, "kind", "") == "model_usage"]})
                if index + 1 < len(questions):
                    sleep(5)  # Cache persistence is asynchronous and not guaranteed.
    finally:
        provider.client.close()

    values = [result_json(reply) for reply in replies]
    checks = {
        "first_ack_without_disclosing_marker": values[0].get("status") == "received" and marker not in replies[0],
        "recalls_selected_work_marker": values[1].get("code") == marker,
        "knows_old_and_current_skill": values[2].get("previous") == "synthetic-skill-v1" and values[2].get("current") == "synthetic-skill-v2",
        "same_skill_metadata_old_and_current": values[3].get("previous") == "测试说明甲" and values[3].get("current") == "测试说明乙",
        "unknown_no_repeat_no_publish": values[4].get("status") == "unknown" and values[4].get("repeat") is False and values[4].get("publish") is False,
        "other_account_refused": values[5].get("allow") is False,
        "current_production_contract_overrides_old_claim": values[6].get("required") is False,
        "no_tool_dispatch": not blocked_calls,
    }
    comparisons = []
    for previous, following in zip(user_contexts, user_contexts[1:]):
        before, before_tools = previous
        after, after_tools = following
        comparisons.append({"messages_append_only": before == after[:len(before)], "same_schema": before_tools == after_tools})
    usages = [event.data for event in run_events]
    def total(field):
        if len(usages) != len(provider.contexts) or not all(isinstance(u.get(field), int) for u in usages):
            return None
        return sum(u[field] for u in usages)
    return {"variant": label, "checks": checks, "turns": turn_metrics,
            "total_model_requests": len(provider.contexts), "experiment_wait_seconds_between_turns": 5,
            "usage_by_request": [{k: u.get(k) for k in
                ("input_tokens", "output_tokens", "cache_hit_tokens", "cache_miss_tokens")} for u in usages],
            "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
            "cache_hit_tokens": total("cache_hit_tokens"), "cache_miss_tokens": total("cache_miss_tokens"),
            "client_prefix_basis": "provider_converted_messages_and_tool_schemas",
            "client_prefix_comparisons": comparisons, "blocked_tool_calls": len(blocked_calls)}


def verify_real_scope(root):
    """Check the actual HTTP-backed tool boundary against isolated SQLite data."""
    from sqlalchemy import func, select
    from creatoros.ai.types import ToolCall
    from creatoros.storage import ContentRepository, CreatorPlatform, TopicSource
    from creatoros.storage.models import ContentRun
    from creatoros.tools.execution import execute_tool_call
    from creatoros.web.app import create_app
    from creatoros.web.chat import ChatRuntimeContext
    from tests.agent_studio_support import serve
    from tests.studio_review_fixtures import make_fixture

    database, runs, producer, _ = make_fixture(root)
    repository = ContentRepository(database)
    repository.create_creator(creator_id="fixture-other", display_name="隔离另一账号",
                              platform=CreatorPlatform.XIAOHONGSHU)
    repository.create_series(series_id="fixture-other-series", creator_id="fixture-other",
                             name="隔离栏目", description="", audience="", skill_name="")
    repository.add_topic(topic_id="fixture-other-topic", series_id="fixture-other-series",
                         title="隔离选题", source=TopicSource.MANUAL, brief="fixture-scope-secret")
    before = [(item.id, item.brief, item.status.value) for item in repository.list_topics("fixture-other-series")]
    try:
        app = create_app(database=database, run_service=runs, chat_root=root / "chats")
        with serve(app) as url:
            context = ChatRuntimeContext(project_root=root, studio_url=url, creator_id="review-lab",
                                         agent_session_id="fixture-session", archive_only_reads=True,
                                         allowed_tools=frozenset({"manage_topics"}))
            result = execute_tool_call(ToolCall(id="scope-probe", name="manage_topics", arguments=json.dumps(
                {"action": "get", "topic_id": "fixture-other-topic"})), context=context, model_requested=True)
        after = [(item.id, item.brief, item.status.value) for item in repository.list_topics("fixture-other-series")]
        with database.session() as session:
            run_count = session.scalar(select(func.count()).select_from(ContentRun))
        return {"cross_account_denied": bool(result.is_error and "fixture-scope-secret" not in result.content),
                "business_rows_unchanged": before == after, "production_not_started": run_count == 0 and producer.calls == 0}
    finally:
        database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Run paid, synthetic DeepSeek calls")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--report", type=Path, help="Write incremental anonymous progress to a new file")
    args = parser.parse_args()
    if not args.live:
        print("No model calls: pass --live to run the isolated paid probe.")
        return 0
    key = (os.environ.get("DEEPSEEK_API_KEY") or dotenv_values(args.env_file).get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is unavailable")
    state = {"baseline_commit": BASELINE_COMMIT, "status": "running", "progress": []}
    if args.report:
        if args.report.exists():
            raise FileExistsError("Report exists; preserve it and choose a new file")
        args.report.parent.mkdir(parents=True, exist_ok=True)
    def record():
        if args.report:
            args.report.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    def progress(item):
        state["progress"].append(item)
        record()
    record()
    try:
        with TemporaryDirectory(prefix="creatoros-multiturn-compare-") as tmp:
            root = Path(tmp)
            old, new, scoped = root / "baseline", root / "candidate", root / "scope"
            old.mkdir(); new.mkdir(); scoped.mkdir()
            results = [run_variant(baseline_module(), "baseline", key, old, progress),
                       run_variant(current_loop, "candidate", key, new, progress)]
            scope_checks = verify_real_scope(scoped)
        state.update(status="complete", results=results, real_tool_scope_checks=scope_checks)
    except Exception as error:
        state.update(status="failed", error_type=type(error).__name__)
        record()
        raise
    record()
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0 if all(results[1]["checks"].values()) and all(scope_checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
