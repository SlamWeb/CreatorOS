"""Opt-in direct DeepSeek schema comparison; tool calls are never executed.

Run ``python -m tests.live_tool_schema_context --live``. Uses synthetic IDs and
request text only, no database, business service, filesystem state or secrets
in output. Single outcomes are observations, not a task-quality benchmark.
"""
import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

from creatoros.ai.context import ModelContext, estimate_tokens
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.context import RuntimeContext
from creatoros.tools import tool_registry, tools
from creatoros.tools.host_contract import bind_account_arguments, model_tool_schemas
from creatoros.web.chat import ACCOUNT_TOOLS


CASES = (
    ('topic', '只读查询选题 sample-topic-001 的完整制作要求，不做修改。',
     'manage_topics', {'action': 'get', 'topic_id': 'sample-topic-001'}),
    ('plan', '只读查询计划 sample-plan-001 的详细状态，不做修改。',
     'manage_plan', {'action': 'get', 'plan_id': 'sample-plan-001'}),
    ('skill', '读取 knowledge-to-carousel 这个 Skill 的 SKILL.md 正文，不做修改。',
     'get_producer_skill', {'skill_id': 'knowledge-to-carousel'}),
    ('tasks', '列出当前账号状态为 failed 的制作任务，不恢复也不修改任务。',
     'get_creator_tasks', {'statuses': ['failed']}),
)


def run():
    load_dotenv()
    key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if not key:
        raise RuntimeError('DEEPSEEK_API_KEY is not configured')
    scope = RuntimeContext(Path.cwd(), creator_id='scope-fixture', archive_only_reads=True,
                           allowed_tools=ACCOUNT_TOOLS)
    with patch('creatoros.tools.host_contract._strip_redundant_property_titles'):
        original = model_tool_schemas(tools, scope)
    reduced = model_tool_schemas(tools, scope)
    provider = DeepSeekProvider(key, model='deepseek-flash', max_retries=0, timeout_seconds=90,
                                reasoning_effort='none')
    results = []
    try:
        for index, (label, query, expected_name, required) in enumerate(CASES):
            # Alternate order to avoid attributing time/position bias to a schema.
            variants = [('original', original), ('reduced', reduced)]
            if index % 2:
                variants.reverse()
            for name, schemas in variants:
                ctx = ModelContext.from_messages([
                    {'role': 'system', 'content': '这是隔离只读测试。请选择适当工具并调用，禁止新增、修改或执行生产。'},
                    {'role': 'user', 'content': query},
                ], schemas)
                response = provider.complete(ctx)
                calls = response.tool_calls
                correct, valid = False, False
                if len(calls) == 1 and calls[0].name == expected_name:
                    try:
                        parsed = json.loads(calls[0].arguments)
                        correct = all(parsed.get(k) == v for k, v in required.items())
                        bound = bind_account_arguments(calls[0].name, calls[0].arguments, scope)
                        tool_registry[calls[0].name].parse_arguments(bound)
                        valid = True
                    except (ValueError, TypeError, KeyError):
                        pass
                results.append({'case': label, 'schema': name, 'correct': correct, 'valid': valid,
                                'call_count': len(calls), 'estimated_schema_tokens': estimate_tokens(schemas),
                                'usage': response.usage.to_dict() if response.usage else None})
    finally:
        provider.client.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item['correct'] and item['valid'] for item in results) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        parser.exit(message='No model calls made. Pass --live for the isolated probe.\n')
    raise SystemExit(run())
