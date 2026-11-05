"""Read-only evidence for a single RSI round, never resubmitting production."""
from __future__ import annotations

import base64
import hashlib
import json
from math import isfinite
import re
from pathlib import Path

from .visual_evidence import VERSION as VISUAL_VERSION, _read


VERSION = "rsi-evidence-v1"
_IDENTITY = re.compile(r"^[0-9a-f]{64}$")


class MissingRSIEvidence(ValueError):
    pass


def _json(value):
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict):
        raise MissingRSIEvidence("Expected structured RSI record")
    return value


def collect_rsi_evidence(world, evidence):
    """Bind frozen reference, trial image, judge request and recorded score.

    A valid record proves what bytes were submitted to the product evaluator,
    not whether that evaluator or the benchmark Judge understood the image.
    """
    root = Path(world.root).resolve()
    result = {"version": VERSION, "status": "needs_review", "detail": "Evidence incomplete"}
    try:
        before, after, variables = evidence["before"], evidence["after"], evidence["variables"]
        old = before["database"]["skill_iterations"]
        rows = after["database"]["skill_iterations"]
        if not isinstance(old, list) or not isinstance(rows, list):
            raise MissingRSIEvidence("RSI database snapshots missing")
        old_ids = {row["id"] for row in old}
        added = [row for row in rows if row["id"] not in old_ids]
        if len(added) != 1:
            return {**result, "status": "failed", "detail": "Expected exactly one new iteration"}
        row = added[0]
        identifier = row["id"]
        if not isinstance(identifier, str) or not _IDENTITY.fullmatch(identifier):
            raise MissingRSIEvidence("Iteration identity missing")
        calls = evidence.get("calls")
        if not isinstance(calls, list):
            raise MissingRSIEvidence("Agent iteration receipts missing")
        starts = [call for call in calls if isinstance(call, dict) and call.get("tool") == "manage_skill_iteration"
                  and isinstance(call.get("arguments"), dict) and call["arguments"].get("action") == "start"
                  and call.get("is_error") is False]
        if len(starts) != 1:
            raise MissingRSIEvidence("Unique successful iteration start receipt missing")
        receipt = _json(starts[0].get("raw_result"))
        if receipt.get("id", receipt.get("iteration_id")) != identifier:
            raise MissingRSIEvidence("Start receipt is not bound to the captured iteration")
        identities = (("creator_id", "creator_a"), ("series_id", "series_a"),
                      ("topic_id", "topic_a"), ("skill_id", "skill_a"))
        if any(not isinstance(row.get(key), str) or not row[key] or
               not isinstance(variables.get(var), str) or not variables[var] for key, var in identities):
            raise MissingRSIEvidence("Iteration ownership or target identity missing")
        if any(row[key] != variables[var] for key, var in identities):
            return {**result, "status": "failed", "detail": "Iteration bound to wrong account, series, topic or Skill"}
        config, state = _json(row["config_json"]), _json(row["state_json"])
        rounds = state.get("rounds")
        if not isinstance(rounds, list):
            raise MissingRSIEvidence("Round records missing")
        if type(state.get("max_rounds")) is not int:
            raise MissingRSIEvidence("Configured round limit missing")
        if len(rounds) != 1 or state["max_rounds"] != 1:
            return {**result, "status": "failed", "detail": "Expected exactly one configured and observed round"}
        round_ = rounds[0]
        if not isinstance(round_, dict) or round_.get("number") != 1:
            raise MissingRSIEvidence("Round identity missing")
        if not isinstance(row.get("status"), str) or not isinstance(round_.get("status"), str):
            raise MissingRSIEvidence("Trial or iteration status missing")
        if row["status"] not in {"completed", "exhausted"} or round_["status"] != "completed":
            return {**result, "status": "failed", "detail": "Trial or iteration did not finish"}
        job, trial = round_.get("trial_job_id"), round_.get("trial_id")
        if not all(isinstance(x, str) and _IDENTITY.fullmatch(x) for x in (job, trial)):
            raise MissingRSIEvidence("Trial identities missing")
        snapshot = after["files"]
        job_rel = f"world-producer-skills/extractions/jobs/{job}/job.json"
        job_record = _json(_read(root, job_rel, snapshot.get(job_rel)).decode("utf-8"))
        if (job_record.get("id") != job or job_record.get("version_candidate_id") != round_.get("skill_id")
                or job_record.get("version_candidate_digest") != round_.get("digest")
                or job_record.get("version_series_id") != variables["series_a"]
                or job_record.get("version_bound_id") != variables["skill_a"]):
            raise MissingRSIEvidence("Trial job and candidate identity disagree")
        trials = job_record.get("trials")
        matches = [item for item in trials or [] if isinstance(item, dict) and item.get("id") == trial]
        if (len(matches) != 1 or matches[0].get("status") != "completed"
                or not job_record.get("digest") or matches[0].get("digest") != job_record["digest"]):
            raise MissingRSIEvidence("Unique completed trial missing")
        files = round_.get("files")
        if not isinstance(files, dict) or len(files) != 1:
            raise MissingRSIEvidence("Unique scored trial image missing")
        image = next(iter(files.values()))
        if not isinstance(image, dict) or image.get("order") != 1:
            raise MissingRSIEvidence("Trial image order missing")
        checksum = image.get("sha256")
        path = Path(world.extractions.trial_image(job, trial, 1)).resolve()
        relative = path.relative_to(root).as_posix()
        if snapshot.get(relative) != checksum:
            raise MissingRSIEvidence("Trial image identity differs from snapshot")
        _read(root, relative, checksum)
        refs = config.get("references")
        if not isinstance(refs, list) or len(refs) != 1 or refs[0].get("name") != "assets/reference-01.png":
            raise MissingRSIEvidence("Requested reference identity missing")
        ref = refs[0]
        ref_hash = ref.get("sha256")
        original = f"world-producer-skills/working/{variables['skill_a']}/assets/reference-01.png"
        if original not in before["files"] or not ref_hash:
            raise MissingRSIEvidence("Original reference hash missing")
        if before["files"][original] != ref_hash:
            return {**result, "status": "failed", "detail": "Frozen reference does not match requested original"}
        ref_path = f"world-producer-skills/iterations/{identifier[:16]}/references/{ref['file']}"
        if snapshot.get(ref_path) != ref_hash:
            raise MissingRSIEvidence("Frozen reference file missing")
        _read(root, ref_path, ref_hash)
        judge_version = round_.get("judge_version")
        if type(judge_version) is not int or judge_version < 1:
            raise MissingRSIEvidence("Evaluation version missing")
        key = f"judge-1-{judge_version}"
        call = state.get("calls", {}).get(key)
        if not isinstance(call, dict) or call.get("status") != "completed" or type(call.get("attempt")) is not int:
            raise MissingRSIEvidence("Completed scoring request missing")
        prefix = f"world-producer-skills/iterations/{identifier[:16]}/model/{key}-{call['attempt']}"
        def frozen_json(name):
            rel = prefix + "/" + name
            return _json(_read(root, rel, snapshot.get(rel)).decode("utf-8"))
        manifest, response = frozen_json("request.json"), frozen_json("response.json")
        manifest_sha = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if call.get("input_digest") != manifest_sha or response.get("result") != call.get("result"):
            raise MissingRSIEvidence("Scoring request or recorded response is not bound to the stored call")
        expected_images = [{"name": "优秀参考：assets/reference-01.png", "sha256": ref_hash},
                           {"name": "当前试产第1页", "sha256": checksum}]
        if manifest.get("kind") != "judge" or manifest.get("images") != expected_images:
            raise MissingRSIEvidence("Judge input image manifest does not match the trial")
        evaluations = round_.get("evaluations")
        evaluated = [x for x in evaluations or [] if isinstance(x, dict) and x.get("judge_version") == judge_version]
        if len(evaluated) != 1 or evaluated[0].get("scores") != call["result"].get("scores"):
            raise MissingRSIEvidence("Recorded scoring is not bound to the completed judge response")
        if type(round_.get("score")) not in (int, float) or not isfinite(round_["score"]):
            raise MissingRSIEvidence("Finite scored round is missing")
        if evaluated[0].get("score") != round_.get("score") or state.get("best_score") != round_.get("score"):
            raise MissingRSIEvidence("Round score and final score do not match")
        events = state.get("events")
        if not isinstance(events, list) or not all(isinstance(x, dict) for x in events):
            raise MissingRSIEvidence("Iteration event history missing")
        kinds = [x.get("kind") for x in events if isinstance(x, dict)]
        if any(kinds.count(kind) != 1 for kind in ("production_submitted", "production_completed", "judged", "finished")):
            raise MissingRSIEvidence("Unique submission, completion, scoring and stop events are required")
        if not (kinds.index("production_submitted") < kinds.index("production_completed") < kinds.index("judged") < kinds.index("finished")):
            raise MissingRSIEvidence("Trial and scoring events are not causally ordered")
        submitted = events[kinds.index("production_submitted")].get("details")
        if not isinstance(submitted, dict) or submitted.get("job_id") != job or submitted.get("trial_id") != trial:
            raise MissingRSIEvidence("Submission event is not bound to this trial")
        # Match the actual measured product request, not just the saved manifest.
        matched = []
        for request in evidence.get("rsi_requests", evidence.get("requests", [])):
            if request.get("purpose") != "rsi" or request.get("error") is not None:
                continue
            for msg in request.get("context", {}).get("messages", []):
                blocks = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(blocks, list):
                    continue
                texts = [x.get("text") for x in blocks if isinstance(x, dict) and x.get("type") == "text"]
                if not texts:
                    continue
                try:
                    payload = json.loads(texts[0])
                    images = [hashlib.sha256(base64.b64decode(x["image_url"]["url"].split(",", 1)[1], validate=True)).hexdigest()
                              for x in blocks if isinstance(x, dict) and x.get("type") == "image_url"]
                except (ValueError, TypeError, KeyError, IndexError):
                    continue
                if payload == manifest.get("input") and images == [ref_hash, checksum] and request.get("response", {}).get("content"):
                    matched.append(request)
        if len(matched) != 1:
            raise MissingRSIEvidence("Unique measured scoring request with exact images missing")
        raw_response = frozen_json("raw-response.json")
        if matched[0]["response"]["content"] != raw_response.get("content"):
            raise MissingRSIEvidence("Measured scoring response differs from persisted response")
        # Reuse the product parser and scoring policy; a saved structured
        # result or repeated aggregate cannot substitute for the raw reply.
        from creatoros.integrations.skill_iteration_judge import parse_reply, score_result
        parsed = parse_reply("judge", raw_response, manifest["input"])
        if parsed != call["result"]:
            raise MissingRSIEvidence("Parsed scoring response differs from stored scoring result")
        score, passed = score_result(manifest["input"]["criteria"], parsed)
        judged = events[kinds.index("judged")].get("details")
        if (round_["score"] != score or round_.get("passed") is not passed
                or round_.get("scores") != parsed["scores"] or evaluated[0].get("passed") is not passed
                or state.get("best_round") != 1 or not isinstance(judged, dict)
                or judged.get("score") != score or judged.get("passed") is not passed):
            raise MissingRSIEvidence("Scoring aggregate, pass state or event disagrees with the raw response")
        bindings = [{"id": "image-1", "role": "rsi_reference", "path": ref_path, "sha256": ref_hash},
                    {"id": "image-2", "role": "rsi_trial", "path": relative, "sha256": checksum,
                     "iteration_id": identifier, "trial_job_id": job, "trial_id": trial}]
        visual = {"version": VISUAL_VERSION, "status": "complete", "case_id": "B04", "bindings": bindings,
                  "errors": [], "agent_read_verified": None}
        return {"version": VERSION, "status": "passed", "detail": "One scored trial is bound to frozen reference, model request and response",
                "iteration_id": identifier, "trial_job_id": job, "trial_id": trial, "judge_version": judge_version,
                "request_id": matched[0].get("request_id"), "visual_manifest": visual}
    except (KeyError, TypeError, ValueError, OSError, AttributeError, RuntimeError) as error:
        result["detail"] = f"Incomplete RSI evidence: {type(error).__name__}"
        return result
