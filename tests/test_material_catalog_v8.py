import copy
import unittest

from creatoros.evaluation.regression_catalog import ROOT, build_catalog, catalog
from creatoros.evaluation.regression_runner import prepare


class MaterialCatalogV8Test(unittest.TestCase):
    def test_three_material_cases_are_explicitly_admitted(self):
        current = catalog(ROOT / 'docs/agent-eval/regression-catalog-v8/catalog.json')
        self.assertEqual([suite['counts']['defined'] for suite in current['suites']], [108, 120])
        cases = {case['instance_id']: case for case in current['suites'][0]['cases']}
        for case_id in ('P01', 'P02', 'P03'):
            self.assertEqual(cases[f'expansion:{case_id}']['adapter'], 'local-dispatch-v7')
            self.assertEqual(cases[f'expansion:{case_id}']['execution_readiness'], 'enabled')
        previous = catalog(ROOT / 'docs/agent-eval/regression-catalog-v7/catalog.json')
        old_cases = {case['instance_id']: case for case in previous['suites'][0]['cases']}
        self.assertTrue(all(old_cases[f'expansion:{x}']['execution_readiness'] == 'requires_review'
                            for x in ('P01', 'P02', 'P03')))

    def test_guard_does_not_authorize_unreviewed_case(self):
        document = copy.deepcopy(catalog(ROOT / 'docs/agent-eval/regression-catalog-v8/catalog.json')['catalog_source'])
        document['classifications']['expansion:E07'] = {'resource_class': 'routine', 'adapter': 'local-dispatch-v7', 'reason': 'test'}
        with self.assertRaises(ValueError):
            build_catalog(document)

    def test_plan_separates_new_material_guard(self):
        plan = prepare('regular-v8')
        self.assertTrue(plan['requires_separate_runs'])
        self.assertTrue(any(g['adapter'] == 'local-dispatch-v7' and 'expansion:P01' in g['instances']
                            for g in plan['execution_groups']))
        with self.assertRaises(ValueError):
            prepare('regular-v8', ['expansion:P01', 'base:B06'])


if __name__ == '__main__':
    unittest.main()
