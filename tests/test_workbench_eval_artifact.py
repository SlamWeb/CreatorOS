"""Pure-data regression checks for the P03 artifact-to-Skill grader."""
from copy import deepcopy
import hashlib
import unittest

from creatoros.evaluation.artifact_chain import EDIT_MARKER, EDIT_NAME, STAGES, chain_checks
from creatoros.evaluation.workbench import grade_slice
from tests.test_workbench_eval import grade_facts


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def artifact_evidence():
    reference = sha("user supplied reference image bytes")
    original_skill = "---\nname: first-draft\ndescription: bilingual scenes\n---\n\nDraft.\n"
    edited_skill = (
        f"---\nname: {EDIT_NAME}\ndescription: bilingual scenes\n---\n\n"
        f"Reusable instructions.\n\n{EDIT_MARKER}\n"
    )
    original_files = {"SKILL.md": sha(original_skill), "assets/reference.png": reference}
    edited_files = {"SKILL.md": sha(edited_skill.replace("\n", "\r\n")), "assets/reference.png": reference}
    original_catalog = [{"id": "existing-skill", "name": "existing"}]
    registered = {
        "id": "adopted-skill",
        "digest": sha("edited version directory"),
        "immutable_digest": sha("edited version directory"),
        "files": edited_files,
        "immutable_files": edited_files,
    }
    before_db = {
        "series": [{"id": "old-series", "creator_id": "creator-a", "name": "Existing",
                    "audience": "Readers", "skill_name": "existing-skill"}],
        "write_receipts": [],
        "topics": [],
        "content_runs": [],
        "content_revisions": [],
        "content_attempts": [],
        "manual_publications": [],
    }
    base_job = {
        "id": "job-p03",
        "mode": "single",
        "status": "ready",
        "revision": 1,
        "digest": sha("original version directory"),
        "thread_id": "extract-thread",
        "uploads": [{"id": reference}],
        "saved_skills": [],
        "trials": [],
        "skills": [{"name": "first-draft"}],
    }

    def stage(name, job, skill_md, files, draft_digest, db=None, catalog=None):
        return {
            "name": name,
            "job": deepcopy(job),
            "draft": {"skill_md": skill_md, "files": deepcopy(files), "digest": draft_digest},
            "catalog": deepcopy(catalog if catalog is not None else original_catalog),
            "state": {"database": deepcopy(db if db is not None else before_db)},
            "reference_copies": {
                "uploaded": reference,
                "job_reference": reference,
            },
        }

    ready = stage("draft_ready", base_job, original_skill, original_files, base_job["digest"])
    edited_job = {**base_job, "revision": 2, "digest": sha("edited version directory"),
                  "skills": [{"name": EDIT_NAME}]}
    edited = stage("draft_edited", edited_job, edited_skill, edited_files, registered["digest"])
    adopted_job = {**edited_job, "status": "saved", "saved_skills": [{"id": registered["id"]}]}
    adopted_catalog = original_catalog + [{"id": registered["id"], "name": EDIT_NAME}]
    adopted = stage("adopted", adopted_job, edited_skill, edited_files, registered["digest"],
                    catalog=adopted_catalog)
    adopted["registered"] = deepcopy(registered)

    series = {
        "id": "series-p03",
        "creator_id": "creator-a",
        "name": "New vocabulary series",
        "audience": "English learners",
        "skill_name": registered["id"],
    }
    production_snapshot = {"skill_name": registered["id"], "skill_digest": registered["digest"]}
    after_db = deepcopy(before_db)
    after_db["series"].append(series)
    after_db["topics"] = [{"id": "topic-p03", "series_id": series["id"], "title": "bring take fetch carry"}]
    after_db["content_runs"] = [{"id": "run-p03", "input_snapshot_json": production_snapshot}]
    after_db["content_revisions"] = [{"id": "revision-p03", "production_input_json": production_snapshot}]
    created_db = deepcopy(before_db)
    created_db["series"].append(series)
    created = stage("series_created", adopted_job, edited_skill, edited_files, registered["digest"],
                    db=created_db, catalog=adopted_catalog)
    created["registered"] = deepcopy(registered)

    extraction_prefix = "sdk-files/extractions/jobs/job-p03/"
    sdk = {"events": [
        {"source": extraction_prefix + "codex_public_events.jsonl", "thread_id": "extract-thread",
         "turn_id": "extract-turn", "method": "turn/completed", "status": "completed"},
        {"source": extraction_prefix + "codex_public_events.jsonl", "thread_id": "extract-thread",
         "turn_id": "extract-turn", "method": "capture/finished", "status": "completed",
         "payload": {"capture_write_failed": False}},
    ], "image_generation_events": []}
    outputs = {
        "thread_id": "production-thread",
        "frozen_skill_resources": {"single": edited_files},
        "skill_digests": {"single": registered["digest"]},
        "images": [{"sha256": sha("new generated image bytes")}],
    }
    oracle = {
        "creator_id": "creator-a",
        "target_series_id": series["id"],
        "series_name": series["name"],
        "audience": series["audience"],
        "single_skill_id": registered["id"],
        "reference_sha256": reference,
    }
    return {
        "artifact_stages": [ready, edited, adopted, created],
        "before_catalog": deepcopy(original_catalog),
        "after_catalog": deepcopy(adopted_catalog),
        "before": {"database": deepcopy(before_db)},
        "after": {"database": deepcopy(after_db)},
        "browser": {"edited_skill_md": edited_skill, "edited_and_restored": True,
                    "created_series_id": series["id"], "reference_sha256": reference,
                    "reference_loaded": True},
        "oracle": oracle,
        "jobs": [{"id": base_job["id"], "status": "saved", "thread_id": "extract-thread"}],
        "sdk": sdk,
        "extraction_prefix": "extractions/jobs/",
        "outputs": outputs,
        "global_skills_guard": {"before": {"codex_skills": {"files": {"x": "1"}}},
                                "after": {"codex_skills": {"files": {"x": "1"}}}},
    }


