"""RSI path rejection must not reserve a new request identity."""
from unittest import TestCase
from unittest.mock import patch

from creatoros.integrations.skill_extraction import ExtractionError
from creatoros.integrations.skill_iterations import digest
from tests import test_skill_iteration_service as fixtures


class SkillIterationPathPreflightTests(TestCase):
    setUp = fixtures.SkillIterationServiceTests.setUp
    create = fixtures.SkillIterationServiceTests.create

    def request_directory(self):
        identity = digest({"kind": "skill-iteration", "session": None, "request_id": "iteration-one"})
        return self.iterations._path(identity)

    def test_invalid_path_does_not_reserve_identity_and_same_id_can_be_corrected(self):
        directory = self.request_directory()
        registry = {p.name for p in (self.catalog.root / "registry").glob("*.json")}
        with self.assertRaises(ExtractionError):
            self.create(reference_paths=["not-a-skill-image.png"])
        self.assertFalse(directory.exists())
        self.assertEqual(registry, {p.name for p in (self.catalog.root / "registry").glob("*.json")})
        self.assertEqual(self.iterations.list(creator_id=self.fixture["creator_id"])["items"], [])
        accepted = self.create(reference_paths=["assets/reference.png"])
        self.assertEqual(accepted["status"], "queued")
        self.assertEqual([ref["name"] for ref in accepted["references"]], ["assets/reference.png"])
        self.assertEqual(self.create(reference_paths=["assets/reference.png"])["id"], accepted["id"])
        self.assertEqual(self.producer.calls, [])

    def test_second_check_failure_preserves_existing_preparation_conflict(self):
        original = self.iterations._skill_reference_paths
        calls = []
        def change_between_checks(skill_id, selected):
            calls.append(selected)
            if len(calls) == 2:
                raise ExtractionError("参考图片在冻结前变化。", 422)
            return original(skill_id, selected)
        with patch.object(self.iterations, "_skill_reference_paths", side_effect=change_between_checks):
            with self.assertRaises(ExtractionError):
                self.create(reference_paths=["assets/reference.png"])
        directory = self.request_directory()
        self.assertTrue((directory / "identity.json").is_file())
        self.assertTrue((directory / "base").is_dir())
        before = (directory / "identity.json").read_bytes()
        with self.assertRaises(ExtractionError) as error:
            self.create(reference_paths=[])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual((directory / "identity.json").read_bytes(), before)
        self.assertEqual(self.producer.calls, [])
