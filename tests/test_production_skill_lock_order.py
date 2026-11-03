"""Controlled concurrency across real production, Skill, SQLite and files.

All fixtures are local; no model or background worker is started. The barrier
only fixes the scheduling of an otherwise possible concurrent execution.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, RLock, Thread, current_thread
from types import SimpleNamespace
import sqlite3
import traceback
import unittest
from unittest.mock import patch

from sqlalchemy import event

from creatoros.integrations.skill_version_service import SkillVersionService
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _SKILL_EDIT_LOCK, _digest
from creatoros.runs import ContentRunError
from creatoros.storage import ContentRevision, ContentRun, ContentRunStatus, Creator, ProductionTask, Series, Topic, TopicRemoval, WriteReceipt
from creatoros.storage.models import ManagedMaterial, ProductionQueueItem
from tests import test_production_tasks, test_reference_production_service, test_selected_skill_run, test_simple_skill_tasks, test_staged_tasks
from creatoros.runs import production_tasks as production_tasks_module
from creatoros.runs import submission as submission_module


class ProductionSkillLockOrderTests(unittest.TestCase):
    skill = test_selected_skill_run.SelectedSkillRunTests.skill

    def setUp(self):
        test_simple_skill_tasks.SimpleSkillTasksTests.setUp(self)

    def test_waiting_for_skill_lock_does_not_hold_sqlite_writer(self):
        held, release, attempted = Event(), Event(), Event()
        outcomes, errors = {}, {}
        original_describe = self.catalog.describe

        def holder():
            with _SKILL_EDIT_LOCK:
                held.set()
                release.wait(10)

        def observe(*args, **kwargs):
            if current_thread().name == 'production-submit':
                attempted.set()
            return original_describe(*args, **kwargs)

        def submit():
            try:
                outcomes['result'] = self.tasks.create('topic', request_id='waiting-skill-lock',
                    tasks=[{'title': 'Independent task', 'kind': 'image', 'skill_ids': [self.base['id']]}])
            except Exception:
                errors['production'] = traceback.format_exc()

        holder_thread = Thread(target=holder, name='skill-holder', daemon=True)
        production_thread = Thread(target=submit, name='production-submit', daemon=True)
        holder_thread.start()
        self.assertTrue(held.wait(5))
        try:
            with patch.object(self.catalog, 'describe', observe):
                production_thread.start()
                self.assertTrue(attempted.wait(10), 'Production did not reach Skill inspection')
                connection = sqlite3.connect(self.db.engine.url.database, timeout=0)
                try:
                    try:
                        connection.execute('BEGIN IMMEDIATE')
                    except sqlite3.OperationalError as error:
                        probe = str(error)
                    else:
                        probe = None
                        connection.rollback()
                finally:
                    connection.close()
        finally:
            release.set()
            holder_thread.join(10)
            production_thread.join(15)
        self.assertFalse(holder_thread.is_alive() or production_thread.is_alive())
        self.assertEqual(errors, {}, errors)
        self.assertIsNone(probe, f'SQLite writer was held while waiting for Skill: {probe}')
        self.assertEqual(outcomes['result']['items'][0]['status'], 'queued')

    def test_unrelated_task_submission_and_version_switch_both_finish(self):
        current = self.catalog.read_skill_file(self.mind['id'], 'SKILL.md')
        edit = self.catalog.update_skill_file(self.mind['id'], 'SKILL.md',
            current['content'] + '\nIndependent candidate.\n', current['digest'])
        candidate = self.catalog.describe(edit['skill_id'])
        versions = SkillVersionService(self.db, SimpleNamespace(lock=RLock(), catalog=self.catalog))
        switch_has_skill_lock, production_flushed = Event(), Event()
        outcomes, errors = {}, {}
        original_describe = self.catalog.describe

        def ordered_describe(*args, **kwargs):
            if current_thread().name == 'version-switch':
                # switch() has acquired the Skill lock. Give the other entry
                # a chance to reach its first DB write, if its order permits.
                switch_has_skill_lock.set()
                production_flushed.wait(2)
            return original_describe(*args, **kwargs)

        def observe_sql(_connection, _cursor, statement, _parameters, _context, _many):
            if (current_thread().name == 'production-submit'
                    and statement.lstrip().upper().startswith('INSERT INTO MEDIA_COLLECTIONS')):
                production_flushed.set()

        def submit():
            try:
                outcomes['production'] = self.tasks.create('topic', request_id='lock-order-task',
                    tasks=[{'title': 'Independent task', 'kind': 'image', 'skill_ids': [self.base['id']]}])
            except Exception:
                errors['production'] = traceback.format_exc()

        def switch():
            try:
                outcomes['switch'] = versions.switch('pair', candidate['id'], expected_revision=1,
                    request_id='lock-order-switch', expected_digest=candidate['digest'])
            except Exception:
                errors['switch'] = traceback.format_exc()

        event.listen(self.db.engine, 'after_cursor_execute', observe_sql)
        try:
            with patch.object(self.catalog, 'describe', ordered_describe):
                version_thread = Thread(target=switch, name='version-switch', daemon=True)
                production_thread = Thread(target=submit, name='production-submit', daemon=True)
                version_thread.start()
                self.assertTrue(switch_has_skill_lock.wait(10), 'Switch did not reach the shared Skill lock')
                production_thread.start()
                version_thread.join(15)
                production_thread.join(15)
        finally:
            event.remove(self.db.engine, 'after_cursor_execute', observe_sql)
        self.assertFalse(version_thread.is_alive() or production_thread.is_alive(), 'Service calls did not finish')
        self.assertEqual(errors, {}, errors)
        self.assertEqual(outcomes['production']['items'][0]['status'], 'queued')
        self.assertEqual(outcomes['switch']['skill_id'], candidate['id'])
        self.assertEqual(self.catalog.describe(candidate['id'])['version_status'], 'released')
        run_id = outcomes['production']['items'][0]['run_id']
        frozen = self.runs.get(run_id).input_snapshot_json['execution_spec']['skills']
        self.assertEqual([(item['skill_id'], item['digest']) for item in frozen],
                         [(self.base['id'], self.base['digest'])])
        with self.db.session() as session:
            self.assertEqual(session.get(Series, 'single').skill_name, self.base['id'])
            self.assertEqual(session.get(Series, 'pair').mind_skill_id, candidate['id'])
            self.assertIsNotNone(session.get(WriteReceipt, 'lock-order-task'))

    def _production_and_library_write(self, write):
        holding_skill, library_writer = Event(), Event()
        original_verify = production_tasks_module.verify_prepared_tasks
        outcomes, errors = {}, {}

        def ordered_verify(*args):
            original_verify(*args)
            holding_skill.set()
            library_writer.wait(2)

        def observe_sql(_connection, _cursor, statement, _parameters, _context, _many):
            if current_thread().name == 'library-write' and statement.strip().upper() == 'BEGIN IMMEDIATE':
                library_writer.set()

        def submit():
            try:
                outcomes['production'] = self.tasks.create('topic', request_id='library-lock-task',
                    tasks=[{'title': 'Independent', 'kind': 'image', 'skill_ids': [self.base['id']]}])
            except Exception:
                errors['production'] = traceback.format_exc()

        def library_write():
            try:
                outcomes['library'] = write()
            except Exception:
                errors['library'] = traceback.format_exc()

        event.listen(self.db.engine, 'after_cursor_execute', observe_sql)
        try:
            with patch.object(production_tasks_module, 'verify_prepared_tasks', side_effect=ordered_verify):
                first = Thread(target=submit, name='production-submit', daemon=True)
                second = Thread(target=library_write, name='library-write', daemon=True)
                first.start()
                self.assertTrue(holding_skill.wait(15), 'Production did not reach its prepared Skill check')
                second.start()
                first.join(15)
                second.join(15)
        finally:
            event.remove(self.db.engine, 'after_cursor_execute', observe_sql)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, {}, errors)
        self.assertEqual(outcomes['production']['items'][0]['status'], 'queued')

    def test_parallel_library_add_and_production_do_not_deadlock(self):
        other = self.skill('unrelated-library', 'production')
        self._production_and_library_write(lambda: self.library.add('creator', other['id'], 'library-add-lock-test'))

    def test_parallel_pool_change_and_production_do_not_deadlock(self):
        self._production_and_library_write(lambda: self.library.set_series('single', self.pool['version'],
            [self.base['id'], self.edit['id']],
            {'image': [{'skill_id': self.base['id'], 'role': 'primary'}], 'video': []}))

    def test_parallel_distinct_requests_on_same_topic_share_collection_and_positions(self):
        barrier = Barrier(2)
        original = self.tasks._prepare_tasks

        def prepare_both(*args, **kwargs):
            result = original(*args, **kwargs)
            barrier.wait(15)
            return result

        def submit(number):
            return self.tasks.create('topic', request_id=f'parallel-distinct-{number}',
                tasks=[{'title': f'Task {number}', 'kind': 'image', 'skill_ids': [self.base['id']]}])

        with patch.object(self.tasks, '_prepare_tasks', side_effect=prepare_both):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, (1, 2)))
        self.assertEqual(len({result['items'][0]['id'] for result in results}), 2)
        self.assertEqual(len({result['collection_id'] for result in results}), 1)
        listed = self.tasks.list('topic')['items']
        self.assertEqual([item['position'] for item in listed], [0, 1])
        self.assertEqual([item['status'] for item in listed], ['queued', 'queued'])
        with self.db.session() as session:
            self.assertIsNotNone(session.get(WriteReceipt, 'parallel-distinct-1'))
            self.assertIsNotNone(session.get(WriteReceipt, 'parallel-distinct-2'))
            for item in listed:
                task = session.get(ProductionTask, item['id'])
                run = session.get(ContentRun, item['run_id'])
                revision = session.query(ContentRevision).filter_by(content_run_id=run.id, revision_number=1).one()
                queue = session.query(ProductionQueueItem).filter_by(run_id=run.id).one()
                self.assertEqual(task.collection_id, results[0]['collection_id'])
                self.assertEqual(run.task_id, task.id)
                self.assertEqual(queue.revision_id, revision.id)
                self.assertEqual(queue.status, 'pending')
                self.assertEqual(run.input_snapshot_json, revision.production_input_json)
                for skill in run.input_snapshot_json['execution_spec']['skills']:
                    self.assertEqual(_digest(Path(skill['path'])), skill['digest'])
                for skill in task.definition_json['execution_spec']['skills']:
                    self.assertEqual(_digest(Path(skill['path'])), skill['digest'])

    def test_same_request_prepares_once_and_replays_when_executor_unavailable(self):
        entered, release = Event(), Event()
        original = self.tasks._prepare_tasks
        count = []
        payload = [{'title': 'Same request', 'kind': 'image', 'skill_ids': [self.base['id']]}]

        def prepare(*args, **kwargs):
            count.append(True)
            entered.set()
            self.assertTrue(release.wait(10))
            return original(*args, **kwargs)

        with patch.object(self.tasks, '_prepare_tasks', side_effect=prepare):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.tasks.create, 'topic', request_id='single-flight-task', tasks=payload)
                self.assertTrue(entered.wait(10))
                second = pool.submit(self.tasks.create, 'topic', request_id='single-flight-task', tasks=payload)
                release.set()
                results = [first.result(20), second.result(20)]
        self.assertEqual(len(count), 1)
        self.assertEqual(results[0]['items'][0]['id'], results[1]['items'][0]['id'])
        self.assertEqual(len(list(self.tasks.stages.root.iterdir())), 1)
        with patch.object(self.tasks.executor, '_readiness_error', return_value='unavailable'):
            replay = self.tasks.create('topic', request_id='single-flight-task', tasks=payload)
        self.assertEqual(replay['items'][0]['id'], results[0]['items'][0]['id'])

    def test_failed_preparation_releases_request_lock(self):
        key = (str(Path(self.db.engine.url.database).resolve()), 'single-flight-failure')
        payload = [{'title': 'One', 'kind': 'image', 'skill_ids': [self.base['id']]}]
        with patch.object(self.tasks, '_prepare_tasks', side_effect=RuntimeError('injected preparation failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected preparation failure'):
                self.tasks.create('topic', request_id='single-flight-failure', tasks=payload)
        self.assertNotIn(key, submission_module._REQUEST_LOCKS)
        result = self.tasks.create('topic', request_id='single-flight-failure', tasks=payload)
        self.assertEqual(result['items'][0]['status'], 'queued')

    def test_scope_changes_after_preparation_do_not_authorize_production(self):
        original_verify = production_tasks_module.verify_prepared_tasks
        foreign = 'scope-change-foreign'
        from creatoros.storage import ContentRepository
        ContentRepository(self.db).create_creator(creator_id=foreign, display_name='Other')
        scenarios = (
            ('disabled', Creator, 'creator', 'is_active', False),
            ('creator-name', Creator, 'creator', 'display_name', 'Changed creator'),
            ('transferred', Series, 'single', 'creator_id', foreign),
            ('edited', Topic, 'topic', 'title', 'Changed topic'),
        )
        for name, model, identifier, field, value in scenarios:
            with self.subTest(name=name):
                with self.db.session() as session:
                    before = getattr(session.get(model, identifier), field)
                def change_after_preparation(*args):
                    original_verify(*args)
                    with self.db.session() as session:
                        setattr(session.get(model, identifier), field, value)
                try:
                    with patch.object(production_tasks_module, 'verify_prepared_tasks', side_effect=change_after_preparation):
                        with self.assertRaises(ContentRunError):
                            self.tasks.create('topic', request_id=f'scope-change-{name}',
                                tasks=[{'title': 'One', 'kind': 'image', 'skill_ids': [self.base['id']]}])
                    with self.db.session() as session:
                        self.assertIsNone(session.get(WriteReceipt, f'scope-change-{name}'))
                        self.assertFalse(session.query(ProductionTask).filter_by(create_request_id=f'scope-change-{name}').first())
                finally:
                    with self.db.session() as session:
                        setattr(session.get(model, identifier), field, before)

    def test_commit_writer_serializes_concurrent_scope_changes(self):
        from creatoros.storage import ContentRepository
        foreign = 'writer-foreign-owner'
        ContentRepository(self.db).create_creator(creator_id=foreign, display_name='Other')
        for name, model, identity, field, value in (
                ('disabled', Creator, 'creator', 'is_active', False),
                ('transferred', Series, 'single', 'creator_id', foreign),
                ('edited', Topic, 'topic', 'title', 'Concurrent edit')):
            with self.subTest(name=name):
                with self.db.session() as session:
                    before = getattr(session.get(model, identity), field)
                writer_acquired, selected, release, attempted, committed = Event(), Event(), Event(), Event(), Event()
                errors = []
                def observe(_connection, _cursor, statement, _parameters, _context, _many):
                    if current_thread().name != 'commit-scope-submit':
                        return
                    upper = statement.strip().upper()
                    if upper == 'BEGIN IMMEDIATE':
                        writer_acquired.set()
                    elif writer_acquired.is_set() and 'FROM TOPICS' in upper and not selected.is_set():
                        selected.set()
                        if not release.wait(10):
                            raise RuntimeError('Timed out at scope check')
                def submit():
                    try:
                        self.tasks.create('topic', request_id=f'writer-serial-{name}', tasks=[{
                            'title': 'One', 'kind': 'image', 'skill_ids': [self.base['id']]}])
                    except ContentRunError as error:
                        # The post-commit view may race a subsequent transfer;
                        # the committed receipt remains authoritative.
                        if error.code != 'studio_outcome_unknown':
                            errors.append(traceback.format_exc())
                    except Exception:
                        errors.append(traceback.format_exc())
                def modify():
                    try:
                        with self.db.session() as session:
                            setattr(session.get(model, identity), field, value)
                            attempted.set()
                        committed.set()
                    except Exception:
                        errors.append(traceback.format_exc())
                event.listen(self.db.engine, 'after_cursor_execute', observe)
                first = Thread(target=submit, name='commit-scope-submit', daemon=True)
                second = Thread(target=modify, daemon=True)
                try:
                    first.start()
                    self.assertTrue(selected.wait(15))
                    second.start()
                    self.assertTrue(attempted.wait(10))
                    self.assertFalse(committed.wait(0.3), 'A concurrent scope change committed inside the writer')
                finally:
                    release.set()
                    first.join(15)
                    if second.ident is not None:
                        second.join(15)
                    event.remove(self.db.engine, 'after_cursor_execute', observe)
                    with self.db.session() as session:
                        setattr(session.get(model, identity), field, before)
                self.assertFalse(first.is_alive() or second.is_alive())
                self.assertEqual(errors, [], errors)
                self.assertTrue(committed.is_set())
                with self.db.session() as session:
                    self.assertIsNotNone(session.get(WriteReceipt, f'writer-serial-{name}'))

    def test_writer_uses_preparsed_skill_authority_without_catalog_reads(self):
        in_writer = Event()
        original = self.catalog._record
        session_type = self.db._session_factory.class_
        def observe_sql(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.strip().upper() == 'BEGIN IMMEDIATE':
                in_writer.set()
        def committed(_session):
            in_writer.clear()
        def guarded_record(*args, **kwargs):
            if in_writer.is_set():
                raise AssertionError('Skill catalog was read while SQLite writer was held')
            return original(*args, **kwargs)
        event.listen(self.db.engine, 'after_cursor_execute', observe_sql)
        event.listen(session_type, 'after_commit', committed)
        try:
            with patch.object(self.catalog, '_record', side_effect=guarded_record):
                result = self.tasks.create('topic', request_id='writer-no-catalog', tasks=[{
                    'title': 'One', 'kind': 'image', 'skill_ids': [self.base['id']]}])
        finally:
            event.remove(self.db.engine, 'after_cursor_execute', observe_sql)
            event.remove(session_type, 'after_commit', committed)
        self.assertEqual(result['items'][0]['status'], 'queued')


class ParallelBatchCollectionTests(unittest.TestCase):
    setUp = test_production_tasks.ProductionTaskTests.setUp
    skill = test_selected_skill_run.SelectedSkillRunTests.skill
    make = test_production_tasks.ProductionTaskTests.make

    def test_two_parallel_requests_collect_exactly_their_finished_images(self):
        from creatoros.web.artifacts import StudioArtifacts
        from creatoros.web.media_collections import MediaCollectionService
        from tests.test_production_queue import wait
        producer, runs, executor, tasks = self.make(concurrency=2)
        barrier = Barrier(2)
        prepare = tasks._prepare_tasks
        def prepare_both(*args, **kwargs):
            result = prepare(*args, **kwargs)
            barrier.wait(15)
            return result
        def submit(number):
            return tasks.create('topic', request_id=f'collected-batch-{number}', tasks=[{'title': f'Part {number}'}])
        with patch.object(tasks, '_prepare_tasks', side_effect=prepare_both):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, (1, 2)))
        collection_id = results[0]['collection_id']
        self.assertTrue(all(result['collection_id'] == collection_id for result in results))
        run_ids = {result['items'][0]['run_id'] for result in results}
        wait(lambda: producer.count_active() == 2)
        producer.release.set()
        wait(lambda: all(not executor.is_submitted(run_id) for run_id in run_ids), timeout=15)
        self.assertEqual(len(producer.requests), 2)
        collection = MediaCollectionService(self.db, StudioArtifacts(self.db, runs.output_root)).get('creator', collection_id)
        self.assertEqual({item['run_id'] for item in collection['items']}, run_ids)
        with self.db.session() as session:
            rows = list(session.query(ProductionTask).order_by(ProductionTask.position))
            self.assertEqual([row.position for row in rows], [0, 1])
            self.assertEqual({row.collection_id for row in rows}, {collection_id})
            self.assertEqual({row.task_id for row in session.query(ContentRun)}, {row.id for row in rows})
            self.assertEqual({row.status for row in session.query(ProductionQueueItem)}, {'succeeded'})


class ReferenceSkillLockOrderTests(unittest.TestCase):
    setUp = test_reference_production_service.ReferenceProductionServiceTests.setUp
    tearDown = test_reference_production_service.ReferenceProductionServiceTests.tearDown
    service = test_reference_production_service.ReferenceProductionServiceTests.service
    request = test_reference_production_service.ReferenceProductionServiceTests.request

    def test_reference_waiting_for_skill_lock_does_not_hold_sqlite_writer(self):
        body = self.request('reference-waiting-skill')
        held, release, attempted = Event(), Event(), Event()
        outcomes, errors = {}, {}
        original_describe = self.catalog.describe
        executor = self.app.state.executor

        def holder():
            with _SKILL_EDIT_LOCK:
                held.set()
                release.wait(10)

        def observe(*args, **kwargs):
            if current_thread().name == 'reference-submit':
                attempted.set()
            return original_describe(*args, **kwargs)

        def submit():
            try:
                outcomes['run'] = self.direct.submit('series', body, creator_id='creator')
            except Exception:
                errors['reference'] = traceback.format_exc()

        holder_thread = Thread(target=holder, name='skill-holder', daemon=True)
        submit_thread = Thread(target=submit, name='reference-submit', daemon=True)
        holder_thread.start()
        self.assertTrue(held.wait(5))
        try:
            with patch.object(self.catalog, 'describe', observe), \
                    patch.object(executor, '_consume', side_effect=lambda: executor._stop.wait()):
                submit_thread.start()
                self.assertTrue(attempted.wait(10), 'Reference submission did not reach Skill inspection')
                connection = sqlite3.connect(self.database.engine.url.database, timeout=0)
                try:
                    try:
                        connection.execute('BEGIN IMMEDIATE')
                    except sqlite3.OperationalError as error:
                        probe = str(error)
                    else:
                        probe = None
                        connection.rollback()
                finally:
                    connection.close()
                release.set()
                submit_thread.join(15)
        finally:
            release.set()
            holder_thread.join(10)
            submit_thread.join(15)
            self.producer.release.set()
        self.assertFalse(holder_thread.is_alive() or submit_thread.is_alive())
        self.assertEqual(errors, {}, errors)
        self.assertIsNone(probe, f'SQLite writer was held while waiting for Skill: {probe}')
        self.assertEqual(outcomes['run'].status.value, 'queued')

    def test_reference_rechecks_revoked_skill_after_initial_selection(self):
        from creatoros.delivery import task_execution
        from creatoros.storage import CreatorSkillRef
        extra = self.skills[2]
        self.library.add('creator', extra['id'], 'reference-extra-authority')
        with self.database.session() as session:
            version = session.get(CreatorSkillRef, 'creator').version
        body = self.request('reference-revoked-skill').model_copy(update={'skill_ids': [extra['id']]})
        entered, release = Event(), Event()
        errors = []
        freeze = task_execution.freeze_skill
        def pause(*args, **kwargs):
            if current_thread().name == 'reference-revoke' and not entered.is_set():
                entered.set()
                if not release.wait(15):
                    raise RuntimeError('Timed out waiting for revocation')
            return freeze(*args, **kwargs)
        def submit():
            try:
                self.direct.submit('series', body, creator_id='creator')
            except Exception as error:
                errors.append(error)
        executor = self.app.state.executor
        thread = Thread(target=submit, name='reference-revoke', daemon=True)
        try:
            with patch.object(task_execution, 'freeze_skill', side_effect=pause), \
                    patch.object(executor, '_consume', side_effect=lambda: executor._stop.wait()):
                thread.start()
                self.assertTrue(entered.wait(15))
                self.library.remove('creator', extra['id'], version)
                release.set()
                thread.join(20)
        finally:
            release.set()
            thread.join(20)
            self.producer.release.set()
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ContentRunError)
        self.assertEqual(errors[0].code, 'scope_conflict')
        with self.database.session() as session:
            self.assertIsNone(session.get(WriteReceipt, body.request_id))


class DirectRunAuthorizationTests(unittest.TestCase):
    setUp = test_selected_skill_run.SelectedSkillRunTests.setUp
    skill = test_selected_skill_run.SelectedSkillRunTests.skill
    selection = test_selected_skill_run.SelectedSkillRunTests.selection
    candidate = test_selected_skill_run.SelectedSkillRunTests.candidate

    def test_deactivation_while_waiting_for_skill_rejects_direct_and_fresh(self):
        for mode in ('direct', 'fresh'):
            with self.subTest(mode=mode):
                source = None
                if mode == 'fresh':
                    source = self.runs.create('topic')
                    with self.db.session() as session:
                        session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
                    source = self.runs.get(source.id)
                held, attempting, release = Event(), Event(), Event()
                outcomes = {}
                describe = ProducerSkillCatalog.describe
                def holder():
                    with _SKILL_EDIT_LOCK:
                        held.set()
                        release.wait(15)
                def observed(catalog, *args, **kwargs):
                    if current_thread().name == 'direct-run-submit':
                        attempting.set()
                    return describe(catalog, *args, **kwargs)
                def submit():
                    try:
                        if source is None:
                            self.runs.create('topic', request_id='direct-scope', skill_overrides=self.selection(self.base))
                        else:
                            self.runs.restart_fresh(source.id, expected_version=source.version,
                                request_id='fresh-scope', skill_overrides=self.selection(self.base))
                        outcomes['accepted'] = True
                    except Exception as error:
                        outcomes['error'] = error
                first = Thread(target=holder, daemon=True)
                second = Thread(target=submit, name='direct-run-submit', daemon=True)
                first.start()
                self.assertTrue(held.wait(5))
                try:
                    with patch.object(ProducerSkillCatalog, 'describe', new=observed):
                        second.start()
                        self.assertTrue(attempting.wait(10))
                        with self.db.session() as session:
                            session.get(Creator, 'creator').is_active = False
                        release.set()
                        second.join(15)
                finally:
                    release.set()
                    first.join(10)
                    second.join(15)
                self.assertFalse(second.is_alive())
                self.assertIsInstance(outcomes.get('error'), ContentRunError, outcomes)
                with self.db.session() as session:
                    self.assertEqual(session.query(ContentRun).count(), 1 if source else 0)
                    session.get(Creator, 'creator').is_active = True

    def test_default_single_and_pair_remain_frozen_after_source_edit(self):
        for topic, skills in (('topic', (self.base,)), ('pair-topic', (self.mind, self.visual))):
            with self.subTest(topic=topic):
                run = self.runs.create(topic)
                before = run.input_snapshot_json
                self.assertTrue(before['skill_snapshot_frozen'])
                for skill in skills:
                    source = self.catalog.locate(skill['id']) / 'SKILL.md'
                    source.write_text(source.read_text(encoding='utf-8') + '\nChanged later.\n', encoding='utf-8')
                attempt = self.runs._begin_attempt(run.id, owner_id='frozen-default')
                result = attempt['input']
                if len(skills) == 1:
                    self.assertEqual(result.skill_digest, skills[0]['digest'])
                    self.assertEqual(_digest(Path(result.skill_path)), skills[0]['digest'])
                else:
                    self.assertEqual(result.composition.mind.digest, skills[0]['digest'])
                    self.assertEqual(result.composition.production.digest, skills[1]['digest'])
                    frozen = self.runs.output_root / result.creator_id / result.series_id / run.id / 'skill-snapshot'
                    self.assertEqual(_digest(frozen / 'skills' / 'mind'), skills[0]['digest'])
                    self.assertEqual(_digest(frozen / 'skills' / 'production'), skills[1]['digest'])

    def test_concurrent_same_fresh_request_returns_one_run(self):
        source = self.runs.create('topic')
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
        source = self.runs.get(source.id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.runs.restart_fresh(source.id, expected_version=source.version,
                request_id='same-direct-fresh'), range(2)))
        self.assertEqual(results[0].id, results[1].id)
        with self.db.session() as session:
            self.assertEqual(session.query(ContentRun).count(), 2)

    def test_different_fresh_requests_do_not_create_two_runs_from_one_source(self):
        source = self.runs.create('topic')
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
        source = self.runs.get(source.id)
        def submit(number):
            try:
                return self.runs.restart_fresh(source.id, expected_version=source.version,
                                               request_id=f'different-fresh-{number}')
            except ContentRunError as error:
                return error
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, (1, 2)))
        self.assertEqual(sum(isinstance(result, ContentRun) for result in results), 1)
        self.assertEqual(sum(isinstance(result, ContentRunError) for result in results), 1)
        with self.db.session() as session:
            self.assertEqual(session.query(ContentRun).count(), 2)

    def test_direct_rechecks_transfer_and_removal_after_preparation(self):
        from creatoros.storage import ContentRepository
        ContentRepository(self.db).create_creator(creator_id='new-owner', display_name='Other')
        original = self.runs._create
        for mode in ('transfer', 'archive'):
            with self.subTest(mode=mode):
                def prepare_then_change(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if kwargs.get('prepare_only'):
                        with self.db.session() as session:
                            if mode == 'transfer':
                                session.get(Series, 'single').creator_id = 'new-owner'
                            else:
                                session.add(TopicRemoval(topic_id='topic', series_id='single', creator_id='creator',
                                                         request_id='direct-archive', status='archived'))
                    return result
                try:
                    with patch.object(self.runs, '_create', side_effect=prepare_then_change):
                        with self.assertRaises(ContentRunError):
                            self.runs.create('topic', request_id=f'direct-{mode}',
                                             skill_overrides=self.selection(self.base))
                    with self.db.session() as session:
                        self.assertEqual(session.query(ContentRun).count(), 0)
                finally:
                    with self.db.session() as session:
                        session.get(Series, 'single').creator_id = 'creator'
                        removed = session.get(TopicRemoval, 'topic')
                        if removed:
                            session.delete(removed)

    def test_fresh_commit_ack_loss_replays_same_id_without_second_run(self):
        source = self.runs.create('topic')
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
        source = self.runs.get(source.id)
        factory = self.db._session_factory
        session_type = factory.class_
        def mark_new_run(session, _context):
            if any(isinstance(item, ContentRun) for item in session.new):
                session.info['test_created_run'] = True
        def lose_ack_factory():
            session = factory()
            commit = session.commit
            def commit_then_raise():
                commit()
                if session.info.get('test_created_run'):
                    raise OSError('injected lost fresh commit acknowledgement')
            session.commit = commit_then_raise
            return session
        event.listen(session_type, 'after_flush', mark_new_run)
        try:
            with patch.object(self.db, '_session_factory', side_effect=lose_ack_factory):
                with self.assertRaisesRegex(OSError, 'lost fresh commit'):
                    self.runs.restart_fresh(source.id, expected_version=source.version, request_id='fresh-ack-loss')
        finally:
            event.remove(session_type, 'after_flush', mark_new_run)
        replay = self.runs.restart_fresh(source.id, expected_version=source.version, request_id='fresh-ack-loss')
        with self.db.session() as session:
            self.assertEqual(session.query(ContentRun).count(), 2)
            self.assertEqual(session.get(ContentRun, replay.id).idempotency_key, 'fresh:fresh-ack-loss')

    def test_another_service_commits_same_request_before_snapshot_changes(self):
        from creatoros.runs import ContentRunService
        from creatoros.runs.prepared_submission import PreparedRun
        candidate = self.candidate()
        selected = self.selection(candidate)
        second = ContentRunService(self.db, output_root=self.root / 'outputs')
        prepare = second._create
        prepared, release = Event(), Event()
        results, errors = [], []
        def wait_after_preparation(*args, **kwargs):
            result = prepare(*args, **kwargs)
            if kwargs.get('prepare_only') and isinstance(result, PreparedRun):
                prepared.set()
                if not release.wait(15):
                    raise RuntimeError('Timed out waiting for first request')
            return result
        def submit():
            try:
                results.append(second.create('topic', request_id='cross-service-request', skill_overrides=selected))
            except Exception as error:
                errors.append(error)
        thread = Thread(target=submit, daemon=True)
        try:
            with patch.object(second, '_create', side_effect=wait_after_preparation):
                thread.start()
                self.assertTrue(prepared.wait(15))
                first = self.runs.create('topic', request_id='cross-service-request', skill_overrides=selected)
                current = self.catalog.read_skill_file(candidate['id'], 'SKILL.md')
                self.catalog.update_skill_file(candidate['id'], 'SKILL.md', current['content'] + '\nLater edit.\n', current['digest'])
                release.set()
                thread.join(20)
        finally:
            release.set()
            thread.join(20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [], errors)
        self.assertEqual(results[0].id, first.id)
        with self.db.session() as session:
            self.assertEqual(session.query(ContentRun).count(), 1)


class VideoInputAuthorizationTests(unittest.TestCase):
    skill = test_selected_skill_run.SelectedSkillRunTests.skill
    setUp = test_staged_tasks.StagedTasksTests.setUp
    image = test_staged_tasks.StagedTasksTests.image
    video_payload = test_staged_tasks.StagedTasksTests.video_payload

    def _reject_after_prepare(self, payload, revoke, request_id):
        original = self.tasks._prepare_tasks
        frozen = []

        def prepare_then_revoke(*args, **kwargs):
            plan = original(*args, **kwargs)
            spec = plan.items[0].task.definition_json['execution_spec']
            frozen.append(Path(spec['skills'][0]['path']) / 'SKILL.md')
            revoke()
            return plan

        with patch.object(self.tasks, '_prepare_tasks', side_effect=prepare_then_revoke):
            with self.assertRaises(ContentRunError):
                self.tasks.create('topic', request_id=request_id, tasks=[payload])
        self.assertTrue(frozen and frozen[0].is_file(), 'Prepared evidence must remain available')
        with self.db.session() as session:
            self.assertIsNone(session.get(WriteReceipt, request_id))
            self.assertFalse(session.query(ProductionTask).filter_by(create_request_id=request_id).first())

    def test_deleted_input_collection_rejects_immediate_video_after_preparation(self):
        images, image = self.image()
        self.runs.execute(image['run_id'])
        source = self.collections.create('creator', name='Independent input', request_id='independent-input',
                                         run_ids=[image['run_id']])
        payload = self.video_payload(images, image)
        payload['inputs']['collection_id'] = source['id']
        payload['depends_on'] = []
        self._reject_after_prepare(payload, lambda: self.collections.delete('creator', source['id'],
            expected_version=source['version']), 'deleted-video-input')

    def test_deleted_input_collection_rejects_waiting_video_after_preparation(self):
        images, image = self.image()
        source = self.collections.create('creator', name='Future input', request_id='future-input')
        payload = self.video_payload(images, image)
        payload['inputs']['collection_id'] = source['id']
        self._reject_after_prepare(payload, lambda: self.collections.delete('creator', source['id'],
            expected_version=source['version']), 'deleted-waiting-input')

    def test_deleted_audio_material_rejects_waiting_video_after_preparation(self):
        images, image = self.image()
        source = self.collections.create('creator', name='Future input', request_id='future-audio-input')
        payload = self.video_payload(images, image)
        payload['inputs']['collection_id'] = source['id']

        def remove_material():
            with self.db.session() as session:
                session.delete(session.get(ManagedMaterial, self.audio['id']))

        self._reject_after_prepare(payload, remove_material, 'deleted-waiting-audio')

    def test_archived_source_rejects_immediate_video_after_archive_is_frozen(self):
        images = self.tasks.create('pair-topic', request_id='source-image', tasks=[{
            'title': 'Source image', 'skill_overrides': {'skill_id': self.base['id'],
                'expected_digests': {self.base['id']: self.base['digest']}}}])
        run_id = images['items'][0]['run_id']
        self.runs.execute(run_id)
        source = self.collections.create('creator', name='Independent source',
                                         request_id='archive-source-input', run_ids=[run_id])
        payload = {'title': 'Video', 'kind': 'video', 'skills': [{
            'skill_id': self.video['id'], 'digest': self.video['digest'], 'role': 'primary'}],
            'inputs': {'collection_id': source['id'], 'bgm_material_id': self.audio['id']}, 'depends_on': []}
        original = self.tasks._prepare_tasks
        frozen_archives = []

        def archive_after_prepare(*args, **kwargs):
            plan = original(*args, **kwargs)
            materials = plan.items[0].dispatch_inputs[1].materials
            frozen_archives.extend(Path(item.path) for item in materials if item.kind == 'images_zip')
            with self.db.session() as session:
                session.add(TopicRemoval(topic_id='pair-topic', series_id='pair', creator_id='creator',
                                         request_id='archive-source', status='archived'))
            return plan

        with patch.object(self.tasks, '_prepare_tasks', side_effect=archive_after_prepare):
            with self.assertRaises(ContentRunError):
                self.tasks.create('topic', request_id='video-archived-source', tasks=[payload])
        self.assertTrue(frozen_archives and all(path.is_file() for path in frozen_archives))
        with self.db.session() as session:
            self.assertIsNone(session.get(WriteReceipt, 'video-archived-source'))
            self.assertFalse(session.query(ProductionTask).filter_by(create_request_id='video-archived-source').first())

    def test_task_fresh_keeps_frozen_skill_even_if_current_copy_changes(self):
        result = self.tasks.create('topic', request_id='frozen-original', tasks=[{
            'title': 'Original', 'kind': 'image', 'skills': [{'skill_id': self.base['id'],
                'digest': self.base['digest'], 'role': 'primary'}]}])
        source = self.runs.get(result['items'][0]['run_id'])
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
        source = self.runs.get(source.id)
        current = self.catalog.locate(self.base['id']) / 'SKILL.md'
        current.write_text(current.read_text(encoding='utf-8') + '\nNewer copy.\n', encoding='utf-8')
        fresh = self.runs.restart_fresh(source.id, expected_version=source.version, request_id='frozen-fresh')
        skill = fresh.input_snapshot_json['execution_spec']['skills'][0]
        self.assertEqual(skill['digest'], self.base['digest'])
        self.assertEqual(_digest(Path(skill['path'])), self.base['digest'])
        self.assertNotEqual(_digest(self.catalog.locate(self.base['id'])), self.base['digest'])
        with self.db.session() as session:
            session.get(ProductionTask, source.task_id).topic_id = 'another'
        with self.assertRaises(ContentRunError) as moved:
            self.runs.restart_fresh(source.id, expected_version=source.version, request_id='frozen-fresh')
        self.assertEqual(moved.exception.code, 'scope_conflict')

    def test_revoked_typed_fresh_has_no_new_run_queue_or_snapshot(self):
        result = self.tasks.create('topic', request_id='typed-revoked-source', tasks=[{
            'title': 'Original', 'skills': [{'skill_id': self.base['id'],
                'digest': self.base['digest'], 'role': 'primary'}]}])
        source = self.runs.get(result['items'][0]['run_id'])
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
            session.get(Series, 'single').skill_name = self.video['id']
        source = self.runs.get(source.id)
        self.assertFalse(self.library.can_use('creator', self.base['id']))
        run_root = self.runs.output_root / 'creator' / 'single'
        before = {path.name for path in run_root.iterdir()}
        with self.db.session() as session:
            counts = (session.query(ContentRun).count(), session.query(ProductionQueueItem).count())
        with self.assertRaises(ContentRunError) as error:
            self.runs.restart_fresh(source.id, expected_version=source.version, request_id='typed-fresh-revoked')
        self.assertEqual(error.exception.code, 'scope_conflict')
        self.assertEqual({path.name for path in run_root.iterdir()}, before)
        with self.db.session() as session:
            self.assertEqual((session.query(ContentRun).count(), session.query(ProductionQueueItem).count()), counts)
            self.assertEqual(session.get(ContentRun, source.id).status, ContentRunStatus.FAILED)


class LegacySkillOwnershipTests(unittest.TestCase):
    setUp = test_simple_skill_tasks.SimpleSkillTasksTests.setUp
    skill = test_selected_skill_run.SelectedSkillRunTests.skill

    def test_legacy_overrides_enforce_single_and_pair_ownership_for_direct_and_task(self):
        from creatoros.storage import ContentRepository
        other = 'other-skill-account'
        ContentRepository(self.db).create_creator(creator_id=other, display_name='Other')
        foreign = self.skill('foreign-single', 'legacy_end_to_end')
        foreign_mind = self.skill('foreign-mind', 'mind')
        self.library.add(other, foreign['id'], 'foreign-single-add')
        self.library.add(other, foreign_mind['id'], 'foreign-mind-add')
        selections = [
            {'skill_id': foreign['id'], 'expected_digests': {foreign['id']: foreign['digest']}},
            {'mind_skill_id': foreign_mind['id'], 'expected_digests': {foreign_mind['id']: foreign_mind['digest']}},
        ]
        for index, selection in enumerate(selections):
            with self.subTest(selection=index):
                topic = 'another' if index == 0 else 'pair-topic'
                with self.assertRaises(ContentRunError) as direct:
                    self.runs.create(topic, request_id=f'foreign-direct-{index}', skill_overrides=selection)
                self.assertEqual(direct.exception.code, 'scope_conflict')
                with self.assertRaises(ContentRunError) as task:
                    self.tasks.create(topic, request_id=f'foreign-task-{index}', tasks=[{
                        'title': 'Foreign', 'skill_overrides': selection}])
                self.assertEqual(task.exception.code, 'scope_conflict')
        with self.db.session() as session:
            self.assertEqual(session.query(ContentRun).count(), 0)
            self.assertEqual(session.query(ProductionTask).count(), 0)

    def test_shared_release_is_usable_but_other_account_private_candidate_is_denied(self):
        from creatoros.storage import ContentRepository
        ContentRepository(self.db).create_creator(creator_id='other-private', display_name='Other')
        shared = self.skill('shared-owned-release', 'legacy_end_to_end')
        self.library.add('creator', shared['id'], 'shared-creator-add')
        self.library.add('other-private', shared['id'], 'shared-other-add')
        private = self.catalog.create_skill_candidate(shared['id'], shared['digest'], 'other-private-candidate',
                                                       creator_id='other-private')
        allowed = {'skill_id': shared['id'], 'expected_digests': {shared['id']: shared['digest']}}
        denied = {'skill_id': private['id'], 'expected_digests': {private['id']: private['digest']}}
        run = self.runs.create('another', request_id='shared-release-allowed', skill_overrides=allowed)
        self.assertEqual(run.input_snapshot_json['skill_name'], shared['id'])
        with self.assertRaises(ContentRunError) as error:
            self.runs.create('topic', request_id='private-candidate-denied', skill_overrides=denied)
        self.assertEqual(error.exception.code, 'scope_conflict')

    def test_previously_authorized_run_resumes_from_snapshot_after_library_change(self):
        selection = {'skill_id': self.base['id'], 'expected_digests': {self.base['id']: self.base['digest']}}
        run = self.runs.create('another', request_id='old-authorized', skill_overrides=selection)
        with self.db.session() as session:
            session.get(Series, 'single').skill_name = self.edit['id']
        self.assertFalse(self.library.can_use('creator', self.base['id']))
        attempt = self.runs._begin_attempt(run.id, owner_id='resume-old-snapshot')
        self.assertEqual(attempt['input'].skill_digest, self.base['digest'])
        self.assertEqual(_digest(Path(attempt['input'].skill_path)), self.base['digest'])

    def test_revoked_legacy_fresh_has_no_new_run_queue_or_snapshot(self):
        result = self.tasks.create('topic', request_id='legacy-revoked-source', tasks=[{
            'title': 'Original', 'skill_overrides': {'skill_id': self.base['id'],
                'expected_digests': {self.base['id']: self.base['digest']}}}])
        source = self.runs.get(result['items'][0]['run_id'])
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
            session.get(Series, 'single').skill_name = self.edit['id']
        source = self.runs.get(source.id)
        self.assertFalse(self.library.can_use('creator', self.base['id']))
        run_root = self.runs.output_root / 'creator' / 'single'
        before = {path.name for path in run_root.iterdir()}
        with self.db.session() as session:
            counts = (session.query(ContentRun).count(), session.query(ProductionQueueItem).count())
        with self.assertRaises(ContentRunError) as error:
            self.runs.restart_fresh(source.id, expected_version=source.version, request_id='legacy-fresh-revoked')
        self.assertEqual(error.exception.code, 'scope_conflict')
        self.assertEqual({path.name for path in run_root.iterdir()}, before)
        with self.db.session() as session:
            self.assertEqual((session.query(ContentRun).count(), session.query(ProductionQueueItem).count()), counts)
            self.assertEqual(session.get(ContentRun, source.id).status, ContentRunStatus.FAILED)

    def test_fresh_receipt_replays_after_revocation_without_reading_skill(self):
        from creatoros.storage import CreatorSkillRef, ContentRepository
        self.runs.skill_library = self.library
        self.library.add('creator', self.base['id'], 'fresh-retained-access')
        source = self.runs.create('another', request_id='fresh-replay-source', skill_overrides={
            'skill_id': self.base['id'], 'expected_digests': {self.base['id']: self.base['digest']}})
        with self.db.session() as session:
            session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
        source = self.runs.get(source.id)
        fresh = self.runs.restart_fresh(source.id, expected_version=source.version,
                                        request_id='fresh-replay-revoked', skill_ids=[self.base['id']])
        with self.db.session() as session:
            session.get(Series, 'single').skill_name = self.edit['id']
        self.library.set_series('single', self.pool['version'], [self.edit['id']], {'image': [], 'video': []})
        with self.db.session() as session:
            version = session.get(CreatorSkillRef, 'creator').version
        self.library.remove('creator', self.base['id'], version)
        self.assertFalse(self.library.can_use('creator', self.base['id']))
        with patch.object(ProducerSkillCatalog, 'describe', side_effect=AssertionError('replay read Skill')):
            replay = self.runs.restart_fresh(source.id, expected_version=source.version,
                                             request_id='fresh-replay-revoked', skill_ids=[self.base['id']])
        self.assertEqual(replay.id, fresh.id)
        with self.assertRaises(ContentRunError) as changed:
            self.runs.restart_fresh(source.id, expected_version=source.version,
                                    request_id='fresh-replay-revoked', skill_ids=[self.edit['id']])
        self.assertEqual(changed.exception.code, 'idempotency_conflict')
        with self.assertRaises(ContentRunError) as changed_session:
            self.runs.restart_fresh(source.id, expected_version=source.version,
                request_id='fresh-replay-revoked', skill_ids=[self.base['id']], origin_session_id='different-session')
        self.assertEqual(changed_session.exception.code, 'idempotency_conflict')
        ContentRepository(self.db).create_creator(creator_id='fresh-replay-other', display_name='Other')
        with self.db.session() as session:
            session.get(Series, 'single').creator_id = 'fresh-replay-other'
        with self.assertRaises(ContentRunError) as transferred:
            self.runs.restart_fresh(source.id, expected_version=source.version,
                                    request_id='fresh-replay-revoked', skill_ids=[self.base['id']])
        self.assertEqual(transferred.exception.code, 'scope_conflict')

    def test_revocation_during_freeze_rejects_direct_task_and_fresh(self):
        from creatoros.delivery import task_execution
        from creatoros.runs import service as run_service
        from creatoros.storage import CreatorSkillRef
        self.runs.skill_library = self.library
        for mode in ('direct', 'task', 'fresh'):
            with self.subTest(mode=mode):
                source = None
                if mode == 'fresh':
                    source = self.runs.create('another', request_id='revocation-source', skill_overrides={
                        'skill_id': self.base['id'], 'expected_digests': {self.base['id']: self.base['digest']}})
                    with self.db.session() as session:
                        session.get(ContentRun, source.id).status = ContentRunStatus.FAILED
                    source = self.runs.get(source.id)
                extra = self.skill('revocable-' + mode, 'legacy_end_to_end')
                self.library.add('creator', extra['id'], 'add-revocable-' + mode)
                with self.db.session() as session:
                    version = session.get(CreatorSkillRef, 'creator').version
                    before_runs = session.query(ContentRun).count()
                entered, release = Event(), Event()
                errors = []
                module = run_service if mode == 'direct' else task_execution
                freeze = module.freeze_skill
                def pause(*args, **kwargs):
                    if current_thread().name == 'revoked-submit' and not entered.is_set():
                        entered.set()
                        if not release.wait(15):
                            raise RuntimeError('Timed out waiting for revocation')
                    return freeze(*args, **kwargs)
                def submit():
                    try:
                        if mode == 'direct':
                            self.runs.create('another', request_id='revoke-direct', skill_overrides={
                                'skill_id': extra['id'], 'expected_digests': {extra['id']: extra['digest']}})
                        elif mode == 'task':
                            self.tasks.create('topic', request_id='revoke-task', tasks=[{
                                'title': 'One', 'skill_ids': [extra['id']]}])
                        else:
                            self.runs.restart_fresh(source.id, expected_version=source.version,
                                                    request_id='revoke-fresh', skill_ids=[extra['id']])
                    except Exception as error:
                        errors.append(error)
                thread = Thread(target=submit, name='revoked-submit', daemon=True)
                try:
                    with patch.object(module, 'freeze_skill', side_effect=pause):
                        thread.start()
                        self.assertTrue(entered.wait(15))
                        self.library.remove('creator', extra['id'], version)
                        self.assertFalse(self.library.can_use('creator', extra['id']))
                        release.set()
                        thread.join(20)
                finally:
                    release.set()
                    thread.join(20)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(errors), 1, errors)
                self.assertIsInstance(errors[0], ContentRunError)
                self.assertEqual(errors[0].code, 'scope_conflict')
                with self.db.session() as session:
                    self.assertEqual(session.query(ContentRun).count(), before_runs)
                    if mode == 'task':
                        self.assertIsNone(session.get(WriteReceipt, 'revoke-task'))

    def test_prepared_authority_matches_library_rule_for_shared_private_and_missing(self):
        from creatoros.integrations.skill_authority import prepare_skill_authority, verify_skill_authority
        from creatoros.storage import ContentRepository, CreatorSkillRef
        ContentRepository(self.db).create_creator(creator_id='other-proof', display_name='Other')
        shared = self.skill('authority-matrix', 'legacy_end_to_end')
        self.library.add('creator', shared['id'], 'matrix-shared')
        self.library.add('other-proof', shared['id'], 'matrix-other')
        own = self.catalog.create_skill_candidate(shared['id'], shared['digest'], 'own-candidate', creator_id='creator')
        foreign = self.catalog.create_skill_candidate(shared['id'], shared['digest'], 'foreign-candidate', creator_id='other-proof')
        unrelated = self.skill('authority-unrelated', 'legacy_end_to_end')
        with self.db.session() as session:
            ref = session.get(CreatorSkillRef, 'creator')
            ref.skill_ids_json = [*ref.skill_ids_json, 'missing--0000000000000000']
        for item in (shared, own, foreign, unrelated):
            with self.subTest(skill=item['id']):
                with _SKILL_EDIT_LOCK:
                    proof = prepare_skill_authority(self.db, self.library, 'creator', [item['id']])
                    self.assertEqual(proof.allowed, self.library.can_use('creator', item['id']))
                    with self.db.session() as session:
                        if proof.allowed:
                            verify_skill_authority(session, proof)
                        else:
                            with self.assertRaises(ValueError):
                                verify_skill_authority(session, proof)


if __name__ == '__main__':
    unittest.main()