def p03_gui_write_evidence():
    evidence = grade_facts("P01")
    chain = artifact_evidence()
    evidence.update(deepcopy(chain))
    evidence["browser"].update(grade_facts("P01")["browser"])
    evidence["browser"].update(chain["browser"])

    # Keep the real P01 Agent/SDK trace shape, but bind its production rows to
    # the newly adopted Skill and the series created in the artifact stages.
    production = grade_facts("P01")
    oracle = evidence["oracle"]
    oracle.update(production["oracle"])
    oracle.update(chain["oracle"])
    oracle["source_skill_md"] = {"adopted-skill": chain["artifact_stages"][2]["registered"]["files"]["SKILL.md"]}
    target_series_id = oracle["target_series_id"]
    topic = deepcopy(production["after"]["database"]["topics"][0])
    topic["series_id"] = target_series_id
    run = deepcopy(production["after"]["database"]["content_runs"][0])
    revision = deepcopy(production["after"]["database"]["content_revisions"][0])
    registered = chain["artifact_stages"][2]["registered"]
    frozen_input = {
        "creator_id": oracle["creator_id"], "series_id": target_series_id,
        "topic_id": topic["id"], "topic_title": topic["title"],
        "skill_name": registered["id"], "skill_digest": registered["digest"],
    }
    run["input_snapshot_json"] = deepcopy(frozen_input)
    revision["production_input_json"] = deepcopy(frozen_input)
    after_db = deepcopy(chain["after"]["database"])
    after_db["topics"] = [topic]
    after_db["content_runs"] = [run]
    after_db["content_revisions"] = [revision]
    after_db["content_attempts"] = deepcopy(production["after"]["database"]["content_attempts"])
    evidence["before"] = deepcopy(chain["before"])
    evidence["after"] = {"database": after_db}

    evidence["outputs"].update(production["outputs"])
    evidence["outputs"].update({
        "frozen_skill_files": {"single": oracle["source_skill_md"][registered["id"]]},
        "frozen_skill_resources": {"single": registered["files"]},
        "skill_digests": {"single": registered["digest"]},
    })
    evidence["sdk"] = {
        "events": chain["sdk"]["events"] + production["sdk"]["events"],
        "image_generation_events": production["sdk"]["image_generation_events"],
        "receipts": production["sdk"]["receipts"],
    }

    original_files = {"sources/original-skill/SKILL.md": "source-unchanged"}
    added_files = {
        "outputs/attempt/images/01.png": production["outputs"]["images"][0]["sha256"],
        "extractions/jobs/job-p03/job.json": "job-snapshot",
        "producer-skills/extractions/uploads/reference.png": oracle["reference_sha256"],
        "producer-skills/working/adopted-skill/SKILL.md": oracle["source_skill_md"][registered["id"]],
        "producer-skills/versions/adopted-skill/SKILL.md": oracle["source_skill_md"][registered["id"]],
        "producer-skills/registry/adopted-skill.json": "registry-record",
    }
    evidence["before"]["files"] = deepcopy(original_files)
    evidence["after"]["files"] = {**original_files, **added_files}
    evidence["catalog_prefix"] = "producer-skills/"

    job_id = chain["artifact_stages"][0]["job"]["id"]
    paths = [
        "/api/skill-extractions/uploads",
        "/api/skill-extractions",
        f"/api/skill-extractions/{job_id}/draft",
        f"/api/skill-extractions/{job_id}/save",
        "/api/series",
    ]
    statuses = [201, 202, 200, 200, 201]
    bodies = [{}, {}, {"expected_digest": chain["artifact_stages"][0]["job"]["digest"]},
              {"expected_digest": chain["artifact_stages"][1]["job"]["digest"]}, {}]
    writes = [{"method": "POST", "path": path, "status": status, "body": deepcopy(body)}
              for path, status, body in zip(paths, statuses, bodies)]
    evidence["network"] = writes + production["network"]
    evidence["browser"]["artifact_write_receipts"] = [
        {"path": path, "body": deepcopy(body)} for path, body in zip(paths, bodies)
    ]
    evidence["network_counts_ok"] = True
    return evidence


