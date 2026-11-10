"""Read-only versioned regression views over frozen, independently identified cases.

The catalog defines membership and conservative resource classification. It
does not start evaluation, establish a sandbox, or turn a new fixture into an
executable benchmark. A new adapter must be reviewed in code before it can be
marked enabled.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from creatoros.evaluation.benchmark.manifest import _dataset, _reject_links
from creatoros.evaluation.benchmark.natural_suite import canonical_digest
from creatoros.evaluation.benchmark.suite_schema import CASE_ID


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "docs/agent-eval/regression-catalog-v9/catalog.json"
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_CLASS = {"routine", "heavy", "unknown"}
# These are hashes of the unchanged published v1/v2 catalogs, not catalog IDs.
# Their original adapter authorization is itself frozen independently of the
# mutable tool-calling dataset and the current dispatch policy.
_PUBLISHED_ADAPTER_SOURCE = {
    "51e070beff5e9297a3f53e24dce7fa4afe3cdfafda32a965fbf1b62e0352f54c": "72f4ac13ac545ff1d681e143819d521fcf035db304129b0890c1bb01bb588c9e",
    "ee4d021c69e95a8f74fbac474303c9adabf0ff12d4cae80ad4488c6dce0c37c3": "72f4ac13ac545ff1d681e143819d521fcf035db304129b0890c1bb01bb588c9e",
}
# Execution authorization is released with the guard, independently of any
# catalog that asks to enable it. A catalog cannot select a different source
# merely by updating its own digest; changing the baseline requires a newly
# reviewed guard/authorization version.
_ADAPTER_SOURCE_BY_VERSION = {
    "local-dispatch-v1": "72f4ac13ac545ff1d681e143819d521fcf035db304129b0890c1bb01bb588c9e",
    "local-dispatch-v2": "72f4ac13ac545ff1d681e143819d521fcf035db304129b0890c1bb01bb588c9e",
    "local-dispatch-v3": "2f92a6cba88fa9a7342edab629091a8813b34d605efe36c7d04f428db14b04e5",
}
_ADAPTER_SOURCE_BY_NAMESPACE = {"local-dispatch-v4": {
    "base": "2f92a6cba88fa9a7342edab629091a8813b34d605efe36c7d04f428db14b04e5",
    "natural": "84589e028b20b3412e980f947c75934ff9377a756e964e493dd727eb987bfbdb",
}, "local-dispatch-v5": {
    "base": "2f92a6cba88fa9a7342edab629091a8813b34d605efe36c7d04f428db14b04e5",
    "natural": "84589e028b20b3412e980f947c75934ff9377a756e964e493dd727eb987bfbdb",
    "expansion": "830efbbda78bc0f1b9a534f1f8a0d72fc1311d8e58518726de090393bbecb608",
}, "local-dispatch-v6": {
    "e06-scope": "d23426b33b0d4ee98bb098024db217d9dcd29a672fb86640c21847b89325549c",
}, "local-dispatch-v7": {
    "expansion": "830efbbda78bc0f1b9a534f1f8a0d72fc1311d8e58518726de090393bbecb608",
}, "local-dispatch-v8": {
    "base": "2f92a6cba88fa9a7342edab629091a8813b34d605efe36c7d04f428db14b04e5",
}}


def _canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_source_name(relative):
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("source path must be a repository-relative POSIX path")
    parts = Path(relative).parts
    if (len(parts) < 4 or parts[:2] != ("docs", "agent-eval") or
            any(part in {".", ".."} for part in parts) or not relative.endswith(".json")):
        raise ValueError("source path must be a JSON file under docs/agent-eval")


def _source_path(root, relative):
    _validate_source_name(relative)
    source = _reject_links(root / relative, root)
    if not source.resolve().is_relative_to(root.resolve()):
        raise ValueError("source path escaped repository")
    return source


def _snapshot_path(root, digest):
    if not isinstance(digest, str) or not _SHA.fullmatch(digest):
        raise ValueError("invalid snapshot digest")
    return _source_path(root, f"docs/agent-eval/source-snapshots-v1/{digest}.json")


def _frozen_dataset(path, digest):
    """Read immutable, digest-pinned definitions without today's run validator.

    Published snapshots were validated when sealed. Rechecking them with a
    later grader's version requirements would make historical viewing depend
    on current execution code. Execution has its own runtime validation.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(doc, dict) or not isinstance(doc.get("cases"), list)
            or canonical_digest(doc) != digest):
        raise ValueError("source snapshot digest differs")
    return doc


