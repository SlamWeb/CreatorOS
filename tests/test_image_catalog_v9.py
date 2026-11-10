import copy
import unittest

from creatoros.evaluation.regression_catalog import ROOT, build_catalog, catalog
from creatoros.evaluation.regression_runner import prepare


class ImageCatalogV9Test(unittest.TestCase):
    def test_three_image_cases_admitted_and_historical_unchanged(self):
        new = catalog()
        self.assertEqual([s['counts']['defined'] for s in new['suites']], [108, 120])
        rows = {r['instance_id']: r for r in new['suites'][0]['cases']}
        old = catalog(ROOT / 'docs/agent-eval/regression-catalog-v8/catalog.json')
        previous = {r['instance_id']: r for r in old['suites'][0]['cases']}
        for case in ('B29', 'B36', 'B45'):
            identity = f'base:{case}'
            self.assertEqual(rows[identity]['adapter'], 'local-dispatch-v8')
            self.assertEqual(previous[identity]['execution_readiness'], 'requires_review')
        plan = prepare('regular-v9')
        self.assertTrue(plan['requires_separate_runs'])
        self.assertTrue(any(group['adapter'] == 'local-dispatch-v8' for group in plan['execution_groups']))

    def test_guard_cannot_authorize_an_unreviewed_case(self):
        source = copy.deepcopy(catalog()['catalog_source'])
        source['classifications']['base:B25'] = {'resource_class': 'routine', 'adapter': 'local-dispatch-v8', 'reason': 'test'}
        with self.assertRaises(ValueError):
            build_catalog(source)


if __name__ == '__main__':
    unittest.main()
