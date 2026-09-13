"""Context development eval: real Runtime/HTTP/SQLite, synthetic fixed histories."""
import argparse
from copy import deepcopy
from datetime import datetime
from io import StringIO
import json
import os
from pathlib import Path
import shutil
from time import monotonic
from unittest.mock import patch

import httpx
from sqlalchemy import select

from creatoros.agent import loop
from creatoros.agent.compaction import CompactionPlan
from creatoros.agent.compactor import compact_session
from creatoros.ai.context import ModelContext
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.config import SYSTEM_PROMPT
from creatoros.context import RuntimeContext
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.runs import ContentRunService
from creatoros.session.artifacts import externalize
from creatoros.session.checkpoint import load_compaction_checkpoint
from creatoros.session.context_trace import trace_path
from creatoros.session.snapshot import save_messages, load_messages
from creatoros.storage import Database, upgrade_database
from creatoros.storage.models import Creator, CreatorPlatform, Series, PendingOperation
from creatoros.terminal import Console
from creatoros.web import create_app
from creatoros.web.chat import STUDIO_TOOLS, WEB_INSTRUCTIONS
from tests.agent_studio_support import serve
from tests.context_eval_cases import CASES, ARMS, fixture
from tests.eval_studio_tasks import ForbiddenAction, write_report, queue_snapshot, grade_preview
from tests.smoke_topic_research import seed_batch

KEEP = 200
INVALID_CASES = {"context-development-v1": {"restart": "历史先禁止新建、后创建预览，事件顺序矛盾；v1.1修正"}}


def database(root):
    url = f"sqlite:///{(root / 'eval.db').resolve().as_posix()}"
    upgrade_database(url)
    return Database(url)


def application(db, root):
    research = TopicResearchService(db, ProducerSkillCatalog(skills_root_for(db)))
    app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs"),
                     topic_research_service=research)
    research.submit = ForbiddenAction().submit
    app.state.executor.submit = ForbiddenAction().submit
    app.state.skill_installs.submit = ForbiddenAction().submit
    return app, research


def operations(db):
    with db.session() as session:
        return [{"id": o.id, "series": o.scope_series_id, "status": o.status.value,
                 "preview": deepcopy(o.preview_json), "version": o.version}
                for o in session.scalars(select(PendingOperation).order_by(PendingOperation.id))]


def seed(root, case):
    root.mkdir(parents=True)
    db = database(root)
    try:
        with db.session() as session:
            for suffix, name in [("a", "知识实验室"), ("b", "编程手记")]:
                session.add(Creator(id="creator-" + suffix, display_name=name,
                                    platform=CreatorPlatform.XIAOHONGSHU))
                session.flush()
                session.add(Series(id="series-" + suffix, creator_id="creator-" + suffix,
                                   name="每日图解AI", description="图解AI知识", audience="初学者", skill_name="knowledge-to-carousel"))
        app, research = application(db, root)
        a = seed_batch(research, "series-a", "a" * 32)
        b = seed_batch(research, "series-b", "b" * 32)
        existing = "{}"
        with serve(app) as base, httpx.Client(base_url=base) as client:
            if case in {"long_loop", "restart"}:
                response = client.post(f"/api/topic-research/{a['id']}/preview",
                                       json={"selections": [{"candidate_id": "c1"}]})
                response.raise_for_status()
                value = response.json()
                existing = json.dumps({"operation_id": value["id"], "status": value["status"],
                    "preview": value["preview"], "url": f"/series/series-a?operation={value['id']}"}, ensure_ascii=False)
        stages, prompt = fixture(case, a, b, existing)
        data = {"case": case, "stages": stages, "prompt": prompt, "a": a, "b": b,
                "initial_operations": operations(db), "initial_queue": queue_snapshot(db, ["series-a", "series-b"])}
        write_report(root / "fixture.json", data)
        return data
    finally:
        db.close()


def split(messages, previous_cut=1, previous_pin=None):
    live = deepcopy(messages[previous_cut:])
    if previous_pin is not None:
        live.insert(0, deepcopy(messages[previous_pin]))
    plan = CompactionPlan.from_context(ModelContext.from_messages(live, []),
                                       input_limit=995904, keep_recent_tokens=KEEP)
    if not plan.can_compact:
        return previous_cut, previous_pin
    def source(i):
        return previous_pin if previous_pin is not None and i == 0 else previous_cut + i - int(previous_pin is not None)
    return source(plan.first_retained_index), (source(plan.pinned_user_index) if plan.pinned_user_index is not None else None)


