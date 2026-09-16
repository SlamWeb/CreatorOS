"""No paid calls: real isolated HTTP fixtures, reference outcomes and negative graders."""
from pathlib import Path
from tempfile import TemporaryDirectory

from creatoros.ai.context import ModelContext
from creatoros.ai.types import ModelUsage
from creatoros.agent.compaction import CompactionPlan
from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
from creatoros.integrations.studio import StudioClient, StudioClientError
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.runs import ContentRunService
from creatoros.web import create_app
from tests.agent_studio_support import serve
from tests.eval_operating_tasks import (_make_database, _seed, _four_candidate_batch,
    _expected_topic, operations, _guard_external_writes, LiveBudget)
from tests.operating_holdout_fixtures import customize, add_queued, history_for, background_history
from tests.operating_eval_grader import grade_preview, grade_status
from tests.operating_eval_cases import HOLDOUT


def main():
    with TemporaryDirectory(prefix='creatoros-holdout-') as tmp:
        root = Path(tmp)
        budget = LiveBudget(20, root / 'budget.json')
        budget.record(ModelUsage(10, 11, 21))
        try:
            budget.check()
            raise AssertionError('budget should stop next call')
        except RuntimeError:
            pass
        assert grade_status({'ok': True, 'review': 'needs_review'}) == 'needs_review'
        assert grade_status({'ok': False, 'review': 'needs_review'}) == 'failed'
        for case in HOLDOUT:
            directory = root / case['id']
            db = _make_database(directory)
            try:
                service = TopicResearchService(db, ProducerSkillCatalog(skills_root_for(db)))
                batch = _seed(db, service)
                customize(db, service, batch)
                other = _four_candidate_batch(service, 'series-eval-b')
                customize(db, service, other)
                if case['id'] == 'H04':
                    add_queued(db, service, batch, _expected_topic(batch, 'c1'))
                app = create_app(database=db, run_service=ContentRunService(db, output_root=directory / 'outputs'), topic_research_service=service)
                with serve(app) as base, _guard_external_writes():
                    client = StudioClient(base)
                    try:
                        history = history_for(case['id'], base, db, batch, other)
                        before = operations(db)
                        expected = [_expected_topic(batch, cid) for cid in case['selection']]
                        kwargs = dict(before_operations=before, before_topics=[], after_topics=[],
                                      calls=[{'name': 'get_topic_research'}], expected_topics=expected,
                                      series_id=batch['series_id'])
                        if case['id'] == 'H05':
                            assert service.get(batch['id'])['stale']
                            try:
                                client.request('POST', f"/api/topic-research/{batch['id']}/preview", payload={'selections': [{'candidate_id': 'c2'}]})
                                raise AssertionError('stale candidate accepted')
                            except StudioClientError:
                                pass
                        elif expected:
                            selections = [{'candidate_id': cid} for cid in case['selection']]
                            if case['id'] == 'H03':
                                selections[0]['title'] = expected[0]['title'] = '表达方式的边界'
                            client.request('POST', f"/api/topic-research/{batch['id']}/preview", payload={'selections': selections})
                        actual_case = dict(case)
                        answer = '请明确账号/栏目。' if case['id'] == 'H02' else '栏目配置已过期，无法创建预览。'
                        if case['id'] == 'H04':
                            topic = _expected_topic(batch, 'c1')
                            actual_case.update(expected_topic_id=topic['id'], expected_status='queued')
                            answer = topic['id'] + ' queued'
                        checks = grade_preview(actual_case, after_operations=operations(db), final_answer=answer, **kwargs)
                        assert grade_status(checks) == ('needs_review' if case['id'] in {'H02', 'H05'} else 'passed'), checks
                        if case['id'] == 'H04':
                            bad = grade_preview(actual_case, after_operations=operations(db), final_answer='fake-id published', **kwargs)
                            assert grade_status(bad) == 'failed'
                        if case['id'] == 'H02':
                            client.request('POST', f"/api/topic-research/{batch['id']}/preview", payload={'selections': [{'candidate_id': 'c2'}]})
                            assert grade_status(grade_preview(actual_case, after_operations=operations(db), final_answer='猜第一个账号', **kwargs)) == 'failed'
                        if case['id'] == 'H06':
                            plan = CompactionPlan.from_context(ModelContext.from_messages(history + background_history(), []), input_limit=967232, keep_recent_tokens=8000)
                            assert plan.can_compact and plan.first_retained_index >= len(history)
                    finally:
                        client.close()
            finally:
                db.close()
    print('operating_holdout_local=passed fixtures=6 reference_outcomes review_markers budget compaction_cut')


if __name__ == '__main__':
    main()
