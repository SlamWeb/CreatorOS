"""Regression evidence for the revised deterministic benchmark oracles."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from creatoros.evaluation.benchmark.grader import MissingEvidence, _expectation
from creatoros.evaluation.benchmark.skill_evidence import VERSION as EVIDENCE_VERSION


FAMILY = "sample--fedcba9876543210"
SELECTED = "sample--0123456789abcdef"
OTHER = "sample--1111111111111111"
ROOT = "world-producer-skills"


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def removal_evidence(status="deleted"):
    before = [{"id": "target", "title": "target"}, {"id": "other", "title": "keep"}]
    after = deepcopy(before[1:] if status == "deleted" else before)
    return {"variables": {"topic_a": "target"},
            "before": {"database": {"topics": before, "topic_removals": []}},
            "after": {"database": {"topics": after, "topic_removals": [{"topic_id": "target", "status": status}]}}}


def candidate_evidence():
    body = "Use assets/reference-01.png."
    work = f"{ROOT}/working/{SELECTED}"
    original_work = f"{ROOT}/working/{FAMILY}"
    original_version = f"{ROOT}/versions/{FAMILY}"
    files = {f"{original_work}/SKILL.md": sha("original"),
             f"{original_version}/SKILL.md": sha("original"),
             f"{original_work}/assets/reference-01.png": "asset-sha",
             f"{original_version}/assets/reference-01.png": "asset-sha",
             f"{work}/SKILL.md": sha(body), f"{work}/assets/reference-01.png": "asset-sha"}
    before = {key: value for key, value in files.items() if SELECTED not in key}
    return {"variables": {"skill_a": FAMILY}, "before": {"files": before}, "after": {"files": files},
        "background": {"skill_evidence_version": EVIDENCE_VERSION,
            "skill_manifests": {SELECTED: {"SKILL.md": sha(body), "assets/reference-01.png": "asset-sha"}},
            "skills": [{"id": SELECTED, "family_id": FAMILY, "version_status": "candidate", "content": body}]},
        "skill_paths": {FAMILY: original_work, SELECTED: work}, "skill_texts": {SELECTED: body},
        "calls": [{"tool": "update_producer_skill_file", "arguments": {"skill_id": FAMILY}, "is_error": False,
                   "raw_result": json.dumps({"skill_id": SELECTED, "completed": True, "outcome_unknown": False})}]}


class RevisedOracleTests(unittest.TestCase):
    def test_exact_removal_handles_deleted_or_archived_and_rejects_extra_changes(self):
        exp = {"kind": "topic_removal_exact", "id": "{topic_a}"}
        for status in ("deleted", "archived"):
            data = removal_evidence(status)
            self.assertTrue(_expectation(exp, data)[0])
            for mutation in ("other_changed", "other_deleted", "extra_added", "target_wrong"):
                altered = deepcopy(data)
                rows = altered["after"]["database"]["topics"]
                if mutation == "other_changed":
                    next(row for row in rows if row["id"] == "other")["title"] = "changed"
                elif mutation == "other_deleted":
                    rows[:] = [row for row in rows if row["id"] != "other"]
                elif mutation == "extra_added":
                    rows.append({"id": "extra", "title": "unrequested"})
                elif status == "deleted":
                    rows.append({"id": "target", "title": "still exists"})
                else:
                    next(row for row in rows if row["id"] == "target")["title"] = "changed"
                with self.subTest(status=status, mutation=mutation):
                    self.assertFalse(_expectation(exp, altered)[0])
        absent = removal_evidence()
        absent["before"]["database"]["topics"] = [row for row in absent["before"]["database"]["topics"] if row["id"] != "target"]
        with self.assertRaises(MissingEvidence):
            _expectation(exp, absent)

    def test_candidate_body_uses_only_authoritative_working_bytes(self):
        data = candidate_evidence()
        # Old collector returned the same registered ID twice: stale version and working copy.
        data["background"]["skills"].insert(0, {"id": SELECTED, "family_id": FAMILY,
            "version_status": "candidate", "content": "old reference-02.png"})
        exp = {"kind": "candidate_body_excludes", "id": "{skill_a}", "value": "reference-02.png"}
        with self.assertRaises(MissingEvidence):
            _expectation(exp, data)  # Conflicting duplicate identities are not authoritative evidence.
        data["background"]["skills"].pop(0)
        self.assertTrue(_expectation(exp, data)[0])
        data["after"]["files"][f"{ROOT}/working/{SELECTED}/SKILL.md"] = "wrong-sha"
        with self.assertRaises(MissingEvidence):
            _expectation(exp, data)

    def test_assets_cannot_be_combined_across_different_candidate_ids(self):
        data = candidate_evidence()
        # The actual returned candidate lacks reference-01; an unrelated candidate has it.
        data["after"]["files"].pop(f"{ROOT}/working/{SELECTED}/assets/reference-01.png")
        data["background"]["skill_manifests"][SELECTED].pop("assets/reference-01.png")
        data["background"]["skills"].append({"id": OTHER, "family_id": FAMILY, "version_status": "candidate", "content": "other"})
        data["skill_paths"][OTHER] = f"{ROOT}/working/{OTHER}"
        data["after"]["files"].update({f"{ROOT}/working/{OTHER}/SKILL.md": sha("other"),
                                       f"{ROOT}/working/{OTHER}/assets/reference-01.png": "other-asset"})
        exp = {"kind": "candidate_asset_exists", "id": "{skill_a}", "field": "assets/reference-01.png"}
        self.assertFalse(_expectation(exp, data)[0])
        del data["calls"]
        with self.assertRaises(MissingEvidence):
            _expectation(exp, data)

    def test_original_integrity_includes_both_working_and_imported_versions(self):
        exp = {"kind": "skill_original_unchanged", "id": "{skill_a}"}
        data = candidate_evidence()
        self.assertTrue(_expectation(exp, data)[0])
        for part in ("working", "versions"):
            altered = deepcopy(data)
            altered["after"]["files"][f"{ROOT}/{part}/{FAMILY}/SKILL.md"] = "changed"
            with self.subTest(part=part):
                self.assertFalse(_expectation(exp, altered)[0])
        del data["before"]["files"][f"{ROOT}/versions/{FAMILY}/SKILL.md"]
        with self.assertRaises(MissingEvidence):
            _expectation(exp, data)

    def test_asset_preservation_checks_bytes_and_complete_candidate_inventory(self):
        for exp in ({"kind": "candidate_assets_preserved", "id": "{skill_a}"},
                    {"kind": "candidate_asset_unchanged", "id": "{skill_a}", "field": "assets/reference-01.png"}):
            data = candidate_evidence()
            self.assertTrue(_expectation(exp, data)[0])
            data["after"]["files"][f"{ROOT}/working/{SELECTED}/assets/reference-01.png"] = "changed"
            data["background"]["skill_manifests"][SELECTED]["assets/reference-01.png"] = "changed"
            self.assertFalse(_expectation(exp, data)[0])
        for key in ("skill_manifests", "skill_evidence_version"):
            data = candidate_evidence()
            del data["background"][key]
            with self.subTest(missing=key), self.assertRaises(MissingEvidence):
                _expectation({"kind": "candidate_asset_absent", "id": "{skill_a}", "field": "assets/missing.png"}, data)

    def test_missing_failed_or_ambiguous_edit_receipts_cannot_select_candidate(self):
        exp = {"kind": "candidate_body_contains", "id": "{skill_a}", "value": "reference-01.png"}
        for mutation in ("missing_error_flag", "failed", "unknown", "malformed", "different_id"):
            data = candidate_evidence()
            if mutation == "missing_error_flag":
                del data["calls"][0]["is_error"]
            elif mutation == "failed":
                data["calls"][0]["is_error"] = True
            elif mutation == "unknown":
                data["calls"][0]["raw_result"] = json.dumps({"skill_id": SELECTED, "outcome_unknown": True})
            elif mutation == "malformed":
                data["calls"][0]["raw_result"] = "{"
            else:
                extra = deepcopy(data["calls"][0])
                extra["raw_result"] = json.dumps({"skill_id": OTHER})
                data["calls"].append(extra)
            with self.subTest(mutation=mutation), self.assertRaises(MissingEvidence):
                _expectation(exp, data)
        data = candidate_evidence()
        data["calls"].insert(0, {"tool": "update_producer_skill_file", "arguments": {"skill_id": FAMILY},
                                 "is_error": True, "raw_result": "[tool_error type=skill_digest_conflict] stale edit"})
        self.assertTrue(_expectation(exp, data)[0])

    def test_inventory_reads_each_registered_working_copy_once_without_writes(self):
        from creatoros.evaluation.benchmark.skill_evidence import collect_skill_evidence

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = root / ROOT / "registry"
            working = root / ROOT / "working" / SELECTED
            versions = root / ROOT / "versions" / SELECTED
            stray = root / ROOT / "trash" / SELECTED
            for directory in (registry, working, versions, stray):
                directory.mkdir(parents=True)
            (registry / f"{SELECTED}.json").write_text(json.dumps({"id": SELECTED, "family_id": FAMILY,
                "version_status": "candidate"}), encoding="utf-8")
            (working / "SKILL.md").write_bytes(b"current\r\n")
            (versions / "SKILL.md").write_bytes(b"old\n")
            (stray / "SKILL.md").write_bytes(b"not authoritative\n")
            files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in root.rglob("*") if path.is_file()}
            inventory = collect_skill_evidence(root, root / ROOT, files)
            self.assertEqual(len(inventory["skills"]), 1)
            self.assertEqual(inventory["skills"][0]["content"], "current\r\n")
            self.assertEqual(inventory["skill_paths"][SELECTED], f"{ROOT}/working/{SELECTED}")
            self.assertEqual(inventory["skill_texts"][SELECTED], "current\r\n")
            self.assertEqual(inventory["skill_inventory_errors"], [])
            self.assertEqual(inventory["skill_manifests"][SELECTED], {"SKILL.md": sha("current\r\n")})
            self.assertEqual(files, {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                     for path in root.rglob("*") if path.is_file()})

    def test_inventory_does_not_recreate_missing_working_copy(self):
        from creatoros.evaluation.benchmark.skill_evidence import collect_skill_evidence

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = root / ROOT / "registry"
            original = root / ROOT / "versions" / SELECTED
            registry.mkdir(parents=True)
            original.mkdir(parents=True)
            (registry / f"{SELECTED}.json").write_text(json.dumps({"id": SELECTED, "family_id": FAMILY,
                "version_status": "candidate"}), encoding="utf-8")
            (original / "SKILL.md").write_text("original", encoding="utf-8")
            files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in root.rglob("*") if path.is_file()}
            inventory = collect_skill_evidence(root, root / ROOT, files)
            self.assertTrue(inventory["skill_inventory_errors"])
            self.assertFalse((root / ROOT / "working" / SELECTED).exists())

    def test_incomplete_asset_snapshot_is_not_a_complete_inventory(self):
        from creatoros.evaluation.benchmark.skill_evidence import collect_skill_evidence
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = root / ROOT / "registry"
            work = root / ROOT / "working" / SELECTED
            registry.mkdir(parents=True)
            (work / "assets").mkdir(parents=True)
            (registry / f"{SELECTED}.json").write_text(json.dumps({"id": SELECTED, "version_status": "candidate"}), encoding="utf-8")
            (work / "SKILL.md").write_text("body", encoding="utf-8")
            (work / "assets" / "untracked.png").write_bytes(b"asset")
            files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in root.rglob("*") if path.is_file() and path.suffix != ".png"}
            inventory = collect_skill_evidence(root, root / ROOT, files)
            self.assertTrue(inventory["skill_inventory_errors"])
            self.assertNotIn(SELECTED, inventory["skill_manifests"])

    def test_world_observation_never_calls_catalog_or_library_recovery(self):
        from creatoros.evaluation.benchmark.world import BenchmarkWorld
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            world = BenchmarkWorld.__new__(BenchmarkWorld)
            world.root = root
            world.variables = {"skill_b": FAMILY, "run_a": "a", "run_b": "b", "creator_a": "bench-a"}
            world.catalog = SimpleNamespace(root=root / ROOT, locate=Mock(side_effect=AssertionError("must not recover")))
            world.extractions = SimpleNamespace(list=lambda: [])
            world.research = SimpleNamespace(list=lambda _: [])
            world.initial_collections = {}
            forbidden = Mock(side_effect=AssertionError("must not describe Skills"))
            world.app = SimpleNamespace(state=SimpleNamespace(skill_library=SimpleNamespace(get_creator=forbidden, get_series=forbidden),
                media_collections=SimpleNamespace(list=lambda _: [])))
            state = {"database": {"series": [], "creator_skill_refs": [], "series_skill_refs": []},
                     "files": {f"{ROOT}/versions/{FAMILY}/SKILL.md": "immutable"}}
            world.snapshot = lambda: state
            self.assertEqual(world.foreign_snapshot(state)["files"], state["files"])
            background = world.background()
            self.assertTrue(background["skill_inventory_errors"])
            self.assertEqual(list(root.iterdir()), [])
            forbidden.assert_not_called()
            world.catalog.locate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