class ArtifactChainTests(unittest.TestCase):
    def test_complete_artifact_chain_passes_all_eight_checks_with_crlf_file_hash(self):
        checks = chain_checks(artifact_evidence())
        self.assertEqual(len(checks), 8)
        self.assertEqual(set(checks.values()), {True})

    def test_missing_stage_fails_closed(self):
        evidence = artifact_evidence()
        evidence["artifact_stages"].pop(1)
        self.assertEqual(set(chain_checks(evidence).values()), {False})

    def test_artifact_chain_rejects_each_broken_transition(self):
        cases = [
            ("draft_gate", lambda e: e["artifact_stages"][0]["job"].update(saved_skills=[{"id": "too-early"}])),
            ("edited_version", lambda e: e["browser"].update(edited_and_restored=False)),
            ("edited_version", lambda e: e["artifact_stages"][1]["draft"]["files"].update({"SKILL.md": "0" * 64})),
            ("reference_chain", lambda e: e["artifact_stages"][1]["reference_copies"].update(job_reference="0" * 64)),
            ("explicit_adoption", lambda e: e["artifact_stages"][2]["job"].update(digest="stale-cas-digest")),
            ("new_series_binding", lambda e: e["artifact_stages"][3]["state"]["database"]["series"][-1].update(creator_id="creator-b")),
            ("new_series_binding", lambda e: e["artifact_stages"][3]["state"]["database"]["series"][-1].update(skill_name="wrong-skill")),
            ("approved_skill_frozen", lambda e: e["after"]["database"]["content_runs"][0]["input_snapshot_json"].update(skill_digest="old-version")),
            ("approved_skill_frozen", lambda e: e["outputs"]["images"][0].update(sha256=e["oracle"]["reference_sha256"])),
            ("extraction_completed", lambda e: e["sdk"]["events"][1].update(status="failed")),
            ("extraction_completed", lambda e: e["artifact_stages"][0]["job"].update(thread_id="production-thread")),
            ("global_skills_unchanged", lambda e: e["global_skills_guard"]["after"]["codex_skills"]["files"].update(polluted="changed")),
        ]
        for expected_failure, mutate in cases:
            with self.subTest(expected_failure=expected_failure, mutation=mutate):
                evidence = artifact_evidence()
                mutate(evidence)
                checks = chain_checks(evidence)
                self.assertIs(checks[expected_failure], False, (expected_failure, checks))

    def test_p03_grade_slice_checks_gui_write_count_and_cas(self):
        evidence = p03_gui_write_evidence()

        def artifact_gui_check(report):
            return next(row["status"] for row in report["checks"] if row["id"] == "artifact_gui_writes")

        self.assertEqual(artifact_gui_check(grade_slice(evidence, "P03")), "passed")

        duplicate = p03_gui_write_evidence()
        duplicate["network"].insert(0, deepcopy(duplicate["network"][0]))
        self.assertEqual(artifact_gui_check(grade_slice(duplicate, "P03")), "failed")

        stale_save_cas = p03_gui_write_evidence()
        stale_save_cas["browser"]["artifact_write_receipts"][3]["body"]["expected_digest"] = "stale-digest"
        self.assertEqual(artifact_gui_check(grade_slice(stale_save_cas, "P03")), "failed")

