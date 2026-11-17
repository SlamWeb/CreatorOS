"""Isolated HTTP/SQLite regression for workspace removal and restoration."""
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import httpx
from creatoros.runs import ContentRunService, ContentRunError
from creatoros.storage import ContentRepository, CreatorPlatform, Database, Series, TopicSource, upgrade_database
from creatoros.storage.models import ContentRevision, ProductionQueueItem
from sqlalchemy import select
from creatoros.web.app import create_app
from tests.agent_studio_support import serve

with TemporaryDirectory() as directory:
    root = Path(directory)
    url = f"sqlite:///{(root / 'test.db').as_posix()}"
    upgrade_database(url)
    db = Database(url)
    repo = ContentRepository(db)
    repo.create_creator(creator_id='a', display_name='A', platform=CreatorPlatform.XIAOHONGSHU)
    repo.create_creator(creator_id='empty', display_name='Empty', platform=CreatorPlatform.XIAOHONGSHU)
    runs = ContentRunService(db, output_root=root / 'outputs')
    app = create_app(database=db, run_service=runs, chat_root=root / 'sessions')
    with serve(app) as base, httpx.Client(base_url=base, timeout=15, trust_env=False) as client:
        def create(name):
            res = client.post('/api/series', json={'name': name, 'creator_id': 'a', 'skill_name': 'knowledge-to-carousel', 'request_id': uuid4().hex})
            assert res.status_code == 201, res.text
            return res.json()['series']['id']
        active, retired = create('保留'), create('先移出')
        repo.add_topic(topic_id='history', series_id=retired, title='历史', source=TopicSource.MANUAL)
        historical_run = runs.create('history')  # raw queued Run is not a dispatch authorization
        mismatch = client.request('DELETE', f'/api/series/{retired}', json={'expected_revision': 1, 'expected_status': 'deleted', 'request_id': uuid4().hex})
        assert mismatch.status_code == 409 and mismatch.json()['error']['code'] == 'removal_changed', mismatch.text
        response = client.request('DELETE', f'/api/series/{retired}', json={'expected_revision': 1, 'expected_status': 'archived', 'request_id': uuid4().hex})
        assert response.status_code == 200 and response.json()['status'] == 'archived', response.text
        with db.session() as session:
            revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == historical_run.id))
            session.add(ProductionQueueItem(run_id=historical_run.id, revision_id=revision.id, expected_version=1, status='pending'))
        preview = client.get('/api/creators/a/removal').json()
        assert preview['status'] == 'blocked' and {'code': 'authorized_queue', 'count': 1} in preview['reasons'], preview
        blocked = client.post('/api/creators/a/delete', json={})
        assert blocked.status_code == 409, blocked.text
        with db.session() as session:
            session.scalar(select(ProductionQueueItem)).status = 'cancelled'
        preview = client.get('/api/creators/a/removal').json()
        assert preview['status'] == 'archived' and preview['reasons'] == [], preview
        mismatch = client.post('/api/creators/a/delete', json={'expected_status': 'deleted'})
        assert mismatch.status_code == 409 and mismatch.json()['error']['code'] == 'removal_changed', mismatch.text
        response = client.post('/api/creators/a/delete', json={'expected_status': 'archived'})
        assert response.status_code == 200 and response.json()['status'] == 'archived', response.text
        assert 'a' not in [x['id'] for x in client.get('/api/creators').json()['items']]
        assert 'a' in [x['id'] for x in client.get('/api/creators?archived=true').json()['items']]
        assert active not in [x['id'] for x in client.get('/api/series').json()]
        assert retired in [x['id'] for x in client.get('/api/series?archived=true').json()]
        assert client.get('/api/creators/a').status_code == 200
        details = client.get(f'/api/runs/{historical_run.id}')
        assert details.status_code == 200 and details.json()['workspace_archived'], details.text
        assert details.json()['allowed_actions'] == ['view'], details.text
        history = client.get('/api/runs', params={'series_id': retired}).json()['items']
        assert [item['id'] for item in history] == [historical_run.id] and history[0]['workspace_archived']
        for operation in (
            lambda: runs.request_revision(historical_run.id, '不应返工', expected_version=historical_run.version),
            lambda: runs.approve(historical_run.id, revision_id='unused', artifact_digest='unused', expected_version=historical_run.version),
            lambda: app.state.content_discussions.submit(historical_run.id, request_id=uuid4().hex,
                revision_id='unused', artifact_digest='unused', message='不应开始讨论'),
        ):
            try:
                operation()
                raise AssertionError('archived scope unexpectedly allowed a mutation')
            except ContentRunError:
                pass
        edit = client.patch('/api/topics/history', json={'title': '不应写入'})
        assert edit.status_code == 409, edit.text
        assert client.post(f'/api/series/{retired}/restore', json={}).status_code == 409
        assert client.post('/api/creators/a/restore', json={}).status_code == 200
        assert active in [x['id'] for x in client.get('/api/series').json()]
        assert retired in [x['id'] for x in client.get('/api/series?archived=true').json()]
        assert client.post(f'/api/series/{retired}/restore', json={}).status_code == 200
        assert retired in [x['id'] for x in client.get('/api/series').json()]
        restored_run = client.get(f'/api/runs/{historical_run.id}').json()
        assert not restored_run['workspace_archived'] and restored_run['status'] == historical_run.status.value
        response = client.post('/api/creators/empty/delete', json={})
        assert response.status_code == 200 and response.json()['status'] == 'deleted', response.text
        assert client.get('/api/creators/empty').status_code == 404
    db.close()
print('workspace_lifecycle=passed history=preserved raw_queued=not_authorized restore=passed')
