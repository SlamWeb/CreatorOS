import copy
import unittest

from creatoros.evaluation.regression_catalog import ROOT, build_catalog, catalog
from creatoros.evaluation.regression_runner import prepare


class E06CatalogV7Test(unittest.TestCase):
    def test_revised_case_replaces_old_and_preserves_old_catalog(self):
        new = catalog(ROOT / 'docs/agent-eval/regression-catalog-v7/catalog.json')
        regular, full = new['suites']
        self.assertEqual((regular['counts']['defined'], full['counts']['defined']), (108, 120))
        cases = {c['instance_id']: c for c in full['cases']}
        self.assertNotIn('expansion:E06', cases)
        self.assertEqual(cases['e06-scope:E06']['adapter'], 'local-dispatch-v6')
        self.assertEqual(cases['e06-scope:E06']['execution_readiness'], 'enabled')
        old = catalog(ROOT / 'docs/agent-eval/regression-catalog-v6/catalog.json')
        self.assertIn('expansion:E06', {c['instance_id'] for c in old['suites'][1]['cases']})

    def test_catalog_cannot_self_authorize_changed_revised_source(self):
        document = copy.deepcopy(catalog(ROOT / 'docs/agent-eval/regression-catalog-v7/catalog.json')['catalog_source'])
        document['adapter_source_sha256_by_namespace']['e06-scope'] = '0' * 64
        with self.assertRaises(ValueError):
            build_catalog(document)

    def test_exclusion_must_name_existing_unique_eligible_cases(self):
        document = copy.deepcopy(catalog(ROOT / 'docs/agent-eval/regression-catalog-v7/catalog.json')['catalog_source'])
        source = next(s for s in document['sources'] if s['namespace'] == 'expansion')
        for bad in (['NOT-EXIST'], ['E06', 'E06']):
            source['exclude_case_ids'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                build_catalog(document)

    def test_default_plan_displays_all_cases_and_separate_run_groups(self):
        plan = prepare('regular-v7')
        self.assertEqual(len(plan['catalog_binding']['planned_instance_ids']), 108)
        self.assertTrue(plan['requires_separate_runs'])
        self.assertIsNone(plan['requires_local_dispatch'])
        self.assertTrue(any(g['source_namespace'] == 'e06-scope' and 'e06-scope:E06' in g['instances']
                            for g in plan['execution_groups']))
        with self.assertRaisesRegex(ValueError, 'Cannot mix'):
            prepare('regular-v7', ['base:B06', 'e06-scope:E06'])


if __name__ == '__main__':
    unittest.main()
