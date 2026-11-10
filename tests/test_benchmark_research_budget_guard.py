"""Controlled consumer-boundary research limits; never invoke a model/network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def worker(root):
    root = Path(root).resolve()
    os.environ['DATABASE_URL'] = 'sqlite:///' + (root / 'world/world.db').as_posix()
    os.environ['CREATOROS_EVAL_REGISTRY'] = str(root / 'registry.sqlite')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace
    import httpx
    from creatoros.integrations.codex import CodexUsage
    from creatoros.integrations.topic_research import ResearchReceipt, _write
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.evaluation.benchmark.research_budget_guard import GuardedResearcher, ResearchBudgetExceeded
    from tests.agent_studio_support import serve

    source = Path(__file__).resolve().parents[1] / 'docs/agent-eval/benchmark-v2/cases.json'
    case = next(c for c in json.loads(source.read_text(encoding='utf-8'))['cases'] if c['id'] == 'B48')
    world = BenchmarkWorld(root / 'world', case, None, dataset_path=source,
                           eval_root=root / 'eval', defer_background_start=True)
    class FakeResearcher:
        def __init__(self): self.calls = []
        def research(self, snapshot, count, instructions, workspace, cancel, *, public_observer=None):
            self.calls.append((snapshot['creator']['id'], count, str(workspace)))
            return SimpleNamespace(receipt=ResearchReceipt(candidates=[], note='fixture'),
                                   thread_id='fixture', usage=CodexUsage())
    fake = FakeResearcher()
    service = world.research
    service.root.mkdir(parents=True, exist_ok=True)
    guard = GuardedResearcher(service, fake, trial_root=world.root,
                              creator_id='bench-a', series_id='bench-english')
    service.researcher = guard
    try:
        world.content.create_series(series_id='foreign-test', creator_id='bench-b', name='foreign test',
                                    description='', audience='', skill_name=None)
        def completed(reply):
            import time
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                state = service.get(reply.json()['id'])['status']
                if state not in {'queued', 'researching'}:
                    return state
                time.sleep(0.05)
            raise TimeoutError('fixture worker did not finish')
        with serve(world.app) as base, httpx.Client(base_url=base, timeout=20, trust_env=False) as client:
            # Bypassing Agent scope through direct HTTP still cannot reach the delegate.
            foreign = client.post('/api/series/foreign-test/topic-research', json={'count': 1, 'instructions': 'foreign'})
            assert foreign.status_code == 202, foreign.text
            assert completed(foreign) == 'failed'
            assert not fake.calls and not guard.claim.exists()
            wrong_count = client.post('/api/series/bench-english/topic-research', json={'count': 2, 'instructions': 'wrong count'})
            assert wrong_count.status_code == 202, wrong_count.text
            assert completed(wrong_count) == 'failed'
            assert not fake.calls and not guard.claim.exists()
            own = client.post('/api/series/bench-english/topic-research', json={'count': 1, 'instructions': 'own'})
            assert own.status_code == 202, own.text
            own_state = completed(own)
            assert own_state == 'ready', (own_state, service.get(own.json()['id']).get('error'), fake.calls)
            assert len(fake.calls) == 1 and guard.claim.is_file()
            again = client.post('/api/series/bench-english/topic-research', json={'count': 1, 'instructions': 'another'})
            assert again.status_code == 202, again.text
            assert completed(again) == 'failed'
            assert len(fake.calls) == 1
            assert not world.app.state.background_services_started
        # A replacement wrapper and a concurrent attempt cannot regain budget.
        replacement = GuardedResearcher(service, fake, trial_root=world.root,
                                         creator_id='bench-a', series_id='bench-english')
        bid = 'b' * 32
        _write(service._path(bid), {'id': bid, 'series_id': 'bench-english', 'count': 1})
        ws = service.root / 'work' / bid / '1'
        ws.mkdir(parents=True)
        snap = service.snapshot('bench-english')
        def invoke(_):
            try: replacement.research(snap, 1, '', ws, Event())
            except ResearchBudgetExceeded: return 'blocked'
            return 'allowed'
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(invoke, range(4))) == ['blocked'] * 4
        assert len(fake.calls) == 1
        race_trial = root / 'concurrent-trial'
        race_root = race_trial / 'research'
        race_ws = race_root / 'work' / ('c' * 32) / '1'
        race_ws.mkdir(parents=True)
        race_record = {'id': 'c' * 32, 'series_id': 's', 'count': 1}
        race_service = SimpleNamespace(root=race_root, _load=lambda _: race_record,
            snapshot=lambda _: {'creator': {'id': 'a'}, 'series': {'name': 'fixture'}},
            repository=SimpleNamespace(get_series=lambda _: SimpleNamespace(creator_id='a')))
        race_fake = FakeResearcher()
        guards = [GuardedResearcher(race_service, race_fake, trial_root=race_trial, creator_id='a', series_id='s')
                  for _ in range(8)]
        def race(g):
            try:
                g.research({'creator': {'id': 'a'}, 'series': {'name': 'fixture'}}, 1, '', race_ws, Event())
                return 'allowed'
            except ResearchBudgetExceeded:
                return 'blocked'
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(race, guards))
        assert outcomes.count('allowed') == 1 and outcomes.count('blocked') == 7
        assert len(race_fake.calls) == 1
        try:
            guards[0].research({'creator': {'id': 'a'}, 'series': {'name': 'fixture'}}, 1, '', root, Event())
            raise AssertionError('outside workspace accepted')
        except (ValueError, ResearchBudgetExceeded):
            pass
        failed_trial = root / 'failed-trial'
        failed_root = failed_trial / 'research'
        failed_ws = failed_root / 'work' / ('d' * 32) / '1'
        failed_ws.mkdir(parents=True)
        failed_service = SimpleNamespace(root=failed_root,
            _load=lambda _: {'id': 'd' * 32, 'series_id': 's', 'count': 1},
            snapshot=lambda _: {'creator': {'id': 'a'}, 'series': {'name': 'fixture'}},
            repository=SimpleNamespace(get_series=lambda _: SimpleNamespace(creator_id='a')))
        class Broken:
            def research(self, *args, **kwargs):
                raise RuntimeError('simulated remote unknown')
        failed_guard = GuardedResearcher(failed_service, Broken(), trial_root=failed_trial, creator_id='a', series_id='s')
        try:
            failed_guard.research({'creator': {'id': 'a'}, 'series': {'name': 'fixture'}}, 1, '', failed_ws, Event())
            raise AssertionError('delegate failure not propagated')
        except RuntimeError as error:
            assert not isinstance(error, ResearchBudgetExceeded)
        assert failed_guard.claim.is_file()
        failed_guard = GuardedResearcher(failed_service, Broken(), trial_root=failed_trial, creator_id='a', series_id='s')
        try:
            failed_guard.research({'creator': {'id': 'a'}, 'series': {'name': 'fixture'}}, 1, '', failed_ws, Event())
            raise AssertionError('failed dispatch was retried')
        except ResearchBudgetExceeded:
            pass
        mismatch_trial = root / 'mismatch-trial'
        mismatch_root = mismatch_trial / 'research'
        mismatch_ws = mismatch_root / 'work' / ('e' * 32) / '1'
        mismatch_ws.mkdir(parents=True)
        mismatch_service = SimpleNamespace(root=mismatch_root,
            _load=lambda _: {'id': 'e' * 32, 'series_id': 's', 'count': 1},
            snapshot=lambda _: {'creator': {'id': 'a'}, 'series': {'name': 'fixture'}},
            repository=SimpleNamespace(get_series=lambda _: SimpleNamespace(creator_id='a')))
        mismatch_fake = FakeResearcher()
        mismatch_guard = GuardedResearcher(mismatch_service, mismatch_fake, trial_root=mismatch_trial, creator_id='a', series_id='s')
        for candidate in ({'creator': {'id': 'a'}, 'series': {'id': 'other', 'name': 'fixture'}},
                          {'creator': {'id': 'a'}, 'series': {'name': 'different'}}):
            try:
                mismatch_guard.research(candidate, 1, '', mismatch_ws, Event())
                raise AssertionError('mismatched snapshot accepted')
            except ResearchBudgetExceeded:
                pass
        assert not mismatch_fake.calls and not mismatch_guard.claim.exists()
        class TooMany:
            def research(self, *args, **kwargs):
                return SimpleNamespace(receipt=SimpleNamespace(candidates=[object(), object()]))
        mismatch_guard = GuardedResearcher(mismatch_service, TooMany(), trial_root=mismatch_trial, creator_id='a', series_id='s')
        try:
            mismatch_guard.research(mismatch_service.snapshot('s'), 1, '', mismatch_ws, Event())
            raise AssertionError('two candidates accepted')
        except ResearchBudgetExceeded:
            assert mismatch_guard.claim.is_file()
    finally:
        service.shutdown()
        world.database.close()


class ResearchBudgetTests(unittest.TestCase):
    def test_actual_http_and_consumer_budget(self):
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, '-m', 'tests.test_benchmark_research_budget_guard', root],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if len(sys.argv) == 2:
        worker(sys.argv[1])
    else:
        unittest.main()
