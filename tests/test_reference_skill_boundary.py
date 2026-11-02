"""The reference-production entry must share normal production's Skill boundary."""
import unittest
from hashlib import sha256

import httpx

from creatoros.storage import Series
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests import test_reference_production_api as ref_fixture


class ReferenceSkillBoundaryTests(unittest.TestCase):
    setUp = ref_fixture.ReferenceProductionAPITests.setUp
    tearDown = ref_fixture.ReferenceProductionAPITests.tearDown
    service = ref_fixture.ReferenceProductionAPITests.service
    counts = ref_fixture.ReferenceProductionAPITests.counts
    upload = ref_fixture.ReferenceProductionAPITests.upload
    freeze = ref_fixture.ReferenceProductionAPITests.freeze

    def clear_old_binding(self):
        with self.database.session() as session:
            series = session.get(Series, "series")
            series.skill_name = series.mind_skill_id = series.production_skill_id = None

    def check_reference_production(self, with_default):
        self.clear_old_binding()
        producer = ref_fixture.CapturingProducer(block=True)
        app = create_app(database=self.database, run_service=self.service(producer))
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            if with_default:
                library = client.get('/api/creators/creator/skills').json()
                if not any(item['id'] == 'knowledge-to-carousel' for item in library['items']):
                    added = client.post('/api/creators/creator/skills', json={
                        'skill_id': 'knowledge-to-carousel', 'request_id': 'ref-pool-seed-01'})
                    self.assertIn(added.status_code, (200, 201), added.text)
                pool = client.get('/api/series/series/skills').json()
                updated = client.put('/api/series/series/skills', json={
                    'expected_version': pool['version'], 'skill_ids': ['knowledge-to-carousel'],
                    'defaults': {'image': [{'skill_id': 'knowledge-to-carousel', 'role': 'primary'}], 'video': []}})
                self.assertEqual(updated.status_code, 200, updated.text)
            image, original = self.upload(client)
            ref = self.freeze(client, [image])
            before = self.counts()
            body = {'reference_id': ref, 'title': '参考图制作', 'request_id': 'ref-pool-run-01'}
            try:
                result = client.post('/api/series/series/reference-production', json=body)
                self.assertEqual(result.status_code, 202 if with_default else 422, result.text)
                if not with_default:
                    self.assertEqual(self.counts(), before)
                    self.assertFalse(producer.started.is_set())
                    return
                self.assertTrue(producer.started.wait(5))
                run = result.json()
                self.assertEqual(run['input_snapshot']['execution_spec']['skills'][0]['skill_id'], 'knowledge-to-carousel')
                self.assertEqual(producer.requests[0]['task_references']['images'][0]['sha256'],
                                 sha256(original).hexdigest())
                replay = client.post('/api/series/series/reference-production', json=body)
                self.assertEqual(replay.status_code, 202, replay.text)
                self.assertEqual(replay.json()['id'], run['id'])
                self.assertEqual(len(producer.requests), 1)
                self.assertEqual(self.counts(), tuple(count + 1 for count in before))
            finally:
                producer.release.set()

    def test_missing_skill_rejects_before_topic_and_run_are_created(self):
        self.check_reference_production(False)

    def test_current_skill_pool_default_is_frozen_and_replayed_once(self):
        self.check_reference_production(True)


if __name__ == '__main__':
    unittest.main()
