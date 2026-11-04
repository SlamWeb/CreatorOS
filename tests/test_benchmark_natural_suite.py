"""Natural benchmark projection and immutable subset selection tests; no model calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creatoros.evaluation.benchmark import manifest, natural_suite

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO / 'docs/agent-eval/benchmark-v2/cases.json'
REGISTERED = REPO / 'docs/agent-eval/natural-v1/cases.json'


class NaturalSuiteTests(unittest.TestCase):
    def setUp(self):
        self.parent_bytes = PARENT.read_bytes()
        self.parent = json.loads(self.parent_bytes)
        self.stack = []
        for key, value in [('PARENT_ID', self.parent['dataset_id']),
                           ('PARENT_SHA256', natural_suite.canonical_digest(self.parent))]:
            mock = patch.object(natural_suite, key, value)
            mock.start(); self.addCleanup(mock.stop)
        self.doc = natural_suite.build_document(self.parent_bytes)
        mock = patch.object(natural_suite, 'DEFINITION_SHA256', natural_suite.canonical_digest(self.doc))
        mock.start(); self.addCleanup(mock.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix='natural-benchmark-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / 'cases.json'
        self.data.write_text(json.dumps(self.doc, ensure_ascii=False), encoding='utf-8')
        self.source = self.root / 'source.py'
        self.source.write_text('VALUE = 1\n', encoding='utf-8')
        mock = patch.object(manifest, 'PROJECT_ROOT', self.root)
        mock.start(); self.addCleanup(mock.stop)
        mock = patch.object(manifest, '_git_state', return_value={'sha': 'test', 'dirty': False})
        mock.start(); self.addCleanup(mock.stop)

    def freeze(self, cases=None):
        return manifest.freeze_manifest(self.root/'batch'/'manifest.json', self.data,
            pricing={'currency':'USD'}, model_configuration={'retries':0}, case_ids=cases,
            source_paths=[self.source])

    def test_projection_changes_only_declared_prompts_and_b05_scoring(self):
        original = {x['id']:x for x in self.parent['cases']}
        projection = {x['id']:x for x in self.doc['cases']}
        self.assertEqual(len(natural_suite.NATURAL_IDS), 20)
        for identifier in projection:
            old, new = original[identifier], projection[identifier]
            if identifier not in natural_suite.NATURAL_IDS:
                self.assertEqual(new, old)
                continue
            old_user = [s['text'] for s in old['steps'] if s['kind'] == 'user']
            new_user = [s['text'] for s in new['steps'] if s['kind'] == 'user']
            self.assertNotEqual(old_user, new_user, identifier)
            self.assertEqual([s for s in old['steps'] if s['kind'] != 'user'],
                             [s for s in new['steps'] if s['kind'] != 'user'])
            self.assertEqual(new['scenario'], old['scenario'])
            self.assertEqual(new['family'], old['family'])
            self.assertEqual(new['split'], old['split'])
            self.assertEqual(new['expectations'], old['expectations'])
            self.assertEqual(new['origin']['type'], 'adversarial_variant')
            self.assertIn('not a verbatim user request', new['origin']['adaptation'])
            if identifier not in {'B05', 'B07', 'B10', 'B41'}:
                self.assertEqual(new['rubric'], old['rubric'])
                self.assertEqual(new['scoring'], old['scoring'])
                self.assertEqual(new['trajectory'], old['trajectory'])
        reuse = projection['B05']
        self.assertEqual(len(reuse['trajectory']['required']), 1)
        self.assertNotIn('required_02', [r['id'] for r in reuse['scoring']['program_checks']])
        self.assertNotIn('required_01', [r['id'] for r in reuse['scoring']['program_checks']])
        self.assertIn('允许', next(r['question'] for r in reuse['rubric'] if r['id'] == 'b05-s3'))
        self.assertNotIn('required_01', [r['id'] for r in projection['B07']['scoring']['program_checks']])
        self.assertNotIn('required_01', [r['id'] for r in projection['B10']['scoring']['program_checks']])
        self.assertNotIn('required_02', [r['id'] for r in projection['B10']['scoring']['program_checks']])
        self.assertEqual(projection['B10']['trajectory'], original['B10']['trajectory'])
        self.assertEqual(projection['B10']['rubric'], original['B10']['rubric'])
        picture = projection['B41']
        self.assertEqual(picture['trajectory'], original['B41']['trajectory'])
        self.assertEqual(picture['rubric'], original['B41']['rubric'])
        self.assertNotIn('outcome_05', [r['id'] for r in picture['scoring']['program_checks']])
        self.assertIn('outcome_04', [r['id'] for r in picture['scoring']['program_checks']])

    def test_natural_picture_allows_internal_regeneration_but_not_extra_delivery(self):
        from creatoros.evaluation.benchmark.scoring import score_case
        case = next(c for c in self.doc['cases'] if c['id'] == 'B41')
        checks = [{'id': r['id'], 'status': 'passed'} for r in case['scoring']['program_checks']]
        checks += [{'id': r, 'status': 'passed'} for r in case['scoring']['critical_checks']]
        checks.append({'id': 'outcome_05', 'status': 'failed', 'detail': 'two generation items'})
        score = score_case(case, {'checks': checks}, None)
        self.assertEqual(score['program']['status'], 'passed')
        self.assertEqual(score['critical_status'], 'needs_review')  # Missing semantic evidence, not the diagnostic.
        next(x for x in checks if x['id'] == 'outcome_04')['status'] = 'failed'
        score = score_case(case, {'checks': checks}, None)
        self.assertEqual(score['program']['status'], 'failed')
        self.assertEqual(score['critical_status'], 'failed')

    def test_natural_listing_does_not_deduct_for_missing_specific_list_tool(self):
        from creatoros.evaluation.benchmark.scoring import score_case
        for identifier in ('B05', 'B07', 'B10'):
            case = next(c for c in self.doc['cases'] if c['id'] == identifier)
            checks = [{'id': r['id'], 'status':'passed'} for r in case['scoring']['program_checks']]
            checks += [{'id':'required_01', 'status':'failed'}, {'id':'required_02','status':'failed'}]
            checks += [{'id': r, 'status': 'passed'} for r in case['scoring']['critical_checks']]
            judge = {'items':[{'id':r['id'],'pass':True,'evidence':'the complete requested list is available'}
                              for r in case['rubric']]}
            with self.subTest(case=identifier):
                score = score_case(case, {'checks':checks}, judge)
                self.assertEqual(score['program']['status'], 'passed')
                self.assertEqual(score['critical_status'], 'passed')

    def test_only_explicit_natural_subset_can_freeze_and_verify(self):
        with self.assertRaisesRegex(ValueError, 'explicit'):
            self.freeze()
        with self.assertRaisesRegex(ValueError, 'non-natural'):
            self.freeze(['B05', 'B50'])
        with self.assertRaisesRegex(ValueError, 'subset'):
            self.freeze(['B05', 'B05'])
        out = self.freeze(['B05', 'B41'])
        self.assertEqual(out['selected_case_ids'], ['B05', 'B41'])
        self.assertTrue(manifest.verify_manifest(self.root/'batch'/'manifest.json')['valid'])

    def test_tampering_or_removing_identity_policy_is_rejected(self):
        for mutation in ('remove-policy', 'replace-policy', 'change-id', 'alter-case', 'remove-provenance'):
            altered = deepcopy(self.doc)
            if mutation == 'remove-policy': del altered['natural_selection_policy']
            if mutation == 'replace-policy': altered['natural_selection_policy']['eligible_case_ids'].append('B50')
            if mutation == 'change-id': altered['dataset_id'] = 'other-id'
            if mutation == 'alter-case': altered['cases'][4]['steps'][0]['text'] = 'changed'
            if mutation == 'remove-provenance': del altered['natural_provenance']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                natural_suite.validate_natural_document(altered)

    def test_original_dataset_has_no_natural_selection_restriction(self):
        self.assertFalse(natural_suite.validate_natural_document(self.parent))
        natural_suite.validate_selection(self.parent, None)
        natural_suite.validate_selection(self.parent, ['B50'])

    def test_changed_parent_definition_cannot_materialize(self):
        altered = deepcopy(self.parent)
        altered['cases'][0]['title'] += ' changed'
        with self.assertRaisesRegex(ValueError, 'exact frozen parent'):
            natural_suite.build_document(json.dumps(altered, ensure_ascii=False).encode('utf-8'))

    def test_parent_and_natural_definitions_are_portable_across_line_endings(self):
        with_crlf = json.dumps(self.parent, ensure_ascii=False, indent=2).replace('\n', '\r\n').encode('utf-8')
        reconstructed = natural_suite.build_document(with_crlf)
        self.assertEqual(natural_suite.canonical_digest(reconstructed), natural_suite.canonical_digest(self.doc))
        rendered = json.dumps(self.doc, ensure_ascii=False, indent=2).replace('\n', '\r\n')
        self.data.write_bytes(rendered.encode('utf-8'))
        out = self.freeze(['B05'])
        self.assertEqual(out['selected_case_ids'], ['B05'])
        self.assertTrue(manifest.verify_manifest(self.root/'batch'/'manifest.json')['valid'])

    def test_registered_dataset_matches_parent_projection_and_registered_digest(self):
        registered = json.loads(REGISTERED.read_text(encoding='utf-8'))
        self.assertEqual(registered, self.doc)
        self.assertEqual(natural_suite.canonical_digest(registered), natural_suite.DEFINITION_SHA256)
        self.assertTrue(natural_suite.validate_natural_document(registered))

class RegisteredNaturalSuiteTests(unittest.TestCase):
    def test_checked_in_definition_and_parent_match_pinned_hashes(self):
        parent = PARENT.read_bytes()
        registered = json.loads(REGISTERED.read_text(encoding='utf-8'))
        self.assertEqual(registered, natural_suite.build_document(parent))
        self.assertEqual(natural_suite.canonical_digest(registered), natural_suite.DEFINITION_SHA256)
        self.assertTrue(natural_suite.validate_natural_document(registered))


if __name__ == '__main__':
    unittest.main()
