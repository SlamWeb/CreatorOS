"""Small frozen batches for read-only HTTP/browser projection tests, not live scores."""
import json
from pathlib import Path
from uuid import uuid4

from creatoros.config import PROJECT_ROOT


def seed_surface_batch(root, *, legacy=False):
    root = Path(root)
    batch_id = ("legacy-" if legacy else "controlled-") + uuid4().hex[:12]
    directory = root / batch_id
    record_id = uuid4().hex
    record = directory / "records" / record_id
    record.mkdir(parents=True)
    dataset = json.loads((PROJECT_ROOT / "docs/agent-eval/benchmark-v2/cases.json").read_text(encoding="utf-8"))
    scores = {"protocol": "case-rubrics-v3", "program": {"score": 75.0, "coverage": 1, "passed": 3, "total": 4, "missing": 0, "status": "failed"},
              "semantic": {"score": None, "observed_score": 100.0, "coverage": 0.5, "passed": 1, "total": 2, "missing": 1, "status": "needs_review"},
              "critical_status": "passed", "combined_score": None}
    scores["program"]["items"] = [
        {"id": f"p{n}", "label": f"受控程序规则 {n}", "status": "passed" if n < 4 else "failed", "detail": f"程序证据 {n}"} for n in range(1, 5)]
    scores["semantic"]["items"] = [
        {"id": "s1", "question": "回复是否准确？", "status": "passed", "detail": "语义证据：回复与保存结果一致。"},
        {"id": "s2", "question": "切入点是否准确？", "status": "needs_review", "detail": "语义证据缺失，等待复核。"}]
    row = {"case_id": "B11", "title": "单条手动选题入队", "status": "needs_review", "auto_status": "passed", "semantic_status": "needs_review",
           "record_paths": [f"records/{record_id}/report.json"]}
    report = {"case_id": "B11", "execution_status": "completed", "checks": [{"id": "outcome_01", "label": "受控：选题正确保存", "status": "passed", "detail": "仅用于验证读取，不是模型评测成绩。"}]}
    summary = {"dataset_id": "controlled-old-protocol" if legacy else dataset["dataset_id"], "planned": 1, "with_results": 1,
               "passed": 0, "failed": 0, "needs_review": 1, "cases": [row], "overall_status": "needs_review", "generated_at_utc": "2026-11-02T08:00:00Z"}
    if not legacy:
        row["scores"] = scores
        report["scores"] = scores
        summary["scores"] = scores
    for path, value in ((directory / "summary.json", summary), (directory / "frozen-cases.json", dataset),
                        (record / "report.json", report), (record / "calls.json", [{"tool": "manage_topics", "arguments": {"action": "create"}, "api_key": "synthetic-sensitive-value"}])):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    (record / "answer.txt").write_text("受控回复：已在英语辨析加入 borrow与lend，尚未生产。", encoding="utf-8")
    return {"batch_id": batch_id, "record_id": record_id}
