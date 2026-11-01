"""Fresh selection through real HTTP/SQLite; no producer is invoked."""
import unittest
from creatoros.storage import ContentRun, ContentRunStatus
from tests import test_skill_version_scope as fixture


class FreshSkillIdsTests(unittest.TestCase):
    setUp = fixture.SkillVersionScopeTests.setUp
    skill = fixture.SkillVersionScopeTests.skill

    def test_multiple_selected_skills_are_frozen_and_other_account_is_denied(self):
        source = self.runs.create("review-1")
        original = source.input_snapshot_json
        with self.database.session() as session:
            row = session.get(ContentRun, source.id)
            row.status = ContentRunStatus.FAILED
            row.version += 1
        source = self.runs.get(source.id)
        headers = {"x-creatoros-agent-session": self.account}
        payload = {"mode": "fresh", "topic_id": source.topic_id, "source_run_id": source.id,
            "expected_version": source.version, "request_id": "fresh-selected-contract",
            "skill_ids": [self.legacy["id"], self.mind["id"]]}
        response = self.client.post("/api/runs", json=payload, headers=headers)
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        selected = result["input_snapshot"]["execution_spec"]["skills"]
        self.assertEqual([item["skill_id"] for item in selected], payload["skill_ids"])
        self.assertEqual({item["role"] for item in selected}, {"selected"})
        self.assertEqual(self.client.post("/api/runs", json=payload, headers=headers).json()["id"], result["id"])
        denied = self.client.post("/api/runs", json={**payload, "request_id": "fresh-forbidden",
            "skill_ids": [self.foreign["id"]]}, headers=headers)
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(self.runs.get(source.id).input_snapshot_json, original)
        self.assertEqual(self.producer.calls, 0)
