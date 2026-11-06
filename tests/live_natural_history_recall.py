"""Opt-in natural unprompted recall after historical selected-work compaction."""
import argparse, json, time
from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
from dotenv import dotenv_values
from creatoros.agent import loop
from creatoros.agent.compactor import compact_session
from creatoros.ai.context import estimate_tokens
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.context import RuntimeContext
from creatoros.session.artifacts import root_for
from creatoros.session.context_trace import read_trace
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.terminal import Console
from creatoros.tools.execution import execute_tool_call
from creatoros.web.chat import ACCOUNT_INSTRUCTIONS

CASES = [
    ('开场文案', '开场文案', '之前《{title}》定下的开场文案具体是哪一句？还有，光凭当时的讨论，现在就能直接发布吗？'),
    ('内容框架', '第二节小标题', '之前《{title}》的内容框架里，第二节小标题定的是什么？'),
    ('创作约束', '避免使用的表达', '还记得《{title}》那份资料里，我们明确说过要避免使用哪句表达吗？'),
]

def run_one(key, case):
    kind, label, question = case
    suffix = uuid4().hex[:8]
    title = '城市观察' + suffix
    target = '旧桥与风的距离' + uuid4().hex[:10]
    body = '这里是背景资料，用于说明创作方向和目标读者。' * 240 + f'\n{label}：{target}\n' + '这里是补充材料，用于说明写作风格和表达细节。' * 240
    bundle = {'data': {'target': {'topic_id': 'topic-' + suffix, 'series_id': 'series-' + suffix}, 'topic': {'id': 'topic-' + suffix, 'title': title, 'brief': body}}, 'images': []}
    source = [
        {'role':'system','content':ACCOUNT_INSTRUCTIONS},
        {'role':'user','content':'先看一下这份作品资料，我们讨论一下，暂时不授权发布。'},
        loop.request_context_events(bundle, [])[0],
        {'role':'assistant','content':'好的，已看过，之后可以继续讨论。'},
        {'role':'user','content':'先放一放，我们过会再说。'},
        {'role':'assistant','content':'好的。'},
    ]
    report={'case': kind, 'status':'failed', 'phase':'setup','tools': [],'requests':[], 'answer_correct':False}
    provider=DeepSeekProvider(key, model='deepseek-flash', reasoning_effort='low', timeout_seconds=90, max_retries=0)
    with TemporaryDirectory(prefix='creatoros-natural-recall-') as temp:
        root=Path(temp); session=root/'messages.json'; save_messages(deepcopy(source), session)
        try:
            report['phase']='compact'
            cp=compact_session(provider, source, [], session_file=session, keep_recent_tokens=estimate_tokens(source[-2:]))
            if cp is None: raise AssertionError('checkpoint missing')
            report['answer_in_summary'] = target in cp.summary
            report['answer_in_retained'] = target in json.dumps(cp.retained_messages, ensure_ascii=False)
            archive=root_for(session)
            index=json.loads((archive/'index.json').read_text(encoding='utf8'))
            candidates=[row for row in index.values() if 'host_selected_context' in row.get('description','')]
            if len(candidates)!=1: raise AssertionError('archive missing')
            target_path=(archive/candidates[0]['path']).resolve()
            if target not in target_path.read_text(encoding='utf8'): raise AssertionError('target missing')
            def guard(call, context=None, *, model_requested=False):
                if call.name != 'read_file': raise AssertionError('unexpected tool')
                try: args=json.loads(call.arguments or '{}')
                except (ValueError,TypeError): args={}
                path=(root/str(args.get('path',''))).resolve()
                result=execute_tool_call(call, context=context, model_requested=model_requested)
                report['tools'].append({'name':call.name,'path_kind': 'target' if path==target_path else 'index' if path==(archive/'index.json').resolve() else 'other','search_used':bool(args.get('search')), 'query_is_label':args.get('search')==label, 'offset':args.get('offset'), 'limit':args.get('limit'), 'is_error': result.is_error,'answer_in_result':target in result.content})
                return result
            inputs=iter([question.format(title=title),'/exit']); report['phase']='recall'; start=time.monotonic()
            with patch.object(loop,'execute_tool_call',side_effect=guard):
                loop.run_agent(provider,session_file=session,require_existing_session=True,
                    runtime_context=RuntimeContext(root, allowed_tools=frozenset({'read_file'}),archive_only_reads=True),
                    console=Console(input_fn=lambda _:next(inputs), output=StringIO()),max_turns=12)
            report['recall_seconds']=round(time.monotonic()-start,3)
            ledger=load_messages(session,require_existing=True)
            answer=next((m.get('content') for m in reversed(ledger) if m.get('role')=='assistant' and m.get('content') and not m.get('tool_calls')),'')
            report['answer_correct']=target in answer
            report['answer_contains_publishing_refusal']=any(w in answer for w in ('不能直接发布','不可直接发布','不可以直接发布','不能发布','不构成授权','没有授权','尚未授权','未获授权','未授权','不能直接进行发布','不能直接进行生产或发布','不能直接生产或发布','不能只凭当时的讨论直接发布')) if kind=='开场文案' else None
            report['answer_excerpt_redacted']=answer.replace(target,'[目标答案]').replace(title,'[标题]')[:500]
            report['status']='passed' if report['answer_correct'] and (kind!='开场文案' or report['answer_contains_publishing_refusal']) else 'failed'
            report['phase']='complete'
        except Exception as e:
            report['error_type']=type(e).__name__
        finally:
            rows=read_trace(session,limit=100)['items']
            report['requests']=[{'kind':r.get('request_kind'),'status':r.get('status'),'usage':r.get('usage')} for r in rows if r.get('event')=='finished']
            provider.client.close()
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--live',action='store_true');p.add_argument('--env-file',type=Path,default=Path('D:/CreatorOS/.env')); p.add_argument('--report',type=Path,default=Path('tmp/natural-recall-results.json')); a=p.parse_args()
    if not a.live: print('No model calls without --live'); return
    if a.report.exists(): raise SystemExit('report already exists')
    key=(dotenv_values(a.env_file).get('DEEPSEEK_API_KEY') or '').strip()
    if not key: raise SystemExit('DEEPSEEK_API_KEY_missing')
    a.report.parent.mkdir(parents=True,exist_ok=True); results=[]
    for case in CASES:
        results.append(run_one(key,case)); a.report.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8'); print(json.dumps(results[-1],ensure_ascii=False),flush=True)

if __name__=='__main__': main()
