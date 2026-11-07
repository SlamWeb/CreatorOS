"""Account view requires an owned job while legacy listing remains available."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from creatoros.agent.tool_protocol import select_tool_protocol
from creatoros.ai.context import estimate_tokens
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.skill_extraction import GetSkillExtractionArgs


class SkillExtractionViewTests(unittest.TestCase):
    def test_scoped_view_requires_id_and_legacy_keeps_list(self):
        with TemporaryDirectory(prefix='extraction-read-view-') as directory:
            root = Path(directory)
            account = RuntimeContext(root, creator_id='owner', session_file=root / 'session.json',
                                     archive_only_reads=True, allowed_tools=frozenset({'get_skill_extraction'}))
            scoped = model_tool_schemas(tools, account)
            original = deepcopy(scoped)
            v1 = ToolGateway(account, scoped, model_view_version='v1').catalog['get_skill_extraction']['schema']
            v2 = ToolGateway(account, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()
            self.assertEqual(scoped, original)
            self.assertEqual(v1, scoped[0])
            self.assertEqual([item['function']['name'] for item in v2], ['get_skill_extraction'])
            self.assertLess(estimate_tokens(v2[0]), estimate_tokens(v1))
            function = v2[0]['function']
            self.assertEqual(set(function['parameters']['properties']), {'job_id'})
            self.assertEqual(function['parameters']['required'], ['job_id'])
            for term in ('ready', 'saved', 'operation'):
                self.assertIn(term, function['description'])
            overview = RuntimeContext(root, creator_id=None, session_file=root / 'overview.json',
                                      archive_only_reads=True, allowed_tools=frozenset({'get_skill_extraction'}))
            self.assertEqual(select_tool_protocol([], overview), 'legacy')
            legacy = model_tool_schemas(tools, overview)[0]['function']['parameters']
            self.assertNotIn('job_id', legacy.get('required', []))
            self.assertIsNone(GetSkillExtractionArgs().job_id)


if __name__ == '__main__':
    unittest.main()
