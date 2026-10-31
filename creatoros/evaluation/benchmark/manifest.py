"""Exclusive benchmark freeze and paid-run source verification; never calls a model."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TOKEN = re.compile(rb"(?i)(?<![a-z0-9])(?:sk-[a-z0-9_-]{20,}|gh[pousr]_[a-z0-9]{25,}|github_pat_[a-z0-9_]{25,}|bearer\s+[a-z0-9._~+/=-]{15,})")
_CODE = {".py", ".js", ".mjs", ".ts", ".tsx", ".css", ".html"}
_STATIC = _CODE | {".json", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".map"}
_PRIVATE = {".env", ".env.local", ".env.production", "credentials.json", "secrets.json"}


class ManifestVerificationError(ValueError):
    """A frozen input changed or cannot be verified; paid execution must stop."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative(path: Path) -> str:
    path = path.resolve()
    try:
        name = path.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("Frozen inputs must stay inside the repository") from error
    if any(part in {".git", "__pycache__", "node_modules", ".venv"} for part in Path(name).parts):
        raise ValueError(f"Dependency/cache path is not a frozen input: {name}")
    if path.name.lower() in _PRIVATE or path.name.lower().startswith(".env"):
        raise ValueError(f"Secret file is not a frozen input: {name}")
    return name