def grade(case, data, after, queue_unchanged, answer, calls):
    before = data["initial_operations"]
    initial_ids = {o["id"] for o in before}
    new = [o for o in after if o["id"] not in initial_ids]
    forbidden = {"start_content_run", "research_series_topics", "install_producer_skill"}
    checks = {"queue_unchanged": queue_unchanged,
              "no_forbidden_attempt": not forbidden.intersection(c["name"] for c in calls),
              "existing_operations_unchanged": [o for o in after if o["id"] in initial_ids] == before}
    if case in {"early_constraint", "restart"}:
        checks["no_new_preview"] = not new
        checks["required_answer"] = ("上下文" in answer if case == "early_constraint" else before[0]["id"] in answer)
    else:
        checks["exactly_one_preview"] = len(new) == 1
        if len(new) == 1:
            expected_series = "series-b" if case == "same_name" else "series-a"
            batch = data["b"] if case == "same_name" else data["a"]
            expected = [batch["candidates"][1]]
            title = {"changed_mind": "上下文需要边界", "evidence": "上下文预算实验-Z924"}.get(case)
            checks["correct_scope_status"] = new[0]["series"] == expected_series and new[0]["status"] == "awaiting_approval"
            changes = (new[0]["preview"] or {}).get("changes", [])
            checks["one_change"] = len(changes) == 1
            checks.update(grade_preview(changes[0].get("after_topics", []) if changes else [], expected,
                lambda cid: TopicResearchService.topic_id(batch["id"], cid), title))
    return checks


def usage_report(rows):
    sent = [r for r in rows if r.get("sent")]
    return {"known_tokens": {k: sum((r.get("usage") or {}).get(k, 0) or 0 for r in sent)
                              for k in ("input_tokens", "output_tokens")},
            "usage_complete": bool(sent) and all(r.get("usage") is not None for r in sent),
            "model_requests": len(sent)}


def summarize(report, path):
    results = report["results"]
    invalid = INVALID_CASES.get(report["dataset"], {})
    lines = ["# Context Eval · " + report["dataset"], "",
             "合成开发集，每题每组一次。成功看环境终态和准确字段；不是泛化指标。",
             "", "| 案例 | 完整历史 | 仅近期 | 摘要＋近期＋回读 |", "|---|---|---|---|"]
    for case in CASES:
        row = [next((r for r in results if r["case"] == case and r["arm"] == arm), None) for arm in ARMS]
        if case in invalid and any(row):
            lines.append(f"| {case} | 夹具无效 | 夹具无效 | 夹具无效 |")
        elif any(row):
            lines.append("| " + case + " | " + " | ".join(
                "未运行" if r is None else "运行错误" if r["error"] else "通过" if r["passed"] else "未完成"
                for r in row) + " |")
    lines += ["", "## 用量（含摘要，不是费用金额）", "",
              "| 组别 | 成功/总数 | 已知输入 | 已知输出 | 用量完整 |", "|---|---|---|---|---|"]
    for arm in ARMS:
        rows = [r for r in results if r["arm"] == arm and r["case"] not in invalid]
        lines.append(f"| {arm} | {sum(r['passed'] for r in rows)}/{len(rows)} | "
                     f"{sum(r['known_tokens']['input_tokens'] for r in rows)} | "
                     f"{sum(r['known_tokens']['output_tokens'] for r in rows)} | "
                     f"{bool(rows) and all(r['usage_complete'] for r in rows)} |")
    paired = []
    for case in CASES:
        if case in invalid:
            continue
        rows = [r for r in results if r["case"] == case and r["arm"] in {"full", "compact"}]
        if len(rows) == 2 and all(r["passed"] and r["usage_complete"] for r in rows):
            paired.extend(rows)
    lines += ["", "完整历史和压缩组共同成功的题目：" + "、".join(sorted({r["case"] for r in paired})), ""]
    for arm in ("full", "compact"):
        rows = [r for r in paired if r["arm"] == arm]
        lines.append(f"- {arm} 共同成功题的输入＋输出 Token：" + str(sum(sum(r["known_tokens"].values()) for r in rows)))
    lines += ["", "## 待分析失败", ""]
    for r in results:
        if not r["passed"] and r["case"] not in invalid:
            reasons = [k for k, v in r["checks"].items() if not v]
            lines.append(f"- {r['case']} / {r['arm']}：{r['error'] or ', '.join(reasons)}；见对应 result.json 和 request-*.json。")
    for case, reason in invalid.items():
        lines.append(f"- 排除 {case}：{reason}。原始判分保留在JSON，不计入有效题统计。")
    lines += ["", "限制：低保留预算强制切分；重复背景和固定测试 ID；无重试取优；",
              "恢复案例为保存后重新加载 checkpoint 和宿主调用，未模拟进程强杀；",
              "安全宿主禁止生产，因此还需结合工具尝试与预览状态判断，不能以无发布证明模型记住约束。"]
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


