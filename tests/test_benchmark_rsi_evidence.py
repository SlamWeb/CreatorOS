"""Bind a trial's exact pixels and score request without a real model call."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from creatoros.evaluation.benchmark.rsi_evidence import collect_rsi_evidence


class RSITrialEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.iteration, self.job, self.trial = "a" * 64, "b" * 64, "c" * 64
        self.skill = "skill-a"
        self.files = {}
        self.ref, self.image = b"reference-pixels", b"trial-pixels"
        self.ref_hash = hashlib.sha256(self.ref).hexdigest()
        self.image_hash = hashlib.sha256(self.image).hexdigest()
        self.ref_path = f"world-producer-skills/iterations/{self.iteration[:16]}/references/ref.png"
        self.image_path = "trial/images/01.png"
        self.write(self.ref_path, self.ref)
        self.write(self.image_path, self.image)
        job_record = {"id":self.job, "version_candidate_id":"candidate", "version_candidate_digest":"digest",
                      "version_series_id":"S", "version_bound_id":self.skill, "digest":"draft-digest",
                      "trials":[{"id":self.trial,"status":"completed", "digest":"draft-digest"}]}
        self.write(f"world-producer-skills/extractions/jobs/{self.job}/job.json", json.dumps(job_record).encode())
        original = f"world-producer-skills/working/{self.skill}/assets/reference-01.png"
        images = [{"name":"优秀参考：assets/reference-01.png", "sha256":self.ref_hash},
                  {"name":"当前试产第1页", "sha256":self.image_hash}]
        payload = {"criteria":[{"id":"c1", "critical":True}], "topic":"sample"}
        manifest = {"kind":"judge", "input":payload, "images":images}
        signature = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        decoded = {"scores":[{"criterion_id":"c1", "score":90, "evidence":"visible"}], "feedback":"ok", "new_criteria":[]}
        raw = {"content":json.dumps(decoded, ensure_ascii=False)}
        prefix = f"world-producer-skills/iterations/{self.iteration[:16]}/model/judge-1-1-1"
        for name, value in (("request.json", manifest), ("response.json", {"result":decoded}), ("raw-response.json",raw)):
            self.write(prefix + "/" + name, json.dumps(value, ensure_ascii=False).encode())
        round_ = {"number":1, "status":"completed", "trial_job_id":self.job, "trial_id":self.trial,
                  "skill_id":"candidate", "digest":"digest",
                  "files":{"round_1_1":{"order":1, "sha256":self.image_hash}}, "judge_version":1,
                  "score":90, "passed":True, "scores":decoded["scores"],
                  "evaluations":[{"judge_version":1, "score":90, "passed":True, "scores":decoded["scores"]}]}
        state = {"max_rounds":1, "rounds":[round_], "best_score":90, "best_round":1,
                 "calls":{"judge-1-1":{"status":"completed", "attempt":1, "input_digest":signature, "result":decoded}},
                 "events":[{"kind":"production_submitted", "details":{"job_id":self.job, "trial_id":self.trial}},
                           {"kind":"production_completed"}, {"kind":"judged", "details":{"score":90,"passed":True}},
                           {"kind":"finished"}]}
        row = {"id":self.iteration,"creator_id":"A","series_id":"S","topic_id":"T","skill_id":self.skill,
               "status":"completed", "config_json":{"references":[{"name":"assets/reference-01.png","sha256":self.ref_hash,"file":"ref.png"}]},
               "state_json":state}
        blocks = [{"type":"text", "text":json.dumps(payload)},
                  *[{"type":"image_url", "image_url":{"url":"data:image/png;base64,"+base64.b64encode(v).decode()}} for v in (self.ref,self.image)]]
        self.evidence = {"variables":{"creator_a":"A","series_a":"S","topic_a":"T","skill_a":self.skill},
            "before":{"database":{"skill_iterations":[]},"files":{original:self.ref_hash}},
            "after":{"database":{"skill_iterations":[row]},"files":self.files},
            "calls":[{"tool":"manage_skill_iteration","arguments":{"action":"start"},"is_error":False,"raw_result":json.dumps({"id":self.iteration})}],
            "requests":[{"purpose":"rsi","error":None,"request_id":"req", "context":{"messages":[{"content":blocks}]},"response":raw}]}
        self.world = SimpleNamespace(root=self.root, extractions=SimpleNamespace(trial_image=lambda *args:self.root/self.image_path))

    def write(self, relative, raw):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.files[relative] = hashlib.sha256(raw).hexdigest()

    def test_one_trial_reference_pixels_and_score_bound(self):
        result = collect_rsi_evidence(self.world, self.evidence)
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual([x["role"] for x in result["visual_manifest"]["bindings"]], ["rsi_reference", "rsi_trial"])

    def test_missing_or_wrong_input_and_response_cannot_pass(self):
        for change in ("missing_requests", "different_image", "different_response", "duplicate_event", "missing_scores", "wrong_candidate", "missing_identity", "missing_limit", "missing_status", "missing_reference", "wrong_receipt"):
            data = deepcopy(self.evidence)
            if change == "missing_requests": data["requests"] = []
            elif change == "different_image": data["requests"][0]["context"]["messages"][0]["content"][-1]["image_url"]["url"] = "data:image/png;base64,"+base64.b64encode(b"other").decode()
            elif change == "different_response": data["requests"][0]["response"]["content"] = "different"
            elif change == "duplicate_event": data["after"]["database"]["skill_iterations"][0]["state_json"]["events"].append({"kind":"production_submitted"})
            elif change == "missing_scores": data["after"]["database"]["skill_iterations"][0]["state_json"]["rounds"][0]["score"] = None
            elif change == "wrong_candidate": data["after"]["database"]["skill_iterations"][0]["state_json"]["rounds"][0]["skill_id"] = "other"
            elif change == "missing_identity": del data["after"]["database"]["skill_iterations"][0]["creator_id"]
            elif change == "missing_limit": del data["after"]["database"]["skill_iterations"][0]["state_json"]["max_rounds"]
            elif change == "missing_status": del data["after"]["database"]["skill_iterations"][0]["status"]
            elif change == "missing_reference": del data["before"]["files"][f"world-producer-skills/working/{self.skill}/assets/reference-01.png"]
            else: data["calls"][0]["raw_result"] = json.dumps({"id":"d"*64})
            with self.subTest(change=change):
                self.assertEqual(collect_rsi_evidence(self.world, data)["status"], "needs_review")

    def test_wrong_reference_or_extra_round_fails_and_changed_file_is_unknown(self):
        changed = deepcopy(self.evidence)
        changed["before"]["files"][f"world-producer-skills/working/{self.skill}/assets/reference-01.png"] = "f" * 64
        self.assertEqual(collect_rsi_evidence(self.world, changed)["status"], "failed")
        extra = deepcopy(self.evidence)
        extra["after"]["database"]["skill_iterations"][0]["state_json"]["rounds"].append({"number":2})
        self.assertEqual(collect_rsi_evidence(self.world, extra)["status"], "failed")
        (self.root / self.image_path).write_bytes(b"changed")
        self.assertEqual(collect_rsi_evidence(self.world, self.evidence)["status"], "needs_review")

    def test_raw_response_and_computed_score_cannot_be_forged(self):
        data = deepcopy(self.evidence)
        state = data["after"]["database"]["skill_iterations"][0]["state_json"]
        state["rounds"][0]["score"] = state["rounds"][0]["evaluations"][0]["score"] = state["best_score"] = 1
        self.assertEqual(collect_rsi_evidence(self.world, data)["status"], "needs_review")
        data = deepcopy(self.evidence)
        state = data["after"]["database"]["skill_iterations"][0]["state_json"]
        forged = {"scores":[{"criterion_id":"c1", "score":1, "evidence":"visible"}], "feedback":"ok", "new_criteria":[]}
        state["calls"]["judge-1-1"]["result"] = forged
        state["rounds"][0]["evaluations"][0]["scores"] = forged["scores"]
        prefix = f"world-producer-skills/iterations/{self.iteration[:16]}/model/judge-1-1-1/response.json"
        path = self.root / prefix
        raw = json.dumps({"result":forged}, ensure_ascii=False).encode()
        path.write_bytes(raw)
        data["after"]["files"][prefix] = hashlib.sha256(raw).hexdigest()
        self.assertEqual(collect_rsi_evidence(self.world, data)["status"], "needs_review")

    def test_submission_and_score_events_must_match_trial(self):
        for kind, details in (("production_submitted", {"job_id":"d"*64, "trial_id":self.trial}),
                              ("judged", {"score":1, "passed":True})):
            data = deepcopy(self.evidence)
            events = data["after"]["database"]["skill_iterations"][0]["state_json"]["events"]
            next(event for event in events if event["kind"] == kind)["details"] = details
            with self.subTest(kind=kind):
                self.assertEqual(collect_rsi_evidence(self.world, data)["status"], "needs_review")

    def test_job_original_skill_and_trial_digest_are_required(self):
        job_path = f"world-producer-skills/extractions/jobs/{self.job}/job.json"
        original = json.loads((self.root / job_path).read_text(encoding="utf8"))
        for mutation in ("wrong_original", "wrong_digest"):
            data = deepcopy(self.evidence)
            job = deepcopy(original)
            if mutation == "wrong_original": job["version_bound_id"] = "another"
            else: job["trials"][0]["digest"] = "another"
            raw = json.dumps(job).encode()
            (self.root / job_path).write_bytes(raw)
            data["after"]["files"][job_path] = hashlib.sha256(raw).hexdigest()
            with self.subTest(mutation=mutation):
                self.assertEqual(collect_rsi_evidence(self.world, data)["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