def _dataset(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    cases = doc.get("cases")
    if not isinstance(cases, list) or [c.get("id") for c in cases] != [f"B{n:02}" for n in range(1, 51)]:
        raise ValueError("Freeze requires all 50 ordered cases B01..B50")
    families: dict[str, str] = {}
    for case in cases:
        family, split = case.get("family"), case.get("split")
        if not family or split not in {"dev", "acceptance"}:
            raise ValueError("Every case requires a family and dev/acceptance split")
        if family in families and families[family] != split:
            raise ValueError(f"Case family crosses splits: {family}")
        families[family] = split
        if case.get("origin", {}).get("type") not in {"trace", "adversarial_variant"}:
            raise ValueError("Every case must preserve trace/adversarial_variant provenance")
    if sum(c["split"] == "acceptance" for c in cases) != 10:
        raise ValueError("Freeze requires 40 dev and 10 acceptance cases")
    return doc


def _default_sources() -> list[Path]:
    root = PROJECT_ROOT
    sources = set((root / "creatoros").rglob("*.py"))
    sources.update((root / "creatoros/skills").rglob("*.md"))
    sources.update(p for p in (root / "web/dist").rglob("*") if p.is_file())
    required = root / "web/e2e/live-benchmark-v2.mjs"
    if not required.is_file():
        raise ValueError("The actual benchmark browser runner is missing")
    sources.add(required)
    for folder in ("tests", "scripts"):
        sources.update((root / folder).glob("*benchmark*.py"))
    sources.update((root / "creatoros").rglob("SPEC.md"))
    sources.update(root / name for name in ("AGENTS.md", "SPEC.md", "web/SPEC.md", "web/FRONTEND_WORKFLOW.md",
        "docs/agent-eval/SPEC.md", "docs/agent-eval/benchmark-v2/SPEC.md",
        "docs/agent-eval/benchmark-v2/METHODOLOGY.md", "docs/agent-eval/benchmark-v2/TRACE_COVERAGE.md") if (root / name).is_file())
    return sorted(p for p in sources if "__pycache__" not in p.parts)


def _snapshot_allowed(name: str) -> bool:
    path = Path(name)
    if any(x in {"data", "tmp", "outputs", "output", "sessions", "production-skills"} for x in path.parts):
        return False
    return (path.suffix in _CODE or (name.startswith("web/dist/") and path.suffix in _STATIC)
            or (name.startswith("creatoros/skills/") and path.suffix == ".md"))


def _git_state() -> dict:
    def git(*args):
        result = subprocess.run(["git", "-C", str(PROJECT_ROOT), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        return result.stdout.rstrip() if result.returncode == 0 else None
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    return {"sha": git("rev-parse", "HEAD"), "dirty": bool(status) if status is not None else None,
            "dirty_paths": status.splitlines() if status else [], "status_available": status is not None}


def _configuration(pricing: dict, configuration: dict) -> None:
    if not isinstance(pricing, dict) or not pricing:
        raise ValueError("Pricing must be supplied explicitly")
    if not isinstance(configuration, dict) or not configuration:
        raise ValueError("Model configuration must be supplied explicitly")
    found_retry = False
    def visit(value):
        nonlocal found_retry
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {"retries", "max_retries", "retry_count", "automatic_retries"}:
                    found_retry = True
                    if child != 0 or isinstance(child, bool):
                        raise ValueError("Baseline model retries must explicitly be zero")
                if str(key).lower() in {"api_key", "authorization", "cookie", "password", "access_token", "refresh_token"}:
                    raise ValueError("Do not freeze credentials in configuration")
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and _TOKEN.search(value.encode()):
            raise ValueError("Credential-like value in frozen configuration")
    visit(configuration)
    visit(pricing)
    if not found_retry:
        raise ValueError("Model configuration must explicitly record retries=0")


def freeze_manifest(path, dataset_path, *, pricing, model_configuration, case_ids=None, source_paths=None) -> dict:
    """Freeze all 50 definitions and selected execution IDs, once, with a source ZIP.

    ``source_paths`` is an explicit small input set for component tests. Production
    callers omit it to lock the complete runtime, served frontend and harness.
    """
    target, dataset = Path(path).resolve(), Path(dataset_path).resolve()
    if target.exists():
        raise FileExistsError(target)
    _configuration(pricing, model_configuration)
    doc = _dataset(dataset)
    dataset_name = _relative(dataset)
    all_ids = [case["id"] for case in doc["cases"]]
    selected = list(all_ids if case_ids is None else case_ids)
    if not selected or len(set(selected)) != len(selected) or any(x not in all_ids for x in selected):
        raise ValueError("Selected case IDs must be a nonempty unique subset of the frozen 50")
    paths = _default_sources() if source_paths is None else [Path(p) if Path(p).is_absolute() else PROJECT_ROOT / p for p in source_paths]
    names = {_relative(p): p.resolve() for p in paths}
    names[dataset_name] = dataset
    payloads, source_hashes = {}, {}
    for name, source in sorted(names.items()):
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Frozen source is missing or linked: {name}")
        data = source.read_bytes()
        if _TOKEN.search(data):
            raise ValueError(f"Credential-like bytes in frozen input: {name}")
        source_hashes[name] = {"sha256": _digest(data), "size_bytes": len(data)}
        if _snapshot_allowed(name):
            payloads[name] = data
        elif not (name == dataset_name or (source.suffix == ".md" and source.name in {"SPEC.md", "AGENTS.md", "FRONTEND_WORKFLOW.md", "METHODOLOGY.md", "TRACE_COVERAGE.md"})):
            raise ValueError(f"Only code, static frontend, specs and dataset inputs are allowed: {name}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    zipped = buffer.getvalue()
    versions = {}
    for package in ("openai-codex", "openai", "fastapi", "sqlalchemy"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    inputs = []
    for name in ("agent-census/summary.json", "agent-census/inventory.json", "agent-census/eval-inventory.json", "codex-census/summary.json", "codex-census/ledger-summary.json", "codex-census/files.jsonl", "codex-census/ledger-files.jsonl"):
        index = PROJECT_ROOT / "tmp/benchmark-20261031" / name
        if index.is_file():
            data = index.read_bytes()
            inputs.append({"path": _relative(index), "sha256": _digest(data), "size_bytes": len(data), "role": "historical_source_index_not_runtime_gate"})
    result = {"schema_version": 1, "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_path": dataset_name, "dataset_id": doc.get("dataset_id"), "all_case_ids": all_ids, "selected_case_ids": selected,
        "case_groups": [{"id": c["id"], "family": c["family"], "split": c["split"], "origin_type": c["origin"]["type"]} for c in doc["cases"]],
        "git": _git_state(), "python_version": sys.version, "package_versions": versions,
        "pricing": pricing, "model_configuration": model_configuration, "execution_policy": {"retries": 0, "first_attempts_only": True},
        "source_selection": "default" if source_paths is None else "explicit_test_inputs", "source_hashes": source_hashes,
        "dataset_snapshot": {"path": "frozen-cases.json", "sha256": source_hashes[dataset_name]["sha256"]},
        "source_snapshot": {"path": "source-snapshot.zip", "sha256": _digest(zipped), "size_bytes": len(zipped), "members": sorted(payloads)},
        "inputs": inputs, "human_verified": False}
    encoded = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    snapshot = target.parent / "source-snapshot.zip"
    receipt = target.with_name(target.name + ".sha256")
    dataset_snapshot = target.parent / "frozen-cases.json"
    if snapshot.exists() or receipt.exists() or dataset_snapshot.exists():
        raise FileExistsError("Freeze directory already contains a source snapshot or receipt")
    # Both writes are exclusive. An interrupted/orphaned freeze fails closed.
    with snapshot.open("xb") as output:
        output.write(zipped)
    with dataset_snapshot.open("xb") as output:
        output.write(dataset.read_bytes())
    with target.open("xb") as output:
        output.write(encoded)
    with receipt.open("x", encoding="ascii") as output:
        output.write(_digest(encoded) + "\n")
    return result


def verify_manifest(path) -> dict:
    """Reject changes before paid work. No scan of historical media or indexes."""
    target = Path(path).resolve()
    try:
        encoded = target.read_bytes()
        receipt = target.with_name(target.name + ".sha256").read_text(encoding="ascii").strip()
        if _digest(encoded) != receipt:
            raise ValueError("Frozen manifest bytes changed")
        manifest = json.loads(encoded.decode("utf-8"))
        if manifest.get("schema_version") != 1:
            raise ValueError("Unsupported manifest schema")
        if "dataset_snapshot" in manifest:
            frozen = manifest["dataset_snapshot"]
            if (frozen["path"] != "frozen-cases.json" or
                    _digest((target.parent / frozen["path"]).read_bytes()) != frozen["sha256"]):
                raise ValueError("Frozen dataset copy changed")
        _configuration(manifest["pricing"], manifest["model_configuration"])
        hashes = manifest["source_hashes"]
        if not isinstance(hashes, dict) or not hashes:
            raise ValueError("No frozen source hashes")
        changed = []
        for name, recorded in hashes.items():
            source = PROJECT_ROOT / name
            if _relative(source) != name or not source.is_file() or source.is_symlink():
                changed.append(name)
                continue
            if _digest(source.read_bytes()) != recorded["sha256"]:
                changed.append(name)
        if manifest["source_selection"] == "default":
            current_names = {_relative(p) for p in _default_sources()} | {manifest["dataset_path"]}
            changed.extend(sorted(current_names.symmetric_difference(hashes)))
        if changed:
            raise ValueError("Frozen source changed: " + ", ".join(sorted(set(changed))))
        dataset = _dataset(PROJECT_ROOT / manifest["dataset_path"])
        expected_groups = [{"id": c["id"], "family": c["family"], "split": c["split"], "origin_type": c["origin"]["type"]} for c in dataset["cases"]]
        if manifest["case_groups"] != expected_groups or manifest["all_case_ids"] != [f"B{n:02}" for n in range(1, 51)]:
            raise ValueError("Frozen case groups changed")
        info = manifest["source_snapshot"]
        if info["path"] != "source-snapshot.zip":
            raise ValueError("Invalid snapshot path")
        data = (target.parent / info["path"]).read_bytes()
        if _digest(data) != info["sha256"]:
            raise ValueError("Frozen source snapshot changed")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.namelist() != info["members"]:
                raise ValueError("Frozen snapshot member set changed")
            for name in archive.namelist():
                if name not in hashes or not _snapshot_allowed(name) or _digest(archive.read(name)) != hashes[name]["sha256"]:
                    raise ValueError("Invalid frozen snapshot member")
        return {"valid": True, "manifest": manifest, "verified_source_files": len(hashes)}
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as error:
        raise ManifestVerificationError(str(error)) from error
