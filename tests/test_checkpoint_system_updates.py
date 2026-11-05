from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.session.checkpoint import (
    CompactionCheckpoint, load_compaction_checkpoint, save_compaction_checkpoint,
)


class CheckpointSystemUpdateTest(unittest.TestCase):
    def setUp(self):
        self.messages = [
            {"role": "system", "content": "旧规则", "host": "v1"},
            {"role": "developer", "content": "旧指引"},
            {"role": "user", "content": "原用户任务"},
            {"role": "assistant", "content": "已处理步骤"},
            {"role": "assistant", "tool_calls": [{"id": "a", "name": "read_file", "arguments": "{}"}]},
            {"role": "tool", "tool_call_id": "a", "content": "结果"},
        ]
        self.checkpoint = CompactionCheckpoint.create(
            summary="旧历史摘要", messages=self.messages, first_retained_index=4,
            pinned_user_index=2, tokens_before=100)

    def test_new_checkpoint_survives_only_leading_instruction_content_change(self):
        changed = deepcopy(self.messages)
        changed[0]["content"] = "新规则优先"
        changed[1]["content"] = "新指引"
        changed.append({"role": "user", "content": "后来追问"})
        with TemporaryDirectory() as directory:
            path = Path(directory) / "messages.json"
            save_compaction_checkpoint(self.checkpoint, path)
            loaded = load_compaction_checkpoint(changed, path)
            self.assertEqual(loaded, self.checkpoint)
            projected = loaded.project_messages(changed)
        self.assertEqual(projected[:2], changed[:2])
        self.assertEqual(projected[3], self.messages[2])  # pinned original user
        self.assertEqual(projected[4:6], self.messages[4:6])
        self.assertEqual(projected[-1], changed[-1])
        self.assertNotIn("旧规则", str(projected))
        self.assertIn("旧历史摘要", projected[2]["content"])

    def test_source_tampering_or_prefix_structure_change_is_rejected(self):
        cases = []
        value = deepcopy(self.messages); value[0]["host"] = "changed"; cases.append(value)
        value = deepcopy(self.messages); value[0]["role"] = "developer"; cases.append(value)
        value = deepcopy(self.messages); del value[0]["content"]; cases.append(value)
        value = deepcopy(self.messages); value[2]["content"] = "伪造用户任务"; cases.append(value)
        value = deepcopy(self.messages); value[5]["content"] = "伪造结果"; cases.append(value)
        value = deepcopy(self.messages); value.insert(1, {"role": "system", "content": "新前缀"}); cases.append(value)
        value = deepcopy(self.messages); value.pop(1); cases.append(value)
        for index, value in enumerate(cases):
            with self.subTest(index=index):
                self.assertFalse(self.checkpoint.matches_session(value))
                with self.assertRaises(ValueError):
                    self.checkpoint.project_messages(value)

    def test_legacy_checkpoint_falls_back_to_original_strict_digest(self):
        data = self.checkpoint.to_dict()
        del data["stable_content_independent_digest"]
        legacy = CompactionCheckpoint.from_dict(data)
        self.assertTrue(legacy.matches_session(self.messages))
        changed = deepcopy(self.messages)
        changed[0]["content"] = "新规则"
        self.assertFalse(legacy.matches_session(changed))

    def test_malformed_new_digest_is_rejected(self):
        data = self.checkpoint.to_dict()
        data["stable_content_independent_digest"] = "not-a-digest"
        with self.assertRaises(ValueError):
            CompactionCheckpoint.from_dict(data)


if __name__ == "__main__":
    unittest.main()
