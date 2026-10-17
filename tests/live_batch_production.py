"""Opt-in real DeepSeek -> Web tools -> real Codex batch smoke.

This is a loopback HTTP integration probe, NOT browser E2E. Model calls cost
tokens/images. All application data/Skill registration/output manifests remain
in a retained isolated tmp directory. No approval or publication is performed.
Run: python -m tests.live_batch_production --run
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import httpx

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.codex import CODEX_EFFORT, CODEX_MODEL
from creatoros.integrations.codex_executable import resolve_codex_executable
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.runs import ContentRunService
from creatoros.session.context_trace import read_trace
from creatoros.storage import ContentRepository, CreatorPlatform, Database, TopicSource, upgrade_database
from creatoros.web.app import create_app
from tests.agent_studio_support import serve


SKILL = """---
name: queue-single-image-probe
description: 为隔离批量生产验证，将一个简单工程概念制作为单张中文教学图。
creatoros-output: social-content-pack.image-carousel
---

# 单张教学图

每个选题仅用原生生图工具生成一张 4:5 竖版教学图，不做系列或多页。
暖白底、蓝色强调，用两个到三个清楚的图形和箭头表达核心关系。
图内只保留简短中文标题和必要标签；发布文案用两句话解释图意。
知识范围只需解释当前选题中的常识，不额外联网调研，不修改 Skill。
"""
PROMPT = (
    "请把当前账号「单图队列验证」栏目里现有的两个选题一起批量提交生产。"
    "这两条都已确认，请用一次批量生产操作一起安排，不要只提交第一条。"
    "每个选题遵循栏目绑定的 Skill，仅实际生成一张简单教学图。"
    "不要新增选题、修改 Skill、返工、批准或发布；提交后告诉我是否两条都已接收，不必等待生图结束。"
)
TERMINAL = {"awaiting_approval", "approved", "failed", "interrupted", "cancelled"}


def save(path, report):
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def capture_runs(client):
    response = client.get("/api/runs")
    response.raise_for_status()
    runs = []
    for item in response.json()["items"]:
        detail = client.get(f"/api/runs/{item['id']}")
        detail.raise_for_status()
        runs.append(detail.json())
    return runs


def evaluate(report):
    runs = report.get("runs", [])
    calls = report.get("tool_calls", [])
    batch_calls = []
    for call in calls:
        if call.get("name") == "start_content_run":
            args = call.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if len(args.get("topic_ids", [])) == 2:
                batch_calls.append(call)
    attempts = [a for run in runs for revision in run["revisions"] for a in revision["attempts"]]
    ordered = sorted(attempts, key=lambda a: a["started_at"])
    serial = (len(ordered) == 2 and ordered[0].get("completed_at") is not None
              and ordered[0]["completed_at"] <= ordered[1]["started_at"])
    return {
        "agent_used_one_batch_call": len(batch_calls) == 1,
        "two_unique_runs": len(runs) == 2 and len({run["topic_id"] for run in runs}) == 2,
        "one_attempt_each": len(attempts) == 2 and all(len(run["revisions"]) == 1
            and len(run["revisions"][0]["attempts"]) == 1 for run in runs),
        "serial_attempts": serial,
        "both_awaiting_human_approval": len(runs) == 2 and all(run["status"] == "awaiting_approval" for run in runs),
        "one_real_image_each": len(runs) == 2 and all(len(run["revisions"][0]["cards"]) == 1 for run in runs),
        "distinct_codex_threads": len(attempts) == 2 and all(a["producer_thread_id"] for a in attempts)
            and len({a["producer_thread_id"] for a in attempts}) == 2,
        "no_approval_or_publication": all(run.get("publication") is None and
            all(revision.get("approved_at") is None for revision in run["revisions"]) for run in runs),
    }


def run(timeout, previous_probe=None):
    tmp = PROJECT_ROOT / "tmp"
    tmp.mkdir(exist_ok=True)
    # Windows tempfile.mkdtemp uses an owner-only ACL: Codex's sandbox identity
    # then cannot read Skill/work. A fresh ordinary directory inherits tmp's ACL.
    root = (tmp / f"live-batch-production-{uuid4().hex[:12]}").resolve()
    root.mkdir()
    report_path = root / "report.json"
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "probe_root": str(root),
              "transport": "real loopback HTTP account chat; NOT browser E2E", "real_deepseek": True,
              "real_codex": True, "codex_model": CODEX_MODEL, "codex_effort": CODEX_EFFORT,
              "prompt": PROMPT, "status": "starting", "probe_task_resubmission": False,
              "runtime_bounded_delivery_repair": "unchanged"}
    if previous_probe:
        report.update(previous_probe=str(Path(previous_probe).resolve()),
                      fixture_fix="Use ordinary mkdir inheriting parent ACL; Windows tempfile owner-only ACL blocked the SDK sandbox. Prior report retained unchanged.")
    save(report_path, report)
    print(json.dumps({"probe_root": str(root), "report": str(report_path)}, ensure_ascii=False), flush=True)
    db = None
    try:
        if os.name == "nt":
            acl = subprocess.run(["icacls", str(root)], check=True, capture_output=True,
                                 text=True, errors="replace", timeout=15)
            report["root_acl"] = {"checked_before_model_calls": True, "inherited": "(I)" in acl.stdout,
                                  "evidence": acl.stdout}
            if not report["root_acl"]["inherited"]:
                raise RuntimeError("Isolated probe root has no inherited ACL; no model call attempted")
            save(report_path, report)
            print(json.dumps({"root_acl": "inherited", "checked_before_model_calls": True}), flush=True)
        if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
            raise RuntimeError("DEEPSEEK_API_KEY unavailable; no model call attempted")
        resolve_codex_executable()  # Discovery only; never print credentials/authentication files.
        url = f"sqlite:///{(root / 'isolated.db').as_posix()}"
        upgrade_database(url)
        db = Database(url)
        skill = root / "fixture-skills" / "queue-single-image-probe"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(SKILL, encoding="utf-8")
        catalog = ProducerSkillCatalog(skills_root_for(db))
        registered = catalog.register_local(skill, role="legacy_end_to_end", source_note="Opt-in isolated live batch probe")
        repo = ContentRepository(db)
        repo.create_creator(creator_id="live-batch-account", display_name="隔离批量生产验证",
                            platform=CreatorPlatform.XIAOHONGSHU)
        repo.create_series(series_id="live-batch-series", creator_id="live-batch-account", name="单图队列验证",
                           description="每个选题一张简单教学图，验证串行队列；禁止批准或发布。",
                           audience="第一次接触工程概念的人", skill_name=registered["id"])
        for index, title in enumerate(["串行队列：一次只处理一件事", "幂等：重复点击只产生一个结果"], 1):
            repo.add_topic(topic_id=f"live-batch-topic-{index}", series_id="live-batch-series", title=title,
                           source=TopicSource.MANUAL, brief="仅生成一张小型中文教学图，不调研，不做多页。")
        service = ContentRunService(db, output_root=root / "outputs")
        app = create_app(database=db, run_service=service, chat_root=root / "chats", eval_root=root / "eval")
        with serve(app) as base, httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
            report.update(status="running", base_url=base, skill_id=registered["id"])
            response = client.post("/api/agent/sessions", json={"creator_id": "live-batch-account"})
            response.raise_for_status()
            session = response.json()
            report["session_id"] = session["id"]
            response = client.post(f"/api/agent/sessions/{session['id']}/turns", json={
                "request_id": str(uuid4()), "expected_version": session["version"], "text": PROMPT})
            response.raise_for_status()
            save(report_path, report)
            deadline, turn_deadline = monotonic() + timeout, monotonic() + 240
            previous, last_print = None, 0
            while monotonic() < deadline:
                response = client.get(f"/api/agent/sessions/{session['id']}")
                response.raise_for_status()
                session = response.json()
                report["session"] = session
                report["runs"] = capture_runs(client)
                ledger = root / "chats" / session["id"] / "messages.json"
                if ledger.exists():
                    messages = json.loads(ledger.read_text(encoding="utf-8"))
                    report["tool_calls"] = [c for m in messages for c in m.get("tool_calls", [])]
                    report["deepseek_requests"] = [item for item in read_trace(ledger, limit=100)["items"]
                        if item.get("event") == "finished" and item.get("request_kind") == "main"]
                report["checked_at"] = datetime.now(timezone.utc).isoformat()
                save(report_path, report)
                progress = {"agent": session["status"], "runs": [{"id": r["id"], "status": r["status"],
                    "queue": r.get("production_queue"), "thread_id": r.get("producer_thread_id")}
                    for r in report["runs"]]}
                if progress != previous or monotonic() - last_print >= 60:
                    print(json.dumps(progress, ensure_ascii=False), flush=True)
                    previous, last_print = progress, monotonic()
                if session["status"] != "running":
                    if len(report["runs"]) != 2:
                        raise RuntimeError("DeepSeek did not submit exactly two production Runs; no automatic retry")
                    if all(run["status"] in TERMINAL for run in report["runs"]):
                        break
                elif monotonic() > turn_deadline:
                    raise TimeoutError("DeepSeek account turn exceeded 240 seconds; no automatic retry")
                sleep(2)
            else:
                raise TimeoutError("Live batch completion deadline exceeded; records retained, no automatic retry")
            report["checks"] = evaluate(report)
            report["status"] = "passed" if all(report["checks"].values()) else "failed"
            # Actual generated images must be readable through the same Studio artifact route.
            report["image_http"] = []
            for run in report["runs"]:
                for revision in run["revisions"]:
                    for card in revision["cards"]:
                        image = client.get(card["url"])
                        report["image_http"].append({"run_id": run["id"], "status": image.status_code,
                                                     "bytes": len(image.content)})
            if any(image["status"] != 200 for image in report["image_http"]):
                report["status"] = "failed"
            save(report_path, report)
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        save(report_path, report)
    finally:
        if db is not None:
            db.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save(report_path, report)
    print(json.dumps({"status": report["status"], "checks": report.get("checks"),
                      "error": report.get("error"), "report": str(report_path)}, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly allow real paid DeepSeek and Codex image calls")
    parser.add_argument("--timeout", type=int, default=3900, help="Total production wait seconds; no retry on failure")
    parser.add_argument("--previous-probe", help="Retained previous failed probe, for a separately frozen fixture-correction run")
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    return run(args.timeout, args.previous_probe)


if __name__ == "__main__":
    raise SystemExit(main())
