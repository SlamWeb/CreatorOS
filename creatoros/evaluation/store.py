"""Read recorded eval evidence and persist explicit, versioned human reviews."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
from threading import RLock
from uuid import uuid4

from creatoros.config import PROJECT_ROOT
from creatoros.web.observation import clean, safe_path


STATUSES = {"passed", "failed", "needs_review"}
DIMENSIONS = {"task_success", "boundary_enforced", "state_consistent", "protocol_valid"}
RUN_ID = re.compile(r"^[a-f0-9]{32}$")
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


class EvalStoreError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code, self.code = status_code, code


def _invalid(message="评测报告损坏或不符合 schema v1。"):
    raise EvalStoreError(422, "eval_report_invalid", message)


def _json(raw):
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def _evidence_name(name):
    if not isinstance(name, str) or not name or len(name) > 512 or "\\" in name or ":" in name:
        return False
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or any(
        part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in name.split("/")
    ):
        return False
    forbidden = {"report.json", "review.json", ".env", ".git", "id_rsa", "id_ed25519"}
    return not any(part.lower() in forbidden or part.startswith(".") or
                   re.search(r"(?i)(?:secret|credential|password|cookie|token|private[-_]?key)", part)
                   for part in parts) and PurePosixPath(name).suffix.lower() in {".json", ".jsonl", ".txt", ".md", ".log"}


class EvalStore:
    def __init__(self, root=None, *, cases_path=None):
        # Construction and all GET paths must leave nonexistent roots nonexistent.
        self.root = Path(root if root is not None else PROJECT_ROOT / "data" / "agent-eval").absolute()
        self.cases_path = Path(cases_path or PROJECT_ROOT / "docs" / "agent-eval" / "cases.json").absolute()
        self.lock = RLock()

    def _bytes(self, path, *, maximum=MAX_REPORT_BYTES, root=None):
        try:
            path = safe_path(root or self.root, path)
            with path.open("rb") as stream:
                raw = stream.read(maximum + 1)
            if len(raw) > maximum:
                raise EvalStoreError(413, "eval_evidence_too_large", "评测文件超过读取上限。")
            return raw
        except (OSError, ValueError):
            raise EvalStoreError(404, "eval_evidence_unavailable", "评测文件缺失或不可安全读取。") from None

    def _dataset(self):
        try:
            doc = _json(self._bytes(self.cases_path, root=self.cases_path.parent))
            cases = doc["cases"]
            if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1 or not isinstance(doc["dataset_id"], str)
                    or not isinstance(cases, list) or not cases):
                raise ValueError()
            ids = set()
            for case in cases:
                if (not isinstance(case, dict) or not re.fullmatch(r"[EASBP]\d{2}", case["id"])
                        or case["id"] in ids or not isinstance(case["title"], str)
                        or case["category"] not in {"security", "persistence", "state", "tools"}
                        or case["split"] not in {"dev", "acceptance"}):
                    raise ValueError()
                steps, assertions, manual = case.get("steps"), case.get("assertions"), case.get("manual_checks")
                if not isinstance(steps, list) or not steps or not isinstance(assertions, list) or not assertions:
                    raise ValueError()
                for step in steps:
                    if (not isinstance(step, dict) or step.get("kind") not in {"user", "event"}
                            or any(not isinstance(step.get(key), str) or not step[key].strip()
                                   for key in (("text",) if step["kind"] == "user" else ("name", "details")))):
                        raise ValueError()
                for assertion in assertions:
                    if (not isinstance(assertion, dict)
                            or any(not isinstance(assertion.get(key), str) or not assertion[key].strip() for key in ("id", "pass"))
                            or not isinstance(assertion.get("evidence"), list)
                            or any(not isinstance(name, str) or not name for name in assertion["evidence"])):
                        raise ValueError()
                if not isinstance(manual, list) or not manual or any(not isinstance(item, str) or not item.strip() for item in manual):
                    raise ValueError()
                ids.add(case["id"])
            return doc
        except (KeyError, TypeError, ValueError, UnicodeError):
            _invalid("评测题集损坏或不符合定义。")

    def _report(self, run_id, dataset):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise EvalStoreError(404, "eval_run_not_found", "评测运行不存在。")
        raw = self._bytes(self.root / run_id / "report.json")
        try:
            doc = _json(raw)
            if (not isinstance(doc, dict) or type(doc.get("schema_version")) is not int or doc.get("schema_version") != 1
                    or doc.get("run_id") != run_id or doc.get("dataset_id") != dataset["dataset_id"]
                    or doc.get("case_id") not in {case["id"] for case in dataset["cases"]}
                    or doc.get("execution_status") not in {"completed", "failed"}
                    or doc.get("execution_mode") not in {"live", "controlled"}
                    or doc.get("auto_status") not in STATUSES or doc.get("review", "missing") is not None):
                _invalid()
            for key in ("git_sha", "fixture_version", "started_at", "finished_at"):
                if not isinstance(doc.get(key), str) or not doc[key]:
                    _invalid()
            started, finished = [datetime.fromisoformat(doc[key].replace("Z", "+00:00"))
                                 for key in ("started_at", "finished_at")]
            if started.tzinfo is None or finished.tzinfo is None or finished < started:
                _invalid()
            elapsed = doc.get("elapsed_seconds")
            if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
                _invalid()
            model, usage, error = doc.get("model"), doc.get("usage", "missing"), doc.get("error", "missing")
            if not isinstance(model, dict) or any(not isinstance(model.get(k), str) or not model[k] for k in ("name", "provider")):
                _invalid()
            if usage is not None and (not isinstance(usage, dict) or not {"input_tokens", "output_tokens"}.issubset(usage) or any(
                isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in usage.values()
            )):
                _invalid()
            if error is not None and (not isinstance(error, dict) or any(not isinstance(error.get(k), str) for k in ("kind", "message"))):
                _invalid()
            dimensions, checks, evidence = doc.get("dimensions"), doc.get("checks"), doc.get("evidence_files")
            if not isinstance(dimensions, dict) or set(dimensions) != DIMENSIONS or any(v not in STATUSES for v in dimensions.values()):
                _invalid()
            if not isinstance(evidence, list) or any(not isinstance(item, dict) or not _evidence_name(item.get("name"))
                    or not isinstance(item.get("label"), str) for item in evidence):
                _invalid("报告包含不允许公开的证据路径。")
            names = {item["name"] for item in evidence}
            if len(names) != len(evidence) or not isinstance(checks, list) or not checks:
                _invalid()
            check_ids = set()
            for check in checks:
                if (not isinstance(check, dict) or not isinstance(check.get("id"), str) or not check["id"]
                        or check["id"] in check_ids or check.get("status") not in STATUSES
                        or any(not isinstance(check.get(k), str) for k in ("label", "detail"))
                        or not isinstance(check.get("evidence"), list)
                        or any(not isinstance(name, str) or name not in names for name in check["evidence"])):
                    _invalid()
                check_ids.add(check["id"])
            if (not isinstance(doc.get("manual_checks"), list)
                    or any(not isinstance(item, str) for item in doc["manual_checks"])):
                _invalid()
            if doc["auto_status"] == "passed" and (doc["execution_status"] != "completed"
                    or any(check["status"] != "passed" for check in checks)
                    or any(value != "passed" for value in dimensions.values())):
                _invalid("自动通过状态与保存的检查结果矛盾。")
            return doc, raw
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            _invalid()

    def _review(self, run_id, report_raw):
        path = self.root / run_id / "review.json"
        try:
            safe_path(self.root, path)
        except ValueError:
            raise EvalStoreError(404, "eval_evidence_unavailable", "复核文件不可安全读取。") from None
        if not path.exists():
            return None, b""
        raw = self._bytes(path)
        try:
            doc = _json(raw)
            if (not isinstance(doc, dict) or type(doc.get("schema_version")) is not int or doc.get("schema_version") != 1 or doc.get("run_id") != run_id
                    or doc.get("decision") not in {"passed", "failed"} or not isinstance(doc.get("note"), str)
                    or not doc["note"].strip() or len(doc["note"]) > 4000 or not isinstance(doc.get("reviewed_at"), str)
                    or datetime.fromisoformat(doc["reviewed_at"].replace("Z", "+00:00")).tzinfo is None):
                _invalid("人工复核文件损坏。")
            if doc.get("report_sha256") != hashlib.sha256(report_raw).hexdigest():
                raise EvalStoreError(409, "eval_review_stale", "原报告已变化，保存的复核不再适用。")
            return doc, raw
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            _invalid("人工复核文件损坏。")

    def _detail(self, run_id, dataset):
        report, raw = self._report(run_id, dataset)
        review, review_raw = self._review(run_id, raw)
        if review and review["decision"] == "passed" and report["auto_status"] != "passed":
            _invalid("人工通过与自动检查结果矛盾。")
        status = "failed" if report["execution_status"] == "failed" or report["auto_status"] == "failed" else "needs_review"
        if review:
            status = review["decision"]
        # The CAS token describes the complete reviewed view, not just report bytes.
        digest = hashlib.sha256(raw + b"\0" + review_raw).hexdigest()
        return {**report, "status": status, "review": review, "report_digest": digest}

    def detail(self, run_id):
        with self.lock:
            return clean(self._detail(run_id, self._dataset()))

    def _runs(self, dataset, case_id=None):
        if case_id is not None and case_id not in {case["id"] for case in dataset["cases"]}:
            raise EvalStoreError(404, "eval_case_not_found", "评测题目不存在。")
        items, errors = [], []
        try:
            safe_path(self.root, self.root)
            paths = sorted(self.root.iterdir()) if self.root.exists() else []
        except (OSError, ValueError):
            raise EvalStoreError(404, "eval_evidence_unavailable", "评测目录不可安全读取。") from None
        for path in paths:
            if not RUN_ID.fullmatch(path.name):
                continue
            try:
                safe_path(self.root, path)
                safe_path(self.root, path / "report.json")
                if not path.is_dir() or not (path / "report.json").exists():
                    continue
                detail = self._detail(path.name, dataset)
                if case_id is None or detail["case_id"] == case_id:
                    keys = ("run_id", "case_id", "dataset_id", "started_at", "finished_at", "execution_status",
                            "execution_mode", "model", "usage", "elapsed_seconds", "auto_status", "status", "review")
                    items.append({key: detail[key] for key in keys})
            except (EvalStoreError, ValueError) as error:
                errors.append({"run_id": path.name, "code": getattr(error, "code", "eval_evidence_unavailable"),
                               "message": str(error) if isinstance(error, EvalStoreError) else "评测目录不可安全读取。"})
        return {"items": sorted(items, key=lambda item: (item["started_at"], item["run_id"]), reverse=True), "errors": errors}

    def runs(self, case_id=None):
        with self.lock:
            return clean(self._runs(self._dataset(), case_id))

    def overview(self):
        with self.lock:
            dataset = self._dataset()
            runs = self._runs(dataset)
            cases = []
            for case in dataset["cases"]:
                history = [run for run in runs["items"] if run["case_id"] == case["id"]]
                latest = history[0] if history else None
                cases.append({**{key: case[key] for key in ("id", "title", "category", "split")},
                              "definition": {key: case[key] for key in ("steps", "assertions", "manual_checks")},
                              "status": latest["status"] if latest else "not_run",
                              "latest_run_id": latest["run_id"] if latest else None, "run_count": len(history)})
            return clean({"dataset_id": dataset["dataset_id"], "cases": cases, "errors": runs["errors"]})

    def evidence(self, run_id, name):
        with self.lock:
            report, _ = self._report(run_id, self._dataset())
            if not _evidence_name(name) or name not in {item["name"] for item in report["evidence_files"]}:
                raise EvalStoreError(404, "eval_evidence_unavailable", "证据未在报告白名单内。")
            raw = self._bytes(self.root / run_id / name, maximum=MAX_EVIDENCE_BYTES)
            try:
                if name.endswith(".json"):
                    content, kind = _json(raw), "json"
                elif name.endswith(".jsonl"):
                    lines = raw.decode("utf-8").splitlines()
                    content = "\n".join(json.dumps(clean(_json(line.encode("utf-8"))), ensure_ascii=False)
                                        for line in lines if line.strip())
                    kind = "text"
                else:
                    content, kind = raw.decode("utf-8"), "text"
            except (ValueError, UnicodeError, RecursionError):
                raise EvalStoreError(422, "eval_evidence_invalid", "证据不是有效的 UTF-8 文本或 JSON。") from None
            return {"name": name, "format": kind, "content": clean(content)}

    def review(self, run_id, *, expected_digest, decision, note):
        with self.lock:
            dataset = self._dataset()
            detail = self._detail(run_id, dataset)
            if detail["report_digest"] != expected_digest:
                raise EvalStoreError(409, "eval_review_conflict", "评测或复核已变化，请重新查看后再提交。")
            if decision not in {"passed", "failed"} or not isinstance(note, str) or not note.strip() or len(note) > 4000:
                raise EvalStoreError(422, "eval_review_invalid", "复核需要结论及 1–4000 字的检查说明。")
            if decision == "passed" and detail["auto_status"] != "passed":
                raise EvalStoreError(409, "eval_review_blocked", "自动检查尚未全部通过，不能标记人工通过。")
            _, report_raw = self._report(run_id, dataset)
            review = {"schema_version": 1, "run_id": run_id, "report_sha256": hashlib.sha256(report_raw).hexdigest(),
                      "decision": decision, "note": note.strip(), "reviewed_at": datetime.now(timezone.utc).isoformat()}
            target = safe_path(self.root, self.root / run_id / "review.json")
            temporary = safe_path(self.root, target.with_name(f".review-{uuid4().hex}.tmp"))
            try:
                with temporary.open("xb") as stream:
                    stream.write(json.dumps(review, ensure_ascii=False, indent=2).encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                safe_path(self.root, target)
                os.replace(temporary, target)
            except (OSError, ValueError):
                raise EvalStoreError(503, "eval_review_unavailable", "人工复核未能安全保存。") from None
            finally:
                temporary.unlink(missing_ok=True)
            return clean(self._detail(run_id, dataset))