def _adapter_cases(root, document, adapter, namespace="base", *, allow_unfrozen=False):
    """Local-dispatch policy is executable only for identical frozen base cases."""
    from creatoros.evaluation.benchmark.local_dispatch_guard import CASES_BY_VERSION
    if adapter in {"local-dispatch-v4", "local-dispatch-v5", "local-dispatch-v6", "local-dispatch-v7", "local-dispatch-v8"}:
        from creatoros.evaluation.benchmark.local_dispatch_guard import CASES_BY_NAMESPACE
        sources = _ADAPTER_SOURCE_BY_NAMESPACE[adapter]
        if namespace not in sources:
            raise ValueError("adapter namespace is not authorized")
        declared = document.get("adapter_source_sha256_by_namespace")
        if not isinstance(declared, dict) or any(declared.get(name) != digest for name, digest in sources.items()):
            raise ValueError(f"{adapter} adapter requires its exact fixed source digests")
        matching = [source for source in document["sources"] if source.get("namespace") == namespace]
        snapshot = sources[namespace]
        if len(matching) != 1 or matching[0].get("canonical_sha256") != snapshot:
            raise ValueError(f"{adapter} adapter requires its exact fixed namespace source")
        allowed = CASES_BY_NAMESPACE[adapter][namespace]
    else:
        if namespace != "base":
            raise ValueError("adapter namespace is not authorized")
        allowed = CASES_BY_VERSION[adapter]
        snapshot = _ADAPTER_SOURCE_BY_VERSION[adapter]
    if adapter == "local-dispatch-v3":
        base = [source for source in document["sources"] if source.get("namespace") == "base"]
        if (document.get("adapter_source_sha256") != snapshot or len(base) != 1
                or base[0].get("canonical_sha256") != snapshot):
            raise ValueError("v3 adapter requires its exact frozen base source digest")
    target = _snapshot_path(root, snapshot)
    if not target.exists() and allow_unfrozen:
        if adapter == "local-dispatch-v3":
            target = _source_path(root, base[0]["path"])
        elif adapter in {"local-dispatch-v4", "local-dispatch-v5", "local-dispatch-v6", "local-dispatch-v7", "local-dispatch-v8"}:
            target = _source_path(root, matching[0]["path"])
    doc = (_frozen_dataset(target, snapshot) if target == _snapshot_path(root, snapshot)
           else _dataset(target))
    if canonical_digest(doc) != snapshot:
        raise ValueError("adapter snapshot digest differs")
    return {case["id"]: _canonical_sha(case) for case in doc["cases"] if case["id"] in allowed}


