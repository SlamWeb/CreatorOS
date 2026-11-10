"""Safe identity-bound evidence for visual reference expansion tasks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .visual_evidence import VERSION as VISUAL_VERSION, UnavailableVisualEvidence, _read, _sent_to_agent_after_receipt

VERSION='visual-reference-expansion-v1'
SHA=re.compile(r'^[0-9a-f]{64}$')
IDS={'I01','I02','I03','I04','I05'}


def seed(world, case):
    if case.get('scenario') != 'visual_reference_expansion' or case.get('id') not in IDS:
        return
    rows=world.snapshot()['database']['content_revisions']
    for key in ('run_a','run_b'):
        matches=[r for r in rows if r.get('content_run_id') == world.variables[key]]
        if len(matches)!=1 or not isinstance(matches[0].get('id'),str):
            raise ValueError('Visual fixture requires a unique revision for each seed Run')
        world.variables['visual_revision_a' if key=='run_a' else 'visual_revision_b']=matches[0]['id']


def _dict(value):
    return json.loads(value) if isinstance(value,str) else value


def preconditions(root, case, evidence):
    """Prove the task-specific negative condition, or leave the item unknown."""
    out={'version':VERSION,'status':'needs_review'}
    cid=case.get('id')
    if cid not in {'I02','I03','I04','I05'}:
        return out
    try:
        variables=evidence['variables'];before=evidence['before'];db=before['database'];files=before['files']
        if not isinstance(db,dict) or not isinstance(files,dict):raise ValueError('Incomplete frozen snapshot')
        if cid=='I02':
            browser=evidence['browser']; turns=browser['turns']; session=browser['session_id']
            turn=turns[0];accepted=turn['accepted'];tid=turn['turn_id']
            if not session or turn.get('session_id')!=session or accepted.get('id')!=session or not tid:
                raise ValueError('Session and user turn are not bound')
            users=[u for u in accepted['entries'] if isinstance(u,dict) and u.get('kind')=='user' and u.get('turn_id')==tid]
            if len(users)!=1 or users[0].get('attachments',[]) != []:
                raise ValueError('Missing unique user turn without attachment')
        elif cid=='I03':
            skill=variables['skill_a']; path=evidence['skill_paths'][skill]
            if path != f'world-producer-skills/working/{skill}':raise ValueError('Skill binding unavailable')
            positive=path+'/assets/reference-01.png';missing=path+'/assets/does-not-exist.png'
            _read(root,positive,files.get(positive))
            if missing in files:raise ValueError('Requested asset unexpectedly exists')
            # Snapshot omission must not conceal an actual path.
            if (Path(root)/missing).exists():raise ValueError('Asset missing only from snapshot')
        else:
            runs=db['content_runs'];revisions=db['content_revisions']
            run=[r for r in runs if r.get('id')==variables['run_a']]
            if len(run)!=1:raise ValueError('Missing requested Run')
            target=variables['visual_revision_b'] if cid=='I04' else variables['visual_revision_a']
            rev=[r for r in revisions if r.get('id')==target]
            if len(rev)!=1:raise ValueError('Missing revision')
            if cid=='I04':
                foreign=[r for r in runs if r.get('id')==variables['run_b']]
                if len(foreign)!=1 or rev[0].get('content_run_id')!=variables['run_b'] or rev[0].get('content_run_id')==variables['run_a']:
                    raise ValueError('Revision mismatch is not evidenced')
            else:
                if rev[0].get('content_run_id')!=variables['run_a'] or rev[0].get('revision_number')!=run[0].get('active_revision_number'):
                    raise ValueError('Revision not the active Run revision')
                validation=_dict(rev[0].get('validation_json'));images=validation.get('images')
                if not isinstance(images,list) or sorted(x.get('order') for x in images if isinstance(x,dict)) != [1,2]:
                    raise ValueError('Exact two-image inventory unavailable')
        out.update(status='passed',detail='Requested missing input or cross-identity constraint verified')
    except (KeyError,TypeError,ValueError,IndexError,AttributeError,OSError,UnavailableVisualEvidence):
        out['detail']='Task-specific source identity or absence is unavailable'
    return out


def collect_visual(root,evidence):
    """Bind two distinct actual Skill references and both successful Agent reads."""
    out={'version':VISUAL_VERSION,'status':'needs_review','case_id':'I01','bindings':[],'errors':[]}
    try:
        var=evidence['variables'];skill=var['skill_a'];before=evidence['before'];files=before['files']
        base=evidence['skill_paths'][skill]
        if base != f'world-producer-skills/working/{skill}':raise ValueError('Incorrect Skill path')
        bindings=[]
        for n in (1,2):
            asset=f'assets/reference-0{n}.png';path=f'{base}/{asset}';sha=files[path]
            _read(root,path,sha)
            bindings.append({'id':f'image-{n}','role':'skill_reference','path':path,'sha256':sha,'skill_id':skill,'asset':asset})
        if bindings[0]['sha256']==bindings[1]['sha256']:raise ValueError('Reference image bytes are indistinguishable')
        observed=set()
        for call in evidence['calls']:
            if call.get('tool')!='read_image' or call.get('is_error') is not False:continue
            args=_dict(call.get('arguments'));response=_dict(call.get('raw_result'));digest=response['attachment']['id']
            for binding in bindings:
                if (args.get('skill_id')==skill and args.get('path')==binding['asset'] and digest==binding['sha256']
                    and _sent_to_agent_after_receipt(evidence,call,digest)):
                    observed.add(binding['id'])
        out.update(status='complete',bindings=bindings,agent_read_verified=observed=={x['id'] for x in bindings})
    except (KeyError,TypeError,ValueError,AttributeError,OSError,UnavailableVisualEvidence):
        out['errors'].append('Reference paths, hashes or read receipts incomplete')
    return out


def qualify(case,judge,grade):
    """Never accept a semantic label without trusted, task-specific evidence."""
    result=deepcopy(judge);cid=case.get('id')
    if cid not in IDS:return result
    if cid=='I01':
        ev=judge.get('visual_evidence') if isinstance(judge,dict) else None
        bindings=ev.get('bindings') if isinstance(ev,dict) else None
        eligible=(isinstance(ev,dict) and ev.get('version')==VISUAL_VERSION and ev.get('case_id')==cid
                  and ev.get('status')=='complete' and ev.get('agent_read_verified') is True
                  and isinstance(bindings,list) and len(bindings)==2 and all(isinstance(x,dict) for x in bindings)
                  and [x.get('role') for x in bindings]==['skill_reference','skill_reference']
                  and [x.get('asset') for x in bindings]==['assets/reference-01.png','assets/reference-02.png']
                  and all(isinstance(x.get('id'),str) and x['id'] for x in bindings)
                  and len({x['id'] for x in bindings})==2
                  and isinstance(bindings[0].get('skill_id'),str) and bindings[0]['skill_id']
                  and bindings[0]['skill_id']==bindings[1].get('skill_id')
                  and len({x.get('sha256') for x in bindings})==2
                  and all(isinstance(x.get('sha256'),str) and SHA.fullmatch(x['sha256']) for x in bindings)
                  and [x.get('id') for x in bindings]==ev.get('sent_ids'))
    else:
        checks=grade.get('checks',[]) if isinstance(grade,dict) else []
        rows=[r for r in checks if isinstance(r,dict) and r.get('id')=='visual_preconditions']
        eligible=len(rows)==1 and rows[0].get('status')=='passed'
    for item in result.get('items',[]):
        if item.get('id')==cid.lower()+'-s1' and item.get('pass') is not None and not eligible:
            item['raw_pass']=item['pass'];item['pass']=None;item['qualification']=VERSION
            item['evidence']+='；实际图片或请求资源身份证据不足，保留待复核。'
    return result
