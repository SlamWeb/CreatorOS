"""Expansion membership is auditable and excludes compatibility placeholders."""
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ExpansionPackTests(unittest.TestCase):
    def test_50_independent_instances_plus_20_paired_variants(self):
        from creatoros.evaluation.benchmark.expansion_pack import build
        from creatoros.evaluation.benchmark.manifest import _dataset
        from creatoros.evaluation.benchmark.natural_suite import NATURAL_IDS
        base=_dataset(ROOT/'docs/agent-eval/benchmark-v2/cases.json')
        natural=_dataset(ROOT/'docs/agent-eval/natural-v2/cases.json')
        expansion=_dataset(ROOT/'docs/agent-eval/expansion-50-v1/cases.json')
        self.assertEqual(expansion,build())
        self.assertEqual((len(base['cases']),len(NATURAL_IDS),len(expansion['cases'])),(50,20,50))
        originals={row['id']:row for row in base['cases']}
        variants={row['id']:row for row in natural['cases'] if row['id'] in NATURAL_IDS}
        self.assertEqual(set(variants),set(NATURAL_IDS))
        self.assertTrue(all(variants[k]['family']==originals[k]['family'] and variants[k]['split']==originals[k]['split'] for k in variants))
        self.assertFalse(set(originals)&{row['id'] for row in expansion['cases']})
        # Three attack inputs deliberately share a family and must not be described as three independent abilities.
        injection=[row for row in expansion['cases'] if row['id'] in {'S01','S02','S03'}]
        self.assertEqual(len({row['family'] for row in injection}),1)

    def test_program_rules_are_not_accidentally_orphaned(self):
        doc=json.loads((ROOT/'docs/agent-eval/expansion-50-v1/cases.json').read_text(encoding='utf-8'))
        for case in doc['cases']:
            with self.subTest(case=case['id']):
                declared=[r['id'] for r in case['scoring']['program_checks']]
                valid={f'outcome_{i+1:02}' for i in range(len(case['expectations']))}
                valid.update(f'required_{i+1:02}' for i in range(len(case['trajectory'].get('required',[]))))
                if case['id'] in {'I02','I03','I04','I05'}:valid.add('visual_preconditions')
                self.assertTrue(set(declared) <= valid, (case['id'],set(declared)-valid))
                self.assertTrue({f'outcome_{i+1:02}' for i in range(len(case['expectations']))} <= set(declared))

if __name__=='__main__':unittest.main()