def build_catalog(document, *, repo_root=ROOT, allow_unfrozen=False):
    """Validate a catalog document and resolve its versioned suite views.

    `repo_root` enables isolated tests and explicit local extension tooling.
    Source digests pin definitions; results and live-run records are separate.
    """
    root = Path(repo_root).absolute()
    if not isinstance(document, dict) or document.get("schema_version") != "regression-catalog-v1":
        raise ValueError("unsupported catalog schema")
    if not isinstance(document.get("catalog_id"), str) or not _NAME.fullmatch(document["catalog_id"]):
        raise ValueError("invalid catalog id")
    if not isinstance(document.get("version"), str) or not _VERSION.fullmatch(document["version"]):
        raise ValueError("invalid catalog version")
    if "sources_frozen" in document and not isinstance(document["sources_frozen"], bool):
        raise ValueError("sources_frozen must be boolean")
    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("catalog requires sources")

    instances, source_info, family_splits = {}, {}, {}
    dataset_ids, namespaces = set(), set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("invalid source")
        name, dataset_id = source.get("namespace"), source.get("dataset_id")
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in namespaces:
            raise ValueError("duplicate or invalid source namespace")
        if not isinstance(dataset_id, str) or not dataset_id or dataset_id in dataset_ids:
            raise ValueError("duplicate or invalid source dataset id")
        expected = source.get("canonical_sha256")
        if not isinstance(expected, str) or not _SHA.fullmatch(expected):
            raise ValueError("source requires a canonical SHA-256")
        _validate_source_name(source.get("path"))
        frozen = _snapshot_path(root, expected)
        if (frozen.exists() or (not allow_unfrozen and
                (_canonical_sha(document) in _PUBLISHED_ADAPTER_SOURCE or document.get("sources_frozen") is True))):
            path = frozen
        else:
            path = _source_path(root, source["path"])
        try:
            doc = _frozen_dataset(path, expected) if path == frozen else _dataset(path)
        except (KeyError, TypeError, IndexError) as error:
            raise ValueError("source dataset is malformed") from error
        if doc.get("dataset_id") != dataset_id or canonical_digest(doc) != expected:
            raise ValueError("source identity or frozen digest differs")
        selection = source.get("selection")
        if selection == "all":
            if "natural_selection_policy" in doc:
                raise ValueError("natural placeholders cannot be selected")
            selected = [case["id"] for case in doc["cases"]]
        elif selection == "natural_eligible":
            selected = doc.get("natural_selection_policy", {}).get("eligible_case_ids")
        elif selection == "suite_eligible":
            selected = doc.get("suite", {}).get("eligible_case_ids")
        else:
            raise ValueError("unsupported source selection")
        if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
            raise ValueError("source selection must contain unique eligible IDs")
        by_id = {case["id"]: case for case in doc["cases"]}
        if any(identifier not in by_id for identifier in selected):
            raise ValueError("selected case not present in source")
        excluded = source.get("exclude_case_ids", [])
        if (not isinstance(excluded, list) or any(not isinstance(identifier, str) for identifier in excluded)
                or len(excluded) != len(set(excluded)) or any(identifier not in selected for identifier in excluded)):
            raise ValueError("excluded cases must be unique eligible IDs")
        selected = [identifier for identifier in selected if identifier not in excluded]
        if not selected:
            raise ValueError("source selection cannot be empty")
        namespaces.add(name)
        dataset_ids.add(dataset_id)
        source_info[name] = {"source": source, "document": doc, "cases": by_id, "selected": set(selected)}
        for case_id in selected:
            if not CASE_ID.fullmatch(case_id):
                raise ValueError("invalid case identity")
            case = by_id[case_id]
            family, split = case.get("family"), case.get("split")
            if (not isinstance(family, str) or not family.strip() or split not in {"dev", "acceptance"}
                    or not isinstance(case.get("title"), str) or not case["title"].strip()):
                raise ValueError("case requires family and split")
            if family in family_splits and family_splits[family] != split:
                raise ValueError("case family crosses development and acceptance")
            family_splits[family] = split
            key = f"{name}:{case_id}"
            if key in instances:
                raise ValueError("duplicate case instance")
            instances[key] = {"instance_id": key, "dataset_id": dataset_id, "case_id": case_id,
                "title": case.get("title") or case_id, "family": family, "split": split, "scenario": case.get("scenario"),
                "parent_instance_id": None, "source_path": source["path"],
                "source_sha256": expected, "case_sha256": _canonical_sha(case),
                "live_codex_declared": case.get("live_codex"),
                "dependencies": ["DeepSeek 模型", "隔离测试世界与浏览器", "本地 ffmpeg / ffprobe 测试素材初始化"],
                "side_effects": ["模型调用可能产生费用", "尚须逐题确认其他外部副作用"],
                "resource_class": "unknown", "execution_readiness": "requires_review",
                "adapter": None, "reason": "尚未核实真实调用链、执行资格及外部副作用。",
                "classification_evidence": [source["path"]]}

    for name, info in source_info.items():
        parent = info["source"].get("parent_namespace")
        if parent is not None and (not isinstance(parent, str) or not _NAME.fullmatch(parent)):
            raise ValueError("invalid source parent")
        seen = {name}
        cursor = parent
        while cursor is not None:
            if cursor in seen or cursor not in source_info:
                raise ValueError("unknown or cyclic source parent")
            seen.add(cursor)
            cursor = source_info[cursor]["source"].get("parent_namespace")
        if parent is None:
            if info["source"]["selection"] == "natural_eligible":
                raise ValueError("natural variants require an explicit parent source")
            continue
        if info["source"]["selection"] == "natural_eligible":
            policy = info["document"]["natural_selection_policy"]
            parent_source = source_info[parent]["source"]
            if (policy.get("parent_dataset_id") != parent_source["dataset_id"]
                    or policy.get("parent_canonical_sha256") != parent_source["canonical_sha256"]):
                raise ValueError("natural source parent identity or digest differs")
        for case_id in info["selected"]:
            child_key, parent_key = f"{name}:{case_id}", f"{parent}:{case_id}"
            if parent_key not in instances:
                raise ValueError("case parent is absent from catalog")
            child, original = instances[child_key], instances[parent_key]
            if child["family"] != original["family"] or child["split"] != original["split"]:
                raise ValueError("variant differs from parent family or split")
            child["parent_instance_id"] = parent_key

    classifications = document.get("classifications", {})
    if not isinstance(classifications, dict):
        raise ValueError("invalid classifications")
    classifications = deepcopy(classifications)
    groups = document.get("classification_groups", [])
    if not isinstance(groups, list):
        raise ValueError("invalid classification groups")
    group_ids = set()
    for group in groups:
        if not isinstance(group, dict) or set(group) - {"id", "resource_class", "reason", "evidence_paths", "instances", "dependencies", "side_effects"}:
            raise ValueError("invalid classification group")
        group_id, members = group.get("id"), group.get("instances")
        if not isinstance(group_id, str) or not _NAME.fullmatch(group_id) or group_id in group_ids:
            raise ValueError("duplicate or invalid classification group")
        group_ids.add(group_id)
        if not isinstance(members, list) or not members or any(not isinstance(x, str) for x in members) or len(members) != len(set(members)):
            raise ValueError("invalid classification members")
        paths = group.get("evidence_paths")
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or "\\" in p or Path(p).is_absolute()
            or any(part in {".", ".."} for part in Path(p).parts) for p in paths):
            raise ValueError("classification requires safe repository evidence paths")
        for key in ("dependencies", "side_effects"):
            if key in group and (not isinstance(group[key], list) or any(not isinstance(x, str) or not x for x in group[key])):
                raise ValueError("invalid classification notes")
        for identity in members:
            if identity in classifications:
                raise ValueError("duplicate classification")
            classifications[identity] = {key: deepcopy(value) for key, value in group.items() if key not in {"id", "instances"}}
    approved_adapter_cases = {}
    for identity, classification in classifications.items():
        if identity not in instances or not isinstance(classification, dict):
            raise ValueError("classification references unknown instance")
        if set(classification) - {"resource_class", "reason", "adapter", "evidence_paths", "dependencies", "side_effects"}:
            raise ValueError("unsupported classification field")
        resource = classification.get("resource_class")
        reason = classification.get("reason")
        if resource not in _CLASS or not isinstance(reason, str) or not reason.strip():
            raise ValueError("classification requires resource and evidence reason")
        item = instances[identity]
        item.update(resource_class=resource, reason=f"{reason}（{item['case_id']}：{item['title']}；场景 {item['scenario']}）")
        item["classification_evidence"].extend(classification.get("evidence_paths", []))
        item["dependencies"].extend(classification.get("dependencies", []))
        item["side_effects"].extend(classification.get("side_effects", []))
        if resource == "heavy":
            item["dependencies"].append("重资源：生产、Codex 或真实联网（以题目为准）")
            item["side_effects"].append("可能触发外部模型、素材生产或联网，需逐题核实")
        adapter = classification.get("adapter")
        if adapter is not None:
            namespace = identity.split(":", 1)[0]
            if (adapter not in {"local-dispatch-v1", "local-dispatch-v2", "local-dispatch-v3", "local-dispatch-v4", "local-dispatch-v5", "local-dispatch-v6", "local-dispatch-v7", "local-dispatch-v8"}
                    or resource != "routine" or namespace not in
                    ({"base"} if adapter == "local-dispatch-v8" else {"expansion"} if adapter == "local-dispatch-v7" else {"e06-scope"} if adapter == "local-dispatch-v6" else {"base", "natural", "expansion"} if adapter == "local-dispatch-v5" else {"base", "natural"} if adapter == "local-dispatch-v4" else {"base"})):
                raise ValueError("unrecognized or ineligible execution adapter")
            adapter_key = (adapter, namespace)
            if adapter_key not in approved_adapter_cases:
                approved_adapter_cases[adapter_key] = _adapter_cases(root, document, adapter, namespace, allow_unfrozen=allow_unfrozen)
            if approved_adapter_cases[adapter_key].get(item["case_id"]) != item["case_sha256"]:
                raise ValueError("adapter does not authorize exact case definition")
            item.update(adapter=adapter, execution_readiness="enabled")

    suites = document.get("suites")
    if not isinstance(suites, list) or not suites:
        raise ValueError("catalog requires suites")
    resolved, suite_ids = [], set()
    for suite in suites:
        if not isinstance(suite, dict) or not isinstance(suite.get("id"), str) or not _NAME.fullmatch(suite["id"]) or suite["id"] in suite_ids:
            raise ValueError("duplicate or invalid suite id")
        suite_ids.add(suite["id"])
        if not isinstance(suite.get("version"), str) or not _VERSION.fullmatch(suite["version"]):
            raise ValueError("suite version required")
        if not isinstance(suite.get("title"), str) or not suite["title"].strip():
            raise ValueError("suite title required")
        if not isinstance(suite.get("description", ""), str):
            raise ValueError("suite description must be text")
        if suite.get("include_all") is True:
            if "instances" in suite or "include_resource_classes" in suite:
                raise ValueError("suite cannot combine membership selectors")
            selected = list(instances)
        elif "include_resource_classes" in suite:
            classes = suite["include_resource_classes"]
            if ("instances" in suite or not isinstance(classes, list) or not classes
                    or any(not isinstance(value, str) or value not in _CLASS for value in classes)
                    or len(classes) != len(set(classes))):
                raise ValueError("invalid resource class selector")
            selected = [key for key, item in instances.items() if item["resource_class"] in classes]
        else:
            selected = suite.get("instances")
        if not isinstance(selected, list) or not selected or any(not isinstance(x, str) for x in selected):
            raise ValueError("suite membership must be a nonempty list")
        if len(selected) != len(set(selected)) or any(key not in instances for key in selected):
            raise ValueError("suite has duplicate or unknown instance")
        cases = [deepcopy(instances[key]) for key in selected]
        candidates = ([deepcopy(item) for key, item in instances.items()
                       if key not in selected and item["resource_class"] != "heavy"]
                      if suite.get("show_unclassified") is True else [])
        digest_input = {"id": suite["id"], "version": suite["version"],
                        "cases": [{key: item[key] for key in ("instance_id", "case_sha256", "resource_class", "execution_readiness", "adapter")} for item in cases],
                        "candidates": [{key: item[key] for key in ("instance_id", "case_sha256", "resource_class", "execution_readiness", "adapter")} for item in candidates]}
        resolved.append({"id": suite["id"], "version": suite["version"], "title": suite["title"],
                         "membership_sha256": _canonical_sha(digest_input),
                         "description": suite.get("description", ""), "cases": cases, "candidates": candidates,
                         "scope_status": "partial" if candidates or any(x["execution_readiness"] != "enabled" for x in cases) else "complete",
                         "counts": {"defined": len(cases), "enabled": sum(x["execution_readiness"] == "enabled" for x in cases),
                                    "requires_review": sum(x["execution_readiness"] != "enabled" for x in cases),
                                    "routine": sum(x["resource_class"] == "routine" for x in cases),
                                    "heavy": sum(x["resource_class"] == "heavy" for x in cases),
                                    "unknown_resource": sum(x["resource_class"] == "unknown" for x in cases),
                                    "pending_candidates": len(candidates),
                                    "unclassified_candidates": sum(x["resource_class"] == "unknown" for x in candidates)}})
    return {"schema_version": document["schema_version"], "catalog_id": document["catalog_id"],
            "version": document["version"], "catalog_sha256": _canonical_sha(document),
            "catalog_source": deepcopy(document), "suites": resolved}


