"""Real DeepSeek planning probe; isolated SQLite with production dispatch held.

This executable never calls Codex. It persists real plans, tasks and queue rows
so the browser probe can check planning against old misleading chat history.
"""
import argparse
import json
import os
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8796)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    root = args.root.resolve()
    if (root.exists() and not args.resume) or not root.is_relative_to(repo / 'tmp'):
        parser.error('Choose a new isolated directory below tmp')
    root.mkdir(parents=True, exist_ok=args.resume)
    os.environ['DATABASE_URL'] = f"sqlite:///{(root / 'studio.db').as_posix()}"
    os.environ['CREATOROS_PRODUCTION_CONCURRENCY'] = '30'

    import uvicorn
    from creatoros.storage import Database, ContentRepository, upgrade_database
    from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
    from creatoros.runs import ContentRunService
    from creatoros.runs.executor import ManagedRunExecutor
    from creatoros.session.snapshot import load_messages, save_messages
    from creatoros.web import create_app
    from creatoros.web.server import StudioServer

    class HeldRunExecutor(ManagedRunExecutor):
        def _consume(self):
            # Keep the actual queue and submissions, but never acquire a Run.
            self._stop.wait()

    upgrade_database(os.environ['DATABASE_URL'])
    db = Database(os.environ['DATABASE_URL'])
    if args.resume:
        runs = ContentRunService(db, output_root=root / 'outputs')
        app = create_app(database=db, run_service=runs, run_executor=HeldRunExecutor(runs),
                         studio_dist=args.dist.resolve(), chat_root=root / 'chats')
        try:
            StudioServer(uvicorn.Config(app, host='127.0.0.1', port=args.port, log_level='warning',
                access_log=False, timeout_graceful_shutdown=3)).run()
        finally:
            db.close()
        return
    catalog = ProducerSkillCatalog(skills_root_for(db))
    records = []
    for name in ('pixel-question-quick-cut--6b85e3be293b7792', 'image-beat-story--971666450140ba88'):
        source = repo / 'data' / 'producer-skills' / 'working' / name
        target = root / 'source-skills' / name
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('node_modules', '.git', '__pycache__'))
        records.append(catalog.register_local(target, role='production'))
    repository = ContentRepository(db)
    repository.create_creator(creator_id='parallel-live', display_name='QA像素')
    repository.create_series(series_id='parallel-series', creator_id='parallel-live', name='Qa',
                             description='', audience='', skill_name=None)
    runs = ContentRunService(db, output_root=root / 'outputs')
    executor = HeldRunExecutor(runs)
    app = create_app(database=db, run_service=runs, run_executor=executor,
                     studio_dist=args.dist.resolve(), chat_root=root / 'chats')
    for index, skill in enumerate(records):
        app.state.skill_library.add('parallel-live', skill['id'], f'parallel-fixture-{index}')
    app.state.skill_library.set_series('parallel-series', 0, [skill['id'] for skill in records],
        {'image': [{'skill_id': records[0]['id'], 'role': 'primary'}], 'video': []})
    view = app.state.chat.create('parallel-live')
    sid = view['id']
    session_file = root / 'chats' / sid / 'messages.json'
    messages = load_messages(session_file)
    previous = [
        {'role': 'user', 'content': '把你为什么一直摆烂的素材集，加上 image beat story，用抖音分享链接里的音乐剪辑。'},
        {'role': 'assistant', 'content': '视频还没提交：BGM 链接第五次仍未解析成功，缺少音频素材 ID，无法提交剪辑。请上传音频文件。'},
        {'role': 'user', 'content': '你不用解析音轨，只需要把链接给 Codex，他会结合 Skill 做到的。'},
        {'role': 'assistant', 'content': '我无法跳过解析。剪辑提交必须传已校验的音频素材 ID，不能直接使用分享链接。'}]
    save_messages(messages + previous, session_file)
    doc = app.state.chat._read(sid)
    doc['entries'] = [{'kind': row['role'], 'text': row['content']} for row in previous]
    doc['title'] = '旧 BGM 错误历史回归'
    app.state.chat._save(doc)
    (root / 'setup.json').write_text(json.dumps({'creator_id': 'parallel-live', 'series_id': 'parallel-series',
        'session_id': sid, 'skills': records, 'production_dispatch': 'held'}, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        StudioServer(uvicorn.Config(app, host='127.0.0.1', port=args.port, log_level='warning',
            access_log=False, timeout_graceful_shutdown=3)).run()
    finally:
        db.close()


if __name__ == '__main__':
    main()
