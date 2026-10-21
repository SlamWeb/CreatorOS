"""New trials have shorter Windows paths; old evidence remains at its exact path."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.integrations.skill_extraction import ExtractionError, SkillExtractionService
from creatoros.integrations.skill_workbench import trial_directory, trial_root


class TrialPathTests(unittest.TestCase):
    def test_new_names_are_short_old_names_are_unchanged(self):
        with TemporaryDirectory() as temporary:
            service = SkillExtractionService(ProducerSkillCatalog(Path(temporary) / "skills"))
            job, key = "a" * 64, "b" * 64
            parent = service._path("jobs", job) / "trials"
            self.assertEqual(trial_root(service, job, key), parent / key[:16])
            old = parent / key
            old.mkdir(parents=True)
            evidence = old / "old-evidence.txt"
            evidence.write_bytes(b"must not move")
            self.assertEqual(trial_root(service, job, key), old)
            self.assertEqual(trial_directory(service, job, key), old / "revision-001" / "attempt-001")
            self.assertEqual(evidence.read_bytes(), b"must not move")
            self.assertFalse((parent / key[:16]).exists())

    def test_invalid_key_cannot_choose_filesystem_path(self):
        with TemporaryDirectory() as temporary:
            service = SkillExtractionService(ProducerSkillCatalog(Path(temporary) / "skills"))
            for key in ("../" + "a" * 61, "A" * 64, "b" * 16, ""):
                with self.assertRaises(ExtractionError):
                    trial_root(service, "a" * 64, key)


if __name__ == "__main__":
    unittest.main()
