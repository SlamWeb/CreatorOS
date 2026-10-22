"""Bounded Windows directory-publication faults; no model/API calls."""
import errno
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.integrations.skill_extraction import ExtractionError
from creatoros.integrations.skill_version_service import SkillVersionService, signature


def denied(code=5):
    error = PermissionError(errno.EACCES, "controlled directory publication failure")
    error.winerror = code
    return error


class SkillTrialPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = SkillVersionService.__new__(SkillVersionService)
        self.service.catalog = ProducerSkillCatalog(self.root / "catalog")
        self.inputs = dict(skill_id="candidate-one", expected_digest="a" * 64, topic="Frozen topic", series_id="series-one")
        self.job_id = "1" * 64

    def prepared(self, name):
        staging = self.root / name
        staging.mkdir()
        (staging / "job.json").write_text(json.dumps(dict(id=self.job_id, input_digest=signature(self.inputs))), encoding="utf-8")
        (staging / "request.json").write_text(json.dumps(self.inputs), encoding="utf-8")
        (staging / "SKILL.md").write_text("Frozen candidate; must never overwrite existing history.", encoding="utf-8")
        return staging

    def test_windows_5_32_33_are_bounded_then_publish_same_preparation(self):
        rename = Path.rename
        for code in (5, 32, 33):
            with self.subTest(code=code):
                staging, target = self.prepared(f"stage-{code}"), self.root / f"job-{code}"
                calls = []
                def blocked(path, destination):
                    calls.append((path, destination))
                    if len(calls) < 4:
                        raise denied(code)
                    return rename(path, destination)
                with patch.object(Path, "rename", blocked), patch("creatoros.integrations.skill_version_service.sleep") as sleep:
                    self.service._publish_trial_directory(staging, target, self.job_id, self.inputs)
                self.assertEqual(len(calls), 4)
                self.assertEqual([call.args[0] for call in sleep.call_args_list], [.05, .1, .2])
                self.assertFalse(staging.exists())
                self.assertEqual(json.loads((target / "request.json").read_text(encoding="utf-8")), self.inputs)

    def test_persistent_windows_denial_stops_without_overwriting_or_model_submission(self):
        staging, target = self.prepared("permanent"), self.root / "never-published"
        before = {p.name: p.read_bytes() for p in staging.iterdir()}
        with patch.object(Path, "rename", side_effect=denied()) as rename, patch("creatoros.integrations.skill_version_service.sleep") as sleep:
            with self.assertRaises(PermissionError):
                self.service._publish_trial_directory(staging, target, self.job_id, self.inputs)
        self.assertEqual(rename.call_count, 4)
        self.assertEqual(sleep.call_count, 3)
        self.assertFalse(target.exists())
        self.assertEqual({p.name: p.read_bytes() for p in staging.iterdir()}, before)

    def test_unrelated_error_does_not_retry(self):
        staging, target = self.prepared("wrong-device"), self.root / "absent"
        with patch.object(Path, "rename", side_effect=OSError(errno.EXDEV, "wrong volume")) as rename, patch("creatoros.integrations.skill_version_service.sleep") as sleep:
            with self.assertRaises(OSError):
                self.service._publish_trial_directory(staging, target, self.job_id, self.inputs)
        self.assertEqual(rename.call_count, 1)
        sleep.assert_not_called()
        self.assertTrue(staging.exists())

    def test_exact_existing_publication_is_adopted_without_touching_either_directory(self):
        staging, target = self.prepared("retry"), self.prepared("published")
        before = {p.name: p.read_bytes() for p in target.iterdir()}
        with patch.object(Path, "rename", side_effect=AssertionError("must not overwrite existing folder")):
            self.service._publish_trial_directory(staging, target, self.job_id, self.inputs)
        self.assertTrue(staging.exists())
        self.assertEqual({p.name: p.read_bytes() for p in target.iterdir()}, before)

    def test_different_id_digest_or_full_request_rejects_existing_publication(self):
        for field in ("id", "input_digest", "request"):
            with self.subTest(field=field):
                staging, target = self.prepared("new-" + field), self.prepared("old-" + field)
                path = target / ("request.json" if field == "request" else "job.json")
                data = json.loads(path.read_text(encoding="utf-8"))
                data["topic" if field == "request" else field] = "different"
                path.write_text(json.dumps(data), encoding="utf-8")
                before = {p.name: p.read_bytes() for p in target.iterdir()}
                with self.assertRaises(ExtractionError), patch.object(Path, "rename", side_effect=AssertionError("must not overwrite")):
                    self.service._publish_trial_directory(staging, target, self.job_id, self.inputs)
                self.assertEqual({p.name: p.read_bytes() for p in target.iterdir()}, before)
                self.assertTrue(staging.exists())

    def test_rename_committed_before_error_is_reconciled_as_same_publication(self):
        staging, target = self.prepared("source"), self.root / "accepted"
        actual = Path.rename
        calls = []
        def lost(path, destination):
            calls.append((path, destination))
            actual(path, destination)
            raise denied()
        with patch.object(Path, "rename", lost), patch("creatoros.integrations.skill_version_service.sleep") as sleep:
            self.service._publish_trial_directory(staging, target, self.job_id, self.inputs)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()
        self.assertFalse(staging.exists())
        self.assertTrue((target / "job.json").is_file())


if __name__ == "__main__":
    unittest.main()