class CapturingProvider(DeepSeekProvider):
    def __init__(self, root):
        super().__init__(api_key=os.environ["DEEPSEEK_API_KEY"], reserve_output_tokens=4096,
                         timeout_seconds=45, max_retries=0)
        self.root = root
        self.count = 0

    def capture(self, context):
        self.count += 1
        messages, tools = context.to_request()
        write_report(self.root / f"request-{self.count:02}.json", {"messages": messages, "tools": tools})

    def complete(self, context):
        self.capture(context)
        return super().complete(context)

    def stream(self, context):
        self.capture(context)
        yield from super().stream(context)


def evaluate(case, arm, root, data):
    db = database(root)
    path = root / "messages.json"
    report = {"case": case, "arm": arm, "passed": False, "checks": {}, "error": None}
    start = monotonic()
    try:
        provider = CapturingProvider(root)
        history = [{"role": "system", "content": SYSTEM_PROMPT + "\n" + WEB_INSTRUCTIONS}]
        checkpoint, cut, pin = None, 1, None
        for index, stage in enumerate(data["stages"]):
            history.extend(deepcopy(stage))
            save_messages(history, path)
            externalize(history, path)
            cut, pin = split(history, cut, pin)
            if arm == "compact":
                checkpoint = compact_session(provider, history, [], checkpoint=checkpoint,
                    session_file=path, keep_recent_tokens=KEEP)
                if checkpoint is None:
                    raise RuntimeError("Expected compaction did not happen")
                assert (checkpoint.first_retained_index, checkpoint.pinned_user_index) == (cut, pin)
                write_report(root / f"checkpoint-{index}.json", checkpoint.to_dict())
                checkpoint = load_compaction_checkpoint(load_messages(path), path)
                assert checkpoint is not None
        report["cut"] = cut
        report["pin"] = pin
        app, _ = application(db, root)
        original_builder = loop.build_model_context
        def builder(messages, tools, checkpoint=None, skill_loader=None):
            if arm == "recent":
                messages = [messages[0], *([messages[pin]] if pin is not None else []), *messages[cut:]]
            return original_builder(messages, tools, checkpoint, skill_loader)
        events = []
        inputs = iter([data["prompt"], "/exit"])
        with serve(app) as base, patch.object(loop, "build_model_context", builder):
            loop.run_agent(provider, max_turns=8, session_file=path,
                runtime_context=RuntimeContext(root, studio_url=base, allowed_tools=STUDIO_TOOLS,
                                               archive_only_reads=True),
                console=Console(input_fn=lambda _: next(inputs), output=StringIO()), on_agent_event=events.append)
        new_messages = load_messages(path)[len(history):]
        calls = [c for m in new_messages for c in m.get("tool_calls", [])]
        answer = "\n".join(m.get("content") or "" for m in new_messages if m["role"] == "assistant" and not m.get("tool_calls"))
        report.update(final_answer=answer, tool_calls=calls, final_operations=operations(db))
        current_queue = queue_snapshot(db, ["series-a", "series-b"])
        # JSON round-trip normalizes tuple snapshots copied from the seed.
        report["checks"] = grade(case, data, report["final_operations"],
            json.loads(json.dumps(current_queue)) == data["initial_queue"], answer, calls)
        report["checks"]["completed"] = bool(new_messages) and new_messages[-1]["role"] == "assistant" and not new_messages[-1].get("tool_calls")
        report["passed"] = all(report["checks"].values())
    except Exception as error:
        report["error"] = type(error).__name__
    finally:
        db.close()
        rows = ([json.loads(line) for line in trace_path(path).read_text(encoding="utf-8").splitlines()]
                if trace_path(path).exists() else [])
        finished = [r for r in rows if r["event"] == "finished"]
        report.update(usage_report(finished))
        report["elapsed_seconds"] = round(monotonic() - start, 2)
        write_report(root / "result.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--report", type=Path, help="仅汇总已有 report.json，不调用模型")
    args = parser.parse_args()
    if args.report:
        summarize(json.loads(args.report.read_text(encoding="utf-8")), args.report)
        print(args.report.with_suffix(".md"))
        return
    root = Path("tmp") / ("context-eval-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True)
    report = {"dataset": "context-development-v1.1", "model": "deepseek-v4-flash", "results": []}
    for index, case in enumerate(CASES):
        if args.case and case != args.case:
            continue
        seed_root = root / case / "seed"
        data = seed(seed_root, case)
        data = json.loads(json.dumps(data))
        for arm in ARMS[index % 3:] + ARMS[:index % 3]:
            target = root / case / arm
            shutil.copytree(seed_root, target)
            result = evaluate(case, arm, target, data)
            report["results"].append(result)
            write_report(root / "report.json", report)
            print(case, arm, "PASS" if result["passed"] else "FAIL", result["error"], flush=True)
    print(f"report={root / 'report.json'}", flush=True)
    summarize(report, root / "report.json")


if __name__ == "__main__":
    main()
