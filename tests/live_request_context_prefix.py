"""Opt-in DeepSeek cache comparison using synthetic data and a simulated tool.

No business service, tool execution, session files, or production data are used.
Pass --live and set DEEPSEEK_API_KEY in the environment or local .env to run.
"""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from time import sleep
from uuid import uuid4

from dotenv import load_dotenv

from creatoros.agent.loop import build_model_context
from creatoros.ai.deepseek import DeepSeekProvider


def payload(seed, blocks):
    return ''.join(hashlib.sha256(f'{seed}:{i}'.encode()).hexdigest() for i in range(blocks))


def experiment(provider, anchored):
    seed = uuid4().hex
    prompt = ('The following data is synthetic padding: ' + payload(seed, 180)
              + '\nCall the probe tool once, then repeat its returned marker.')
    focus = {'kind': 'tool_contract', 'data': {'padding': payload(seed + 'focus', 100)}, 'images': []}
    messages = [{'role': 'system', 'content': 'Synthetic isolated test ' + seed +
                 '. Use the probe tool before answering. No side effects.'},
                {'role': 'user', 'content': prompt}]
    tools = [{'type': 'function', 'function': {'name': 'probe',
             'description': 'Return the fixture marker.', 'parameters': {'type': 'object', 'properties': {}}}}]
    initial = build_model_context(messages, tools, request_context=focus,
                                  request_context_anchor=messages[1] if anchored else None)
    initial_response = provider.complete(replace(initial, max_output_tokens=1024))
    if len(initial_response.tool_calls) != 1 or initial_response.tool_calls[0].name != 'probe':
        raise RuntimeError('Model did not make the expected single synthetic tool call')
    messages.append(initial_response.to_message())
    messages.append({'role': 'tool', 'tool_call_id': initial_response.tool_calls[0].id,
                     'content': 'SYNTHETIC_MARKER_483'})
    # DeepSeek documents that cache persistence can take seconds.
    sleep(5)
    followup = build_model_context(messages, tools, request_context=focus,
                                   request_context_anchor=messages[1] if anchored else None)
    followup_response = provider.complete(replace(followup, max_output_tokens=1024))
    if not followup_response.content or 'SYNTHETIC_MARKER_483' not in followup_response.content:
        raise RuntimeError('Model did not preserve the synthetic tool marker')
    return {'layout': 'anchored' if anchored else 'original',
            'initial': initial_response.usage.to_dict() if initial_response.usage else None,
            'followup': followup_response.usage.to_dict() if followup_response.usage else None,
            'client_prefix_identical': followup.to_request()[0][:len(initial.to_request()[0])] == initial.to_request()[0]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Send paid DeepSeek requests')
    if not parser.parse_args().live:
        print('live_request_context_prefix=not_run pass --live to make model requests')
        return
    load_dotenv()
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise RuntimeError('DEEPSEEK_API_KEY is not configured')
    provider = DeepSeekProvider(key, model='deepseek-flash', max_retries=0, timeout_seconds=90,
                                reasoning_effort='low')
    try:
        print(json.dumps([experiment(provider, False), experiment(provider, True)]))
    finally:
        provider.client.close()


if __name__ == '__main__':
    main()
