"""Real HTTP/tool adapters with a controlled local producer, not model E2E."""
import json
from pathlib import Path
import unittest

import httpx

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.tools import execute_tool_call
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests import test_parallel_production as fixture


class ParallelProductionApiTests(unittest.TestCase):
    setUp = fixture.ParallelProductionTests.setUp
    tearDown = fixture.ParallelProductionTests.tearDown
    make = fixture.ParallelProductionTests.make

    def test_web_and_agent_share_five_slots_and_durable_results(self):
        producer, service, executor = self.make(max_concurrency=5)
        app = create_app(database=self.database, run_service=service, run_executor=executor)
        with serve(app) as base, httpx.Client(base_url=base, trust_env=False, timeout=15) as client:
            web = client.post('/api/runs/batch', json={'topic_ids': [f'topic-{i}' for i in range(4)]})
            self.assertEqual(web.status_code, 202, web.text)
            sid = client.post('/api/agent/sessions', json={'creator_id': 'creator'}).json()['id']
            context = RuntimeContext(Path.cwd(), creator_id='creator', agent_session_id=sid, studio_url=base)
            args = json.dumps({'topic_ids': [f'topic-{i}' for i in range(4, 7)]})
            tool = execute_tool_call(ToolCall('parallel', 'start_content_run', args), context, model_requested=True)
            self.assertFalse(tool.is_error, tool.content)
            agent = json.loads(tool.content)
            self.assertEqual(agent['accepted_count'], 3)
            items = web.json()['items'] + agent['items']
            run_ids = [item['run_id'] for item in items]
            fixture.wait(lambda: len(producer.snapshot()[1]) == 5)
            views = [client.get(f'/api/runs/{rid}').json() for rid in run_ids]
            self.assertEqual(sum(view['status'] == 'producing' for view in views), 5)
            self.assertEqual(sum(view['production_queue']['status'] == 'pending' for view in views), 2)
            replay = execute_tool_call(ToolCall('parallel-replay', 'start_content_run', args), context, model_requested=True)
            self.assertFalse(replay.is_error, replay.content)
            self.assertEqual({i['run_id'] for i in json.loads(replay.content)['items']},
                             {i['run_id'] for i in agent['items']})
            producer.release_all()
            fixture.wait(lambda: all(not executor.is_submitted(rid) for rid in run_ids))
            refreshed = [client.get(f'/api/runs/{rid}').json() for rid in run_ids]
            self.assertEqual([r['status'] for r in refreshed], ['awaiting_approval'] * 7)
            self.assertEqual([r['production_queue']['status'] for r in refreshed], ['succeeded'] * 7)
            self.assertEqual(len(producer.snapshot()[0]), 7)
            self.assertEqual(producer.max_active, 5)
            for rid in run_ids:
                self.assertEqual(len(service.repository.list_attempts(service.get_active_revision(rid).id)), 1)


if __name__ == '__main__':
    unittest.main()
