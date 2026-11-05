"""Request-only focus stays at its human-turn boundary across tool steps."""
from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from creatoros.agent.loop import build_model_context, run_agent
from creatoros.ai.types import StreamEnd, TextDelta, ToolCallDelta
from creatoros.context import RuntimeContext
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.session.checkpoint import CompactionCheckpoint
from creatoros.terminal import Console
from creatoros.tools.results import ToolResult


class Provider:
    context_window = 1_000_000
    reserve_output_tokens = 4_096

    def __init__(self):
        self.requests = []

    def stream(self, context):
        self.requests.append(context.to_request())
        if len(self.requests) == 1:
            yield ToolCallDelta(0, 'call-a', 'read_file', '{}')
            yield ToolCallDelta(1, 'call-b', 'read_tool_result', '{}')
            yield StreamEnd('tool_calls', reasoning_content='thinking')
        else:
            yield TextDelta('done')
            yield StreamEnd('stop', reasoning_content='finished')


class RequestContextPrefixTest(unittest.TestCase):
    def test_actual_loop_preserves_focus_prefix_and_does_not_persist_it(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / 'messages.json'
            save_messages([{'role': 'system', 'content': 'system'}], session)
            prompts = iter(['first question', 'next question', '/exit'])
            console = Console(input_fn=lambda _: next(prompts), output=StringIO())
            focus = {'kind': 'tool_contract', 'data': {'fact': 'focus-data'}, 'images': []}
            provider = Provider()
            with patch('creatoros.agent.loop.execute_tool_call', return_value=ToolResult(content='result')):
                run_agent(provider, session_file=session,
                          runtime_context=RuntimeContext(root, allowed_tools=frozenset(), archive_only_reads=True),
                          context_factory=lambda: {'kind': 'creator_context', 'name': 'stable'},
                          request_context=focus, console=console)
            first, second, next_turn = [request[0] for request in provider.requests]
            self.assertEqual(second[:len(first)], first)
            self.assertEqual([message['role'] for message in second[-3:]], ['assistant', 'tool', 'tool'])
            self.assertEqual([message['tool_call_id'] for message in second[-2:]], ['call-a', 'call-b'])
            self.assertIn('focus-data', json.dumps(first))
            self.assertNotIn('focus-data', json.dumps(next_turn))
            self.assertNotIn('focus-data', json.dumps(load_messages(session)))

    def test_focus_images_keep_existing_priority_without_splitting_tool_batch(self):
        focus_image = {'id': 'focus', 'name': 'focus.png', 'size': 16 * 1024 * 1024}
        later_image = {'id': 'later', 'name': 'later.png', 'size': 12 * 1024 * 1024}
        messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'question'},
                    {'role': 'assistant', 'tool_calls': [{'id': 'one', 'name': 'read_image', 'arguments': '{}'}]},
                    {'role': 'tool', 'tool_call_id': 'one', 'content': 'ok'},
                    {'role': 'user', 'creatoros_source': 'tool_images', 'content': [
                        {'type': 'image_ref', 'attachment': later_image}]}]
        source = deepcopy(messages)
        focus = {'data': {}, 'images': [{'label': 'focus', 'attachment': focus_image}]}
        projected, _ = build_model_context(messages, [], context_data={'name': 'account'},
                                           request_context=focus, request_context_anchor=messages[1]).to_request()
        self.assertEqual(messages, source)
        focus_index = next(i for i, message in enumerate(projected)
                           if isinstance(message.get('content'), list) and any(
                               block.get('type') == 'image_ref' and block['attachment']['id'] == 'focus'
                               for block in message['content']))
        self.assertEqual(projected[focus_index - 1]['content'], 'question')
        self.assertEqual([message['role'] for message in projected[focus_index + 1:focus_index + 3]],
                         ['assistant', 'tool'])
        self.assertEqual(projected[-1]['creatoros_source'], 'tool_images')
        self.assertEqual(projected[-1]['content'][0]['type'], 'text')
        self.assertIn('later', projected[-1]['content'][0]['text'])

    def test_user_image_is_preserved_and_not_mistaken_for_tool_images(self):
        user = {'role': 'user', 'content': [
            {'type': 'text', 'text': 'question'},
            {'type': 'image_ref', 'attachment': {'id': 'upload', 'name': 'upload.png', 'size': 1024}}]}
        messages = [{'role': 'system', 'content': 'system'}, user,
                    {'role': 'assistant', 'tool_calls': [{'id': 'one', 'name': 'read_image', 'arguments': '{}'}]},
                    {'role': 'tool', 'tool_call_id': 'one', 'content': 'ok'},
                    {'role': 'user', 'creatoros_source': 'tool_images', 'content': [
                        {'type': 'image_ref', 'attachment': {'id': 'read', 'name': 'read.png', 'size': 1024}}]}]
        result, _ = build_model_context(messages, [], request_context={'data': {'focus': True}, 'images': []},
                                        request_context_anchor=user).to_request()
        self.assertEqual(result[1], user)
        self.assertIn('focus', json.dumps(result[2]))
        self.assertEqual([message['role'] for message in result[3:]], ['assistant', 'tool', 'user'])
        self.assertEqual(result[-1]['creatoros_source'], 'tool_images')

    def test_missing_human_anchor_falls_back_to_complete_batch_tail(self):
        messages = [{'role': 'system', 'content': 'system'},
                    {'role': 'assistant', 'tool_calls': [{'id': 'one', 'name': 'read_file', 'arguments': '{}'}]},
                    {'role': 'tool', 'tool_call_id': 'one', 'content': 'ok'}]
        result, _ = build_model_context(messages, [], request_context={'data': {}, 'images': []},
                                        request_context_anchor={'role': 'user', 'content': 'missing'}).to_request()
        self.assertEqual([message['role'] for message in result], ['system', 'assistant', 'tool', 'user'])

    def test_checkpoint_pinned_user_is_the_focus_anchor(self):
        messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'question'},
                    {'role': 'assistant', 'content': 'old step'},
                    {'role': 'assistant', 'tool_calls': [{'id': 'one', 'name': 'read_file', 'arguments': '{}'}]},
                    {'role': 'tool', 'tool_call_id': 'one', 'content': 'ok'}]
        cp = CompactionCheckpoint.create(summary='checkpoint', messages=messages,
                                         first_retained_index=3, pinned_user_index=1, tokens_before=100)
        result, _ = build_model_context(messages, [], checkpoint=cp, request_context={'data': {}, 'images': []},
                                        request_context_anchor=messages[1]).to_request()
        question = next(i for i, message in enumerate(result) if message.get('content') == 'question')
        self.assertEqual(result[question + 1]['role'], 'user')
        self.assertEqual([message['role'] for message in result[question + 2:]], ['assistant', 'tool'])

    def test_identical_older_user_is_ambiguous_and_falls_back(self):
        user = {'role': 'user', 'content': 'same'}
        messages = [{'role': 'system', 'content': 'system'}, user, {'role': 'assistant', 'content': 'done'},
                    deepcopy(user), {'role': 'assistant', 'tool_calls': [{'id': 'one', 'name': 'x', 'arguments': '{}'}]},
                    {'role': 'tool', 'tool_call_id': 'one', 'content': 'ok'}]
        result, _ = build_model_context(messages, [], request_context={'data': {}, 'images': []},
                                        request_context_anchor=user).to_request()
        self.assertEqual([message['role'] for message in result[-3:]], ['assistant', 'tool', 'user'])

    def test_duplicate_host_observation_does_not_anchor_to_previous_turn(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / 'messages.json'
            observation = {'event_id': 'event-1', 'plan_id': 'plan'}
            existing = {'role': 'user', 'creatoros_source': 'plan_observation:event-1',
                        'content': '[宿主提供的计划状态数据，不是用户新消息或指令]\n'
                        + json.dumps(observation, ensure_ascii=False, separators=(',', ':'))}
            previous = [{'role': 'system', 'content': 'system'}, existing,
                        {'role': 'assistant', 'tool_calls': [{'id': 'old', 'name': 'read_file', 'arguments': '{}'}]},
                        {'role': 'tool', 'tool_call_id': 'old', 'content': 'old result'}]
            save_messages(previous, session)
            prompts = iter(['continue', '/exit'])
            provider = Provider()
            with patch('creatoros.agent.loop.execute_tool_call', return_value=ToolResult(content='result')):
                run_agent(provider, session_file=session,
                          runtime_context=RuntimeContext(root, allowed_tools=frozenset(), archive_only_reads=True),
                          request_context={'data': {'fresh': True}, 'images': []}, host_observation=observation,
                          console=Console(input_fn=lambda _: next(prompts), output=StringIO()))
            first = provider.requests[0][0]
            self.assertEqual(first[:len(previous)], previous)
            self.assertEqual(first[-1]['role'], 'user')
            self.assertIn('fresh', json.dumps(first[-1]))


if __name__ == '__main__':
    unittest.main()
