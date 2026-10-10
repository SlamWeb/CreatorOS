"""Freeze evaluation inputs; claim every paid execution once, without retries."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from creatoros.config import PROJECT_ROOT
from .run import now, write_json

MANIFEST = PROJECT_ROOT / "docs/agent-eval/freeze-manifest.json"


def hashes(root=PROJECT_ROOT):
    paths = set(root.joinpath("creatoros").rglob("*.py"))
    for directory in ("web/src", "web/dist", "web/live-eval"):
        paths.update(path for path in root.joinpath(directory).rglob("*") if path.is_file())
    paths.update(root.joinpath("tests").glob("*account*eval*.py"))
    paths.update(root / name for name in ("docs/agent-eval/cases.json", "web/playwright.live.config.ts",
        "web/package.json", "web/package-lock.json"))
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths) if path.is_file()}


def evaluator_path(name):
    return (name.startswith(("creatoros/evaluation/", "web/live-eval/"))
        or name.startswith("tests/") or name in {"docs/agent-eval/cases.json", "web/playwright.live.config.ts"})


def freeze():
    if MANIFEST.exists():
        raise ValueError("已存在冻结清单；不得覆盖旧基线。")
    dataset = json.loads(MANIFEST.with_name("cases.json").read_text(encoding="utf-8"))
    if dataset.get("status") != "frozen_not_run" or dataset["freeze"]["status"] != "frozen":
        raise ValueError("先完成执行器验证并设置明确冻结日期。")
    source = hashes()
    manifest = {"schema_version": 1, "revision": dataset["revision"], "frozen_at": dataset["freeze"]["frozen_at"],
        "source_hashes": source, "evaluator_hashes": {name: digest for name, digest in source.items() if evaluator_path(name)},
        "execution_keys": [f"E{number:02}" for number in range(1, 13) if number != 9] + ["E09:failed", "E09:unknown"],
        "policy": "先跑完整基线；不修、不重试挑答案。回归只允许业务源码改变，题目和判据不变。"}
    write_json(MANIFEST, manifest)
    return manifest


def verify(phase):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = manifest["source_hashes" if phase == "baseline" else "evaluator_hashes"]
    actual = hashes()
    if phase == "regression":
        actual = {name: digest for name, digest in actual.items() if evaluator_path(name)}
    mismatches = sorted(name for name in set(expected) | set(actual) if expected.get(name) != actual.get(name))
    if mismatches:
        raise ValueError("冻结来源发生改变，拒绝付费执行：" + ", ".join(mismatches[:10]))
    return manifest


def claim(output_root, batch_id, phase, case_id, variant, run_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch_id) or phase not in {"baseline", "regression"}:
        raise ValueError("非法批次/阶段。")
    manifest = verify(phase)
    key = case_id + (":" + (variant or "failed") if case_id == "E09" else "")
    if key not in manifest["execution_keys"]:
        raise ValueError("不属于冻结执行集。")
    directory = Path(output_root) / "batches" / batch_id / phase
    directory.mkdir(parents=True, exist_ok=True)
    record = {"batch_id": batch_id, "phase": phase, "key": key, "run_id": run_id, "started_at": now(),
              "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest()}
    # Exclusive creation: reconnecting the same server is fine, restarting the
    # paid runner for this slot is not. Abandoned attempts remain visible.
    with (directory / (key.replace(":", "-") + ".json")).open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    return record


def summarize(output_root, batch_id, phase):
    directory = Path(output_root) / "batches" / batch_id / phase
    rows = []
    for path in sorted(directory.glob("E*.json")):
        claim_record = json.loads(path.read_text(encoding="utf-8"))
        report_path = Path(output_root) / claim_record["run_id"] / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        rows.append({**claim_record, "status": report.get("auto_status", "evidence_missing"),
            "execution_status": report.get("execution_status", "failed"), "usage": report.get("usage"),
            "error": report.get("error"), "checks": report.get("checks", []),
            "url": f"/eval?case={claim_record['key'].split(':')[0]}&run={claim_record['run_id']}"})
    result = {"batch_id": batch_id, "phase": phase, "expected_executions": 13, "recorded": len(rows),
        "missing": sorted(set(json.loads(MANIFEST.read_text(encoding="utf-8"))["execution_keys"]) - {row["key"] for row in rows}),
        "counts": {status: sum(row["status"] == status for row in rows) for status in ("passed", "failed", "needs_review", "evidence_missing")},
        "runs": rows}
    write_json(directory / "summary.json", result)
    return result


def formal_guard(batch_id, snapshot):
    """Read-only data guard; evidence contains digests, never formal row values."""
    from sqlalchemy.engine import make_url
    from creatoros.config import DATABASE_URL
    if not isinstance(batch_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch_id) or snapshot not in {"before", "after"}:
        raise ValueError("非法检查批次/快照。")
    database = make_url(DATABASE_URL)
    if database.get_backend_name() != "sqlite" or not database.database:
        raise ValueError("正式数据检查仅支持本地 SQLite，不连接其他服务。")
    path = Path(database.database).resolve()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("BEGIN")
        tables = {}
        for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            quoted = '"' + name.replace('"', '""') + '"'
            rows = sorted(json.dumps(list(row), ensure_ascii=False, sort_keys=True, default=str)
                          for row in connection.execute("SELECT * FROM " + quoted))
            tables[name] = {"rows": len(rows), "sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest()}
    directories = [path.parent / ("producer-skills" if path.name == "creatoros.db" else path.stem + "-producer-skills"),
                   path.parent / (path.stem + "-agent-sessions"),
                   path.parent / (path.stem + "-agent-sessions-discussions"), PROJECT_ROOT / "outputs"]
    files = {file.as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
             for directory in directories for file in directory.rglob("*") if file.is_file()}
    record = {"database": path.as_posix(), "tables": tables, "files": files}
    root = PROJECT_ROOT / "data/agent-eval/batches" / batch_id
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"formal-{snapshot}.json"
    if destination.exists():
        raise ValueError("不能覆盖正式数据检查的原始证据。")
    write_json(destination, record)
    if snapshot == "after":
        before = json.loads((root / "formal-before.json").read_text(encoding="utf-8"))
        same = record == before
        write_json(root / "formal-guard.json", {"unchanged": same, "tables": len(tables), "files": len(files)})
        if not same:
            raise ValueError("正式数据发生变化，请先核查；不能声称数据未改。")
    print("正式数据只读检查：", snapshot, len(tables), "tables", len(files), "files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "verify", "summary", "guard"])
    parser.add_argument("--phase", choices=["baseline", "regression"], default="baseline")
    parser.add_argument("--batch-id")
    parser.add_argument("--snapshot", choices=["before", "after"])
    args = parser.parse_args()
    if args.action == "freeze":
        result = freeze()
        print("已冻结：", result["revision"], "13 executions")
    elif args.action == "verify":
        verify(args.phase)
        print("冻结来源校验通过：", args.phase)
    elif args.action == "guard":
        formal_guard(args.batch_id, args.snapshot)
    else:
        result = summarize(PROJECT_ROOT / "data/agent-eval", args.batch_id, args.phase)
        print(json.dumps({key: result[key] for key in ("batch_id", "phase", "recorded", "missing", "counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