def catalog(manifest_path=DEFAULT_MANIFEST, *, repo_root=ROOT):
    path = Path(manifest_path)
    root = Path(repo_root).absolute()
    if not path.is_absolute():
        path = root / path
    checked = _reject_links(path, root)
    if not checked.resolve().is_relative_to(root.resolve()):
        raise ValueError("catalog path escaped repository")
    return build_catalog(json.loads(checked.read_text(encoding="utf-8-sig")), repo_root=root)


def freeze_sources(document, *, repo_root=ROOT):
    """Copy verified catalog inputs into immutable, content-addressed files.

    Existing files are verified, never overwritten. Publishing a catalog and
    qualifying its execution adapters remain separate explicit actions.
    """
    root = Path(repo_root).absolute()
    build_catalog(document, repo_root=root, allow_unfrozen=True)
    frozen = []
    for source in document["sources"]:
        digest = source["canonical_sha256"]
        target = _snapshot_path(root, digest)
        if target.exists():
            if canonical_digest(_frozen_dataset(target, digest)) != digest:
                raise ValueError("existing source snapshot digest differs")
        else:
            original = _source_path(root, source["path"])
            payload = original.read_bytes()
            if (canonical_digest(json.loads(payload.decode("utf-8"))) != digest
                    or canonical_digest(_dataset(original)) != digest):
                raise ValueError("source identity or frozen digest differs")
            target.parent.mkdir(parents=True, exist_ok=True)
            target = _snapshot_path(root, digest)
            fd, temp_name = tempfile.mkstemp(prefix=".source-", suffix=".tmp", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temp_name, target)
            except FileExistsError:
                if canonical_digest(_frozen_dataset(_snapshot_path(root, digest), digest)) != digest:
                    raise ValueError("existing source snapshot digest differs")
            finally:
                Path(temp_name).unlink(missing_ok=True)
        frozen.append(digest)
    return frozen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--summary", action="store_true", help="Show suite counts and frozen identities only")
    parser.add_argument("--freeze-sources", action="store_true", help="Persist verified catalog inputs by digest")
    args = parser.parse_args()
    if args.freeze_sources:
        path = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
        checked = _reject_links(path, ROOT)
        if not checked.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("catalog path escaped repository")
        document = json.loads(checked.read_text(encoding="utf-8-sig"))
        print(json.dumps({"frozen_sources": freeze_sources(document)}, ensure_ascii=False))
        return
    data = catalog(args.manifest)
    if args.summary:
        data = {key: value for key, value in data.items() if key != "catalog_source"}
        data = {**data, "suites": [{key: suite[key] for key in
                ("id", "version", "title", "membership_sha256", "scope_status", "counts")}
                for suite in data["suites"]]}
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
