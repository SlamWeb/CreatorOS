"""Account Skill metadata and on-demand reads through the Web Agent."""
import json
import unittest
from uuid import uuid4
from unittest.mock import patch

from creatoros.storage import CreatorSkillRef, Series, SeriesSkillRef
from tests.test_account_context_crud_integration import ScriptedProvider
from tests import test_skill_version_scope as scope_fixture


class AgentSkillDiscoveryTests(unittest.TestCase):
    setUp = scope_fixture.SkillVersionScopeTests.setUp
    skill = scope_fixture.SkillVersionScopeTests.skill
    header = scope_fixture.SkillVersionScopeTests.header

    def test_live_directory_precedes_scoped_body_read(self):
        library = self.app.state.skill_library
        additional = self.skill("format-receipts", "legacy_end_to_end")
        library.add("review-lab", additional["id"], "add-format-skill")
        self.catalog.create_skill_candidate(additional["id"], additional["digest"],
                                            "not-explicitly-referenced", creator_id="review-lab")

        class Provider(ScriptedProvider):
            @property
            def client(self):
                return self

            def close(self):
                pass

        provider = Provider([("get_producer_skill", {"skill_id": additional["id"], "path": "SKILL.md"})])
        chat = self.app.state.chat
        chat.provider_factory = lambda: provider
        endpoint = f"/api/agent/sessions/{self.account}/turns"
        # The fixture emits the existing tool call deterministically. It does
        # not simulate the model's ability to match prose to a description.
        with patch.dict("os.environ", {"CREATOROS_TOOL_PROTOCOL": "legacy"}):
            response = self.client.post(endpoint, json={"request_id": str(uuid4()),
                "text": "把这张收据整理成可复用的格式。", "expected_version": 0})
            self.assertEqual(response.status_code, 202, response.text)
            worker = chat.threads.get(self.account)
            if worker:
                worker.join(timeout=15)
                self.assertFalse(worker.is_alive())

        first, second = provider.contexts
        directory = next(item["content"] for item in first if "宿主账号目录" in str(item.get("content")))
        state = json.loads(directory.split("\n", 1)[1])["state"]
        self.assertEqual([item["id"] for item in state["skills"] if item["name"] == "format-receipts"],
                         [additional["id"]])
        self.assertIn("test format-receipts", directory)
        self.assertNotIn("local_path", directory)
        self.assertNotIn("assets/reference.png", directory)
        self.assertNotIn(self.foreign["id"], directory)
        self.assertIn("assets/reference.png", json.dumps(second))
        self.assertEqual(self.producer.calls, 0)

    def test_misreferenced_private_candidate_absent_from_directory_and_focus(self):
        private = self.catalog.create_skill_candidate(self.legacy["id"], self.legacy["digest"],
                                                      "another-owner", creator_id="other")
        identity = private["id"]
        with self.database.session() as db:
            db.get(Series, "agent-notes").skill_name = identity
            db.add(CreatorSkillRef(creator_id="review-lab", skill_ids_json=[self.legacy["id"], identity],
                                   request_ids_json={}, version=1))
            db.add(SeriesSkillRef(series_id="agent-notes", skill_ids_json=[self.legacy["id"], identity],
                                  defaults_json={"image": [{"skill_id": identity, "role": "primary"}],
                                                 "video": []}, version=1))

        chat = self.app.state.chat
        directory = chat.creator_context_factory("review-lab")
        self.assertNotIn(identity, json.dumps(directory))
        series = next(item for item in directory["series"] if item["id"] == "agent-notes")
        self.assertEqual(series["skill_pool"]["skill_ids"], [self.legacy["id"]])
        self.assertEqual(series["skill_pool"]["defaults"]["image"], [])
        with patch.object(self.catalog, "read_skill_file", wraps=self.catalog.read_skill_file) as read:
            focused = chat.focus_context_builder.build("review-lab", self.account,
                {"series_id": "agent-notes", "topic_id": "review-1"})
        read.assert_not_called()
        self.assertEqual(focused["data"]["current_skills"], [])
        self.assertNotIn(identity, json.dumps(focused))


if __name__ == "__main__":
    unittest.main()
