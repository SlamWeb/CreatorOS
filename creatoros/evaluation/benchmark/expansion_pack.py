"""Compose the independently versioned 50-case expansion without double counting."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
SOURCES=('expansion-v2','expansion-identity','expansion-safety','expansion-materials',
         'expansion-media-v1','expansion-recovery','expansion-visual','expansion-extra',
         'expansion-extra2','expansion-extra3')


def build(root=ROOT):
    from .manifest import _dataset
    cases=[]; source_info=[]; ids=set(); families={}
    for folder in SOURCES:
        path=Path(root)/'docs'/'agent-eval'/folder/'cases.json'
        doc=_dataset(path)
        for case in doc['cases']:
            if case['id'] in ids:
                raise ValueError('duplicate expansion case id: '+case['id'])
            if case['family'] in families and families[case['family']] != case['split']:
                raise ValueError('case family crosses splits: '+case['family'])
            ids.add(case['id']);families[case['family']]=case['split'];cases.append(deepcopy(case))
        canonical=json.dumps(doc,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
        source_info.append({'dataset_id':doc['dataset_id'],'path':path.relative_to(root).as_posix(),
                            'canonical_sha256':hashlib.sha256(canonical).hexdigest(),'case_count':len(doc['cases'])})
    return {'schema_version':3,'dataset_id':'creatoros-expansion-50-v1-20261105',
            'status':'synthetic_development_unvalidated_by_humans',
            'suite':{'version':'benchmark-suite-v1','eligible_case_ids':[c['id'] for c in cases]},
            'source_suites':source_info,'split_policy':'All new cases are public development, not a blind or independent holdout.',
            'reference_policy':'Synthetic isolated tasks. Missing evidence stays unknown. Natural variants are counted separately.',
            'cases':cases}


def main():
    path=ROOT/'docs/agent-eval/expansion-50-v1/cases.json';path.parent.mkdir(parents=True,exist_ok=True)
    doc=build();path.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'path':str(path),'cases':len(doc['cases'])},ensure_ascii=False))


if __name__=='__main__':main()
