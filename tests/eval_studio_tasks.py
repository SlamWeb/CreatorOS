"""Small development set: real DeepSeek + HTTP, synthetic isolated business data."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx

from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.runs import ContentRunService
from creatoros.storage import Database, ContentRepository, upgrade_database
from creatoros.web import create_app
from tests.agent_studio_support import serve
from tests.smoke_topic_research import seed_batch


CASES = [
    {"id": "catalog", "prompt": "查询知识实验室账号下有哪些栏目，只查询，告诉我栏目名字。", "selection": []},
    {"id": "select_second", "prompt": "读取调研批次 {batch}，只选第二条，保留原内容，生成入队预览供我确认。不要入队或生产。", "selection": ["c2"]},
    {"id": "rename", "prompt": "读取调研批次 {batch}，只选第二条，标题改成‘上下文不是越长越好’，切入点和来源保持原样。生成预览，不要确认或生产。", "selection": ["c2"], "title": "上下文不是越长越好"},
    {"id": "batch_order", "prompt": "读取调研批次 {batch}，两条都要，把第二条放在第一条前面。保留原文，准备入队预览让我确认，先不要入队和生产。", "selection": ["c2", "c1"]},
]
FORBIDDEN = {"start_content_run", "research_series_topics", "install_producer_skill"}


class ForbiddenAction:
    def submit(self, *args, **kwargs):
        raise ValueError("评测禁止启动生产、安装或重新调研。")


def write_report(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def grade_preview(after, expected, topic_id, replacement_title=None):
    return {
        "titles_and_order": [t["title"] for t in after] == [replacement_title or c["title"] for c in expected],
        "identity_and_evidence": len(after) == len(expected) and all(
            t["topic_id"] == topic_id(c["id"]) and c["angle"] in (t.get("brief") or "")
            and all(s["url"] in (t.get("brief") or "") for s in c["sources"])
            for t, c in zip(after, expected)),
    }


def evaluate(case, root):
    root.mkdir(parents=True)
    url = f"sqlite:///{(root / 'eval.db').resolve().as_posix()}"
    upgrade_database(url)
    db = Database(url)
    research = TopicResearchService(db, ProducerSkillCatalog(skills_root_for(db)))
    app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs"),
                     topic_research_service=research)
    # Guard external work at the host. These scenarios exercise real read/preview tools.
    research.submit = ForbiddenAction().submit
    app.state.skill_installs.submit = ForbiddenAction().submit
    app.state.executor.submit = ForbiddenAction().submit
    report = {"case": case["id"], "passed": False, "checks": {}, "usage": None}
    started = monotonic()
    try:
        with serve(app) as base, httpx.Client(base_url=base, timeout=20) as client:
            creator = client.post("/api/creators", json={"display_name": "知识实验室"}).json()
            series = client.post(f"/api/creators/{creator['id']}/series", json={"name": "每日图解AI"}).json()
            batch = seed_batch(research, series["id"])
            doc = client.post("/api/agent/sessions", json={}).json()
            report["session_id"] = doc["id"]
            response = client.post(f"/api/agent/sessions/{doc['id']}/turns", json={
                "request_id": str(uuid4()), "expected_version": doc["version"],
                "text": case["prompt"].format(batch=batch["id"])})
            response.raise_for_status()
            deadline = monotonic() + 180
            while monotonic() < deadline:
                doc = client.get(f"/api/agent/sessions/{doc['id']}").json()
                if doc["status"] != "running":
                    break
                sleep(0.5)
            report["checks"]["completed"] = doc["status"] == "idle"
            message_path = root / "eval-agent-sessions" / doc["id"] / "messages.json"
            messages = json.loads(message_path.read_text(encoding="utf-8"))
            calls = [c for m in messages for c in m.get("tool_calls", [])]
            names = [c["name"] for c in calls]
            report["tool_calls"] = calls
            report["messages_path"] = str(message_path.resolve())
            report["checks"]["no_forbidden_calls"] = not FORBIDDEN.intersection(names)
            report["checks"]["queue_unchanged"] = not ContentRepository(db).list_topics(series["id"])
            usage = [e for e in doc["entries"] if e["kind"] == "usage"]
            report["model_calls_with_usage"] = len(usage)
            if usage:
                report["usage"] = {k: sum(e[k] for e in usage) if all(isinstance(e.get(k), int) for e in usage) else None
                                   for k in ("input_tokens", "output_tokens", "cache_hit_tokens")}
            if not case["selection"]:
                answer = "\n".join(e.get("text", "") for e in doc["entries"] if e["kind"] == "assistant")
                report["checks"]["catalog_queried"] = {"list_creators", "list_creator_series"}.issubset(names)
                report["checks"]["correct_series_in_answer"] = "每日图解AI" in answer
            else:
                results = []
                for m in messages:
                    if m["role"] == "tool":
                        try:
                            value = json.loads(m["content"])
                            if isinstance(value, dict) and "operation_id" in value:
                                results.append(value)
                        except (ValueError, TypeError):
                            pass
                report["checks"]["preview_created"] = bool(results)
                if results:
                    operation = client.get(f"/api/operations/{results[-1]['operation_id']}").json()
                    write_report(root / "operation.json", operation)
                    after = operation["preview"]["changes"][0]["after_topics"]
                    expected = [next(c for c in batch["candidates"] if c["id"] == cid) for cid in case["selection"]]
                    report["checks"].update(grade_preview(after, expected,
                        lambda cid: research.topic_id(batch["id"], cid), case.get("title")))
            report["passed"] = all(report["checks"].values())
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        db.close()
    report["elapsed_seconds"] = round(monotonic() - started, 2)
    write_report(root / "result.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=[c["id"] for c in CASES])
    args = parser.parse_args()
    root = Path("tmp") / ("studio-eval-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    root.mkdir(parents=True)
    report = {"dataset": "studio-development-v1", "synthetic_candidates": True, "results": []}
    for case in CASES:
        if args.case and args.case != case["id"]:
            continue
        result = evaluate(case, root / case["id"])
        report["results"].append(result)
        report["passed"] = sum(r["passed"] for r in report["results"])
        report["total"] = len(report["results"])
        write_report(root / "report.json", report)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    print(f"report={root / 'report.json'}", flush=True)
    raise SystemExit(0 if report["passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()
