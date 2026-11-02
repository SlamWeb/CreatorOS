"""Read-only views of the current 50-case standard and immutable batch evidence."""
from __future__ import annotations

import json
from pathlib import Path
import re

from creatoros.config import PROJECT_ROOT
from creatoros.web.observation import clean, safe_path


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
CASE = re.compile(r"^B(?:0[1-9]|[1-4][0-9]|50)$")
RECORD = re.compile(r"^records/[a-f0-9]{32}/report\.json$")
EVIDENCE_NAMES = {"report.json", "requests.json", "calls.json", "evidence.json", "before.json",
                  "after.json", "background.json", "codex.json", "browser.json", "metrics.json",
                  "answer.txt", "judge.json", "messages.json", "isolation.json", "grade.json",
                  "case.json", "probes.json", "foreign_before.json", "foreign_after.json",
                  "variables.json", "controls.json"}


class BenchmarkStoreError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code, self.code = status_code, code


class BenchmarkStore:
    def __init__(self, root=None, *, cases_path=None):
        self.root = Path(root or PROJECT_ROOT / "data" / "agent-benchmark-v2").absolute()
        self.cases_path = Path(cases_path or PROJECT_ROOT / "docs" / "agent-eval" / "benchmark-v2" / "cases.json").absolute()

    def _read(self, path, *, root=None, text=False):
        try:
            path = safe_path(root or self.root, path)
            with path.open("rb") as stream:
                raw = stream.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise BenchmarkStoreError(413, "benchmark_file_too_large", "评测证据超过读取上限。")
            content = raw.decode("utf-8-sig")
            return content if text else json.loads(content)
        except (OSError, ValueError) as error:
            raise BenchmarkStoreError(404, "benchmark_file_unavailable", "评测文件不存在或无法读取。") from error

    def _batch(self, batch_id):
        if not IDENTIFIER.fullmatch(batch_id) or batch_id in {".", ".."}:
            raise BenchmarkStoreError(404, "benchmark_batch_unavailable", "评测批次不存在。")
        value = self._read(self.root / batch_id / "summary.json")
        if not isinstance(value, dict) or not isinstance(value.get("cases"), list):
            raise BenchmarkStoreError(422, "benchmark_batch_invalid", "评测批次汇总格式错误。")
        return value

    def overview(self):
        dataset = self._read(self.cases_path, root=self.cases_path.parent)
        cases = dataset.get("cases", [])
        if {item.get("id") for item in cases} != {f"B{n:02}" for n in range(1, 51)} or len(cases) != 50:
            raise BenchmarkStoreError(422, "benchmark_dataset_invalid", "当前题集必须完整包含 B01–B50。")
        batches, errors = [], []
        for directory in sorted(self.root.iterdir(), reverse=True) if self.root.is_dir() else []:
            if not directory.is_dir() or not IDENTIFIER.fullmatch(directory.name):
                continue
            try:
                value = self._batch(directory.name)
                batches.append({"id": directory.name, **{key: value.get(key) for key in
                    ("dataset_id", "generated_at_utc", "planned", "with_results", "execution_scope", "overall_status", "scores")}})
            except BenchmarkStoreError as error:
                errors.append({"batch_id": directory.name, "message": str(error)})
        batches.sort(key=lambda item: str(item.get("generated_at_utc") or item["id"]), reverse=True)
        return clean({"dataset_id": dataset.get("dataset_id"), "schema_version": dataset.get("schema_version"),
                      "scoring_protocol": dataset.get("scoring_protocol"), "cases": cases, "batches": batches, "errors": errors})

    def batch(self, batch_id):
        summary = self._batch(batch_id)
        return clean({"batch_id": batch_id, "summary": summary,
                      "score_note": "只展示本批已保存分数；旧协议缺失分数不按现行标准重算。"})

    def detail(self, batch_id, case_id):
        if not CASE.fullmatch(case_id):
            raise BenchmarkStoreError(404, "benchmark_case_unavailable", "评测题目不存在。")
        summary = self._batch(batch_id)
        row = next((item for item in summary.get("cases", []) if item.get("case_id") == case_id), None)
        if row is None:
            raise BenchmarkStoreError(404, "benchmark_case_unavailable", "此批次未计划该题。")
        directory = self.root / batch_id
        frozen, frozen_error = None, None
        try:
            dataset = self._read(directory / "frozen-cases.json")
            frozen = next((item for item in dataset.get("cases", []) if item.get("id") == case_id), None)
        except BenchmarkStoreError as error:
            frozen_error = str(error)
        records, errors = [], []
        for relative in row.get("record_paths", []):
            if not isinstance(relative, str) or not RECORD.fullmatch(relative):
                errors.append("记录路径不合法。")
                continue
            try:
                report = self._read(directory / relative)
                if not isinstance(report, dict) or report.get("case_id") != case_id:
                    errors.append("记录内容与题目不匹配。")
                    continue
                record_id = relative.split("/")[1]
                files = [name for name in sorted(EVIDENCE_NAMES)
                         if (directory / "records" / record_id / name).is_file()]
                records.append({"record_id": record_id, "report": report, "evidence_files": files})
            except BenchmarkStoreError as error:
                errors.append(str(error))
        return clean({"batch_id": batch_id, "case_id": case_id, "row": row, "frozen_definition": frozen,
                      "frozen_error": frozen_error, "records": records, "errors": errors})

    def evidence(self, batch_id, case_id, record_id, name):
        if not re.fullmatch(r"[a-f0-9]{32}", record_id) or name not in EVIDENCE_NAMES:
            raise BenchmarkStoreError(404, "benchmark_evidence_unavailable", "评测证据不存在。")
        detail = self.detail(batch_id, case_id)
        if not any(record["record_id"] == record_id for record in detail["records"]):
            raise BenchmarkStoreError(404, "benchmark_evidence_unavailable", "证据不属于此题。")
        path = self.root / batch_id / "records" / record_id / name
        is_text = name.endswith(".txt")
        return {"name": name, "format": "text" if is_text else "json", "content": clean(self._read(path, text=is_text))}
