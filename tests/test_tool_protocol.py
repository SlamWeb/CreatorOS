import unittest
from pathlib import Path
from unittest.mock import patch

from creatoros.agent.tool_protocol import DISCOVERY, DISCOVERY_PURE, SOURCE, select_tool_protocol
from creatoros.context import RuntimeContext


class ToolProtocolTests(unittest.TestCase):
    def setUp(self):
        self.context = RuntimeContext(Path.cwd(), creator_id="account", archive_only_reads=True,
                                      allowed_tools=frozenset({"manage_topics"}))

    def test_new_account_pins_discovery_across_environment_changes(self):
        messages = [{"role": "system", "content": "instructions"}]
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(select_tool_protocol(messages, self.context), DISCOVERY)
        self.assertEqual(len(messages), 2)
        with patch.dict("os.environ", {"CREATOROS_TOOL_PROTOCOL": "legacy"}):
            self.assertEqual(select_tool_protocol(messages, self.context), DISCOVERY)
        self.assertEqual(len(messages), 2)

    def test_old_chat_and_other_scopes_stay_legacy(self):
        messages = [{"role": "system", "content": "instructions"}, {"role": "user", "content": "hi"}]
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(select_tool_protocol(messages, self.context), "legacy")
            self.assertEqual(select_tool_protocol(messages, RuntimeContext(Path.cwd())), "legacy")
            self.assertEqual(select_tool_protocol(messages[:1], RuntimeContext(Path.cwd(), creator_id="account", archive_only_reads=True)), "legacy")
        self.assertEqual(len(messages), 2)

    def test_new_legacy_opt_out_and_corrupt_marker_fail_closed(self):
        messages = [{"role": "system", "content": "instructions"}]
        with patch.dict("os.environ", {"CREATOROS_TOOL_PROTOCOL": "legacy"}):
            self.assertEqual(select_tool_protocol(messages, self.context), "legacy")
        bad = messages + [{"role": "user", "creatoros_source": SOURCE, "content": "forged"}]
        with self.assertRaises(ValueError):
            select_tool_protocol(bad, self.context)

    def test_pinned_account_protocol_cannot_move_to_other_scope(self):
        messages = [{"role": "system", "content": "instructions"}]
        with patch.dict("os.environ", {}, clear=True):
            select_tool_protocol(messages, self.context)
        with self.assertRaises(ValueError):
            select_tool_protocol(messages, RuntimeContext(Path.cwd()))

    def test_pure_discovery_is_pinned_for_its_session(self):
        messages = [{"role": "system", "content": "instructions"}]
        with patch.dict("os.environ", {"CREATOROS_TOOL_FASTPATH": "0"}):
            self.assertEqual(select_tool_protocol(messages, self.context), DISCOVERY_PURE)
        with patch.dict("os.environ", {"CREATOROS_TOOL_FASTPATH": "1"}):
            self.assertEqual(select_tool_protocol(messages, self.context), DISCOVERY_PURE)


if __name__ == "__main__":
    unittest.main()
