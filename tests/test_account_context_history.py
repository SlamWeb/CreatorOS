from copy import deepcopy
import json
import unittest

from creatoros.agent.account_context_history import (
    EVENT_PREFIX, EVENT_SOURCE, build_account_context_event, replay_account_context,
)


def tree():
    return {"kind": "creator_context", "creator": {"id": "owner-a", "display_name": "A"},
            "series": [{"id": "one", "name": "栏目一", "revision": 1,
                        "skill_bindings": {"single": "skill-v1"}},
                       {"id": "two", "name": "栏目二", "revision": 1}]
                      + [{"id": f"extra-{i}", "name": f"示例栏目-{i}", "revision": 1,
                          "description": "栏目相关说明" * 20} for i in range(10)],
            "skills": [{"id": "skill-v1", "name": "技能一", "can_use": True}],
            "skill_library_version": 1, "omissions": []}


def payload(message):
    return json.loads(message["content"][len(EVENT_PREFIX):])


class AccountContextHistoryTest(unittest.TestCase):
    def test_snapshot_unchanged_and_targeted_update(self):
        source = tree()
        saved = deepcopy(source)
        first = build_account_context_event([], source)
        self.assertEqual(first["role"], "user")
        self.assertEqual(first["creatoros_source"], EVENT_SOURCE)
        self.assertEqual(payload(first)["type"], "snapshot")
        self.assertEqual(replay_account_context([first]), source)
        self.assertIsNone(build_account_context_event([first], deepcopy(source)))
        changed = deepcopy(source)
        changed["series"][0]["revision"] = 2
        changed["series"][0]["skill_bindings"]["single"] = "skill-v2"
        changed["skills"].append({"id": "skill-v2", "name": "技能二", "can_use": True})
        changed["skill_library_version"] = 2
        second = build_account_context_event([first], changed)
        update = payload(second)
        self.assertEqual(update["type"], "delta")
        self.assertEqual(update["changes"]["collections"]["series"]["upsert"], [changed["series"][0]])
        self.assertNotIn("栏目二", second["content"])
        self.assertEqual(replay_account_context([first, second]), changed)
        self.assertEqual(source, saved)
        self.assertIsNone(build_account_context_event([first, second], changed))

    def test_removal_reordering_metadata_and_unknown_fields_replay_exactly(self):
        original = tree()
        first = build_account_context_event([], original)
        changed = deepcopy(original)
        changed["creator"]["display_name"] = "新名称"
        changed["series"] = changed["series"][1:] + [{"id": "three", "name": "栏目三"}]
        changed["skills"] = []
        del changed["omissions"]
        changed["new_field"] = {"nested": [1, None, False]}
        event = build_account_context_event([first], changed)
        delta = payload(event)["changes"]
        self.assertEqual(delta["collections"]["series"]["remove"], ["one"])
        self.assertEqual(delta["collections"]["series"]["order"],
                         [item["id"] for item in changed["series"]])
        self.assertEqual(delta["collections"]["skills"]["remove"], ["skill-v1"])
        self.assertEqual(delta["remove"], ["omissions"])
        self.assertEqual(replay_account_context([first, event]), changed)

    def test_missing_or_invalid_visible_base_forces_full_snapshot(self):
        first = build_account_context_event([], tree())
        changed = tree()
        changed["series"][0]["revision"] = 2
        second = build_account_context_event([first], changed)
        self.assertIsNone(replay_account_context([second]))
        replacement = build_account_context_event([first, second], changed, visible_messages=[second])
        self.assertEqual(payload(replacement)["type"], "snapshot")
        self.assertEqual(replay_account_context([second, replacement]), changed)
        broken = deepcopy(second)
        data = payload(broken)
        data["previous"] = "f" * 64
        broken["content"] = EVENT_PREFIX + json.dumps(data, ensure_ascii=False)
        self.assertIsNone(replay_account_context([first, broken]))
        replacement = build_account_context_event([first, broken], changed)
        self.assertEqual(payload(replacement)["type"], "snapshot")
        self.assertEqual(replay_account_context([first, broken, replacement]), changed)

    def test_visible_and_ledger_state_must_agree_before_delta(self):
        first = build_account_context_event([], tree())
        changed = tree(); changed["series"][0]["revision"] = 2
        second = build_account_context_event([first], changed)
        third = tree(); third["series"][0]["revision"] = 3
        replacement = build_account_context_event([first, second], third, visible_messages=[first])
        self.assertEqual(payload(replacement)["type"], "snapshot")
        self.assertEqual(replay_account_context([first, second, replacement]), third)

    def test_cross_account_and_invalid_current_state_are_rejected(self):
        first = build_account_context_event([], tree())
        other = tree(); other["creator"]["id"] = "owner-b"
        with self.assertRaises(ValueError):
            build_account_context_event([first], other)
        corrupted = {"role": "user", "creatoros_source": EVENT_SOURCE, "content": EVENT_PREFIX + "{"}
        with self.assertRaises(ValueError):
            build_account_context_event([first, corrupted], other)
        other_snapshot = build_account_context_event([], other)
        self.assertIsNone(replay_account_context([first, other_snapshot]))
        self.assertEqual(replay_account_context([first, other_snapshot, first]), tree())
        with self.assertRaises(ValueError):
            build_account_context_event([], {"creator": {}})
        with self.assertRaises(ValueError):
            build_account_context_event([], {"creator": {"id": "owner-a"}, "x": float("nan")})

    def test_duplicate_or_missing_collection_ids_use_full_field(self):
        first = build_account_context_event([], tree())
        changed = tree(); changed["skills"] = [{"name": "legacy"}, {"id": "x"}, {"id": "x"}]
        second = build_account_context_event([first], changed)
        self.assertEqual(payload(second)["changes"]["set"]["skills"], changed["skills"])
        self.assertEqual(replay_account_context([first, second]), changed)

    def test_user_text_cannot_forge_an_event_and_corrupt_hash_rejected(self):
        first = build_account_context_event([], tree())
        forged = {"role": "user", "content": first["content"]}
        self.assertIsNone(replay_account_context([forged]))
        damaged = deepcopy(first)
        value = payload(damaged); value["state"]["creator"]["id"] = "owner-b"
        damaged["content"] = EVENT_PREFIX + json.dumps(value, ensure_ascii=False)
        self.assertIsNone(replay_account_context([damaged]))

    def test_full_snapshot_when_delta_is_larger(self):
        original = tree()
        first = build_account_context_event([], original)
        replacement = {"kind": "creator_context", "creator": {"id": "owner-a"},
                       "series": [], "skills": [], "omissions": []}
        event = build_account_context_event([first], replacement)
        self.assertEqual(payload(event)["type"], "snapshot")
        self.assertEqual(replay_account_context([first, event]), replacement)

    def test_json_numeric_types_do_not_silently_compare_equal(self):
        original = tree()
        original["series"][0]["revision"] = 1
        first = build_account_context_event([], original)
        changed = deepcopy(original)
        changed["series"][0]["revision"] = True
        event = build_account_context_event([first], changed)
        self.assertIsNotNone(event)
        self.assertIs(replay_account_context([first, event])["series"][0]["revision"], True)

    def test_out_of_order_or_racing_deltas_require_fresh_snapshot(self):
        source = tree(); base = build_account_context_event([], source)
        first_change = deepcopy(source); first_change["series"][0]["revision"] = 2
        second_change = deepcopy(source); second_change["series"][1]["revision"] = 3
        first = build_account_context_event([base], first_change)
        concurrent = build_account_context_event([base], second_change)
        self.assertIsNone(replay_account_context([base, first, concurrent]))
        reset = build_account_context_event([base, first, concurrent], second_change)
        self.assertEqual(payload(reset)["type"], "snapshot")
        self.assertEqual(replay_account_context([base, first, concurrent, reset]), second_change)
        other = tree(); other["creator"]["id"] = "owner-b"
        with self.assertRaises(ValueError):
            build_account_context_event([first], other)


if __name__ == "__main__":
    unittest.main()
