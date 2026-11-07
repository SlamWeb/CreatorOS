"""The three image sources are legible to the model and remain host-validated."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError
from creatoros.context import RuntimeContext
from creatoros.tools import tools
from creatoros.tools.discovery_gateway import ToolGateway
from creatoros.tools.host_contract import model_tool_schemas
from creatoros.tools.chat_images import ReadImageArgs


class ReadImageViewTests(unittest.TestCase):
    def test_view_states_three_exclusive_source_combinations(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = RuntimeContext(root, creator_id='owner', session_file=root/'session.json',
                                     archive_only_reads=True, allowed_tools=frozenset({'read_image'}))
            scoped = model_tool_schemas(tools, context)
            view = ToolGateway(context, scoped, model_view_version='v2', entry_mode='canonical').model_schemas()[0]
            params = view['function']['parameters']
            self.assertEqual(set(params['properties']), {'attachment_id', 'skill_id', 'path', 'run_id',
                                                         'revision_id', 'image_index'})
            self.assertEqual({frozenset(branch['required']) for branch in params['oneOf']}, {
                frozenset({'attachment_id'}), frozenset({'skill_id', 'path'}),
                frozenset({'run_id', 'revision_id', 'image_index'})})
            text = str(view)
            self.assertIn('相对路径', text)
            self.assertIn('从 1 开始', text)

    def test_original_parser_accepts_exact_sources_and_rejects_mixed_or_missing(self):
        for data in ({'attachment_id': 'a'}, {'skill_id': 's', 'path': 'assets/a.png'},
                     {'run_id': 'r', 'revision_id': 'v', 'image_index': 1}):
            with self.subTest(data=data):
                ReadImageArgs.model_validate(data)
        for data in ({}, {'skill_id': 's'}, {'path': 'assets/a.png'},
                     {'run_id': 'r', 'revision_id': 'v'},
                     {'run_id': 'r', 'revision_id': 'v', 'image_index': 0},
                     {'attachment_id': 'a', 'skill_id': 's', 'path': 'assets/a.png'}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                ReadImageArgs.model_validate(data)


if __name__ == '__main__':
    unittest.main()
