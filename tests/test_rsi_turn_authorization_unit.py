"""The host's RSI switch is an append-only one-turn event."""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.agent.loop import run_agent
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import load_messages
from creatoros.terminal import Console
from tests.test_reconstruct_public_contract import ScriptedProvider


class RsiTurnAuthorizationTests(unittest.TestCase):
    def test_event_belongs_to_first_user_turn_only(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "messages.json"
            provider = ScriptedProvider([None, None])
            inputs = iter(("请处理这个目标。", "接下来只聊聊。", "/exit"))
            run_agent(provider, session_file=session, runtime_context=RuntimeContext(root),
                      console=Console(input_fn=lambda _prompt: next(inputs), output=StringIO()),
                      rsi_turn_authorized=True)
            messages = load_messages(session)
            grants = [(i, m) for i, m in enumerate(messages)
                      if m.get("creatoros_source", "").startswith("rsi_turn_authorization:")]
            self.assertEqual(len(grants), 1)
            index, grant = grants[0]
            self.assertEqual(messages[index - 1]["role"], "user")
            self.assertEqual(messages[index - 1]["content"], "请处理这个目标。")
            self.assertIn("不继承到后续请求", grant["content"])
            self.assertEqual(provider.requests[1][0][:len(provider.requests[0][0])], provider.requests[0][0])
