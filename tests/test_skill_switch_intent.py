"""Durable switch intent and account-scoped inspection, without model calls."""
from unittest.mock import patch
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event, Thread, current_thread

from sqlalchemy import event

from creatoros.storage import (ContentRepository, Creator, Database, Series, SkillSwitchIntent,
                               TopicSource, WriteReceipt, upgrade_database)
from tests import test_skill_version_service


class SkillSwitchIntentTests(unittest.TestCase):
    setUp = test_skill_version_service.SkillVersionServiceTests.setUp
    candidate = test_skill_version_service.SkillVersionServiceTests.candidate
    switch_payload = test_skill_version_service.SkillVersionServiceTests.switch_payload
    switch = test_skill_version_service.SkillVersionServiceTests.switch

    def test_same_request_rechecks_committed_receipt_after_stale_first_read(self):
        candidate = self.candidate()
        payload = self.switch_payload(candidate)
        seen, release = Event(), Event()
        outcomes, errors = {}, {}

        def hold_first_receipt(_connection, _cursor, statement, _parameters, _context, _many):
            if current_thread().name == 'early-switch' and 'FROM WRITE_RECEIPTS' in statement.upper() and not seen.is_set():
                seen.set()
                if not release.wait(20):
                    raise RuntimeError('Timed out waiting for concurrent switch')

        def switch():
            try:
                outcomes[current_thread().name] = self.service.switch(self.fixture['series_id'], **payload)
            except Exception as error:
                errors[current_thread().name] = repr(error)

        event.listen(self.db.engine, 'after_cursor_execute', hold_first_receipt)
        early = Thread(target=switch, name='early-switch', daemon=True)
        late = Thread(target=switch, name='committing-switch', daemon=True)
        try:
            early.start()
            self.assertTrue(seen.wait(10))
            late.start()
            late.join(20)
            self.assertFalse(late.is_alive())
        finally:
            release.set()
            early.join(20)
            event.remove(self.db.engine, 'after_cursor_execute', hold_first_receipt)
        self.assertFalse(early.is_alive())
        self.assertEqual(errors, {})
        self.assertEqual(outcomes['early-switch'], outcomes['committing-switch'])
        with self.db.session() as session:
            self.assertEqual(session.query(WriteReceipt).count(), 1)
            self.assertEqual(session.get(Series, self.fixture['series_id']).revision, 2)

    def test_failed_intent_write_does_not_publish_or_switch(self):
        candidate = self.candidate()
        payload = self.switch_payload(candidate)

        def reject_intent(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith('INSERT INTO SKILL_SWITCH_INTENTS'):
                raise RuntimeError('intent write rejected')

        event.listen(self.db.engine, 'before_cursor_execute', reject_intent)
        try:
            with self.assertRaisesRegex(RuntimeError, 'intent write rejected'):
                self.switch(payload)
        finally:
            event.remove(self.db.engine, 'before_cursor_execute', reject_intent)
        self.assertEqual(self.catalog.describe(candidate['skill_id'])['version_status'], 'candidate')
        with self.db.session() as session:
            self.assertEqual(session.get(Series, self.fixture['series_id']).mind_skill_id, self.fixture['skill_id'])
            self.assertEqual(session.query(WriteReceipt).count(), 0)

    def test_failed_switch_commit_is_inspectable_and_exact_replay_finishes_once(self):
        candidate = self.candidate()
        payload = self.switch_payload(candidate)
        session_type = self.db._session_factory.class_

        def reject_final_commit(session):
            if any(isinstance(row, WriteReceipt) and row.response_json.get('kind') == 'skill_version'
                   for row in session.new):
                raise RuntimeError('controlled final commit failure')

        with patch.object(self.catalog, 'publish_skill_candidate', wraps=self.catalog.publish_skill_candidate) as publish:
            event.listen(session_type, 'before_commit', reject_final_commit)
            try:
                failed = self.switch(payload)
            finally:
                event.remove(session_type, 'before_commit', reject_final_commit)
            self.assertEqual(failed.status_code, 409, failed.text)
            self.assertEqual(failed.json()['error']['code'], 'studio_outcome_unknown')
            params = {'request_id': payload['request_id'], 'creator_id': self.fixture['creator_id']}
            endpoint = f"/api/series/{self.fixture['series_id']}/skill-version"
            view = self.client.get(endpoint, params=params)
            self.assertEqual(view.status_code, 200, view.text)
            self.assertEqual(view.json()['status'], 'published_not_switched')
            self.assertEqual(view.json()['skill_id'], candidate['skill_id'])
            self.assertEqual(view.json()['digest'], candidate['digest'])
            self.assertEqual(view.json()['current_skill_id'], self.fixture['skill_id'])
            other = 'switch-foreign-account'
            ContentRepository(self.db).create_creator(creator_id=other, display_name='Foreign')
            denied = self.client.get(endpoint, params={**params, 'creator_id': other})
            self.assertEqual(denied.status_code, 403)
            recovered = self.switch(payload)
            self.assertEqual(recovered.status_code, 200, recovered.text)
            self.assertEqual(recovered.json()['skill_id'], candidate['skill_id'])
            self.assertEqual(publish.call_count, 1)
            replay = self.switch(payload)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(replay.json(), recovered.json())
            self.assertEqual(publish.call_count, 1)
        after = self.client.get(endpoint, params=params)
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json()['status'], 'committed')

    def test_inspection_reports_changed_source_owner_and_revoked_use(self):
        candidate = self.candidate()
        payload = self.switch_payload(candidate)
        session_type = self.db._session_factory.class_
        def reject_final(session):
            if any(isinstance(row, WriteReceipt) and row.response_json.get('kind') == 'skill_version'
                   for row in session.new):
                raise OSError('injected final commit failure')
        event.listen(session_type, 'before_commit', reject_final)
        try:
            response = self.switch(payload)
        finally:
            event.remove(session_type, 'before_commit', reject_final)
        self.assertEqual(response.status_code, 409, response.text)
        endpoint = f"/api/series/{self.fixture['series_id']}/skill-version"
        params = {'request_id': payload['request_id'], 'creator_id': self.fixture['creator_id']}
        with patch.object(self.service.library, '_source_creator', return_value='other-account'):
            state = self.client.get(endpoint, params=params)
            self.assertEqual(state.status_code, 200, state.text)
            self.assertEqual(state.json()['status'], 'authorization_changed')
        with patch.object(self.service.library, 'can_use', return_value=False):
            state = self.client.get(endpoint, params=params)
            self.assertEqual(state.status_code, 200, state.text)
            self.assertEqual(state.json()['status'], 'authorization_changed')
        with self.db.session() as session:
            self.assertEqual(session.query(WriteReceipt).count(), 0)
            self.assertEqual(session.get(Series, self.fixture['series_id']).revision, 1)

    def test_same_request_concurrently_publishes_and_switches_once(self):
        candidate = self.candidate()
        payload = self.switch_payload(candidate)
        with patch.object(self.catalog, 'publish_skill_candidate', wraps=self.catalog.publish_skill_candidate) as publish:
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: self.service.switch(self.fixture['series_id'], **payload), range(2)))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(publish.call_count, 1)
        with self.db.session() as session:
            self.assertEqual(session.query(SkillSwitchIntent).count(), 1)
            self.assertEqual(session.query(WriteReceipt).count(), 1)
            self.assertEqual(session.get(Series, self.fixture['series_id']).mind_skill_id, candidate['skill_id'])

    def test_commit_succeeded_but_ack_lost_is_readable_without_republication(self):
        candidate = self.candidate()
        payload = self.switch_payload(candidate)
        original_factory = self.db._session_factory

        def factory():
            session = original_factory()
            commit = session.commit

            def lose_ack():
                final = any(isinstance(row, WriteReceipt) and row.response_json.get('kind') == 'skill_version'
                            for row in session.new)
                commit()
                if final:
                    raise OSError('controlled lost commit acknowledgement')
            session.commit = lose_ack
            return session

        with patch.object(self.catalog, 'publish_skill_candidate', wraps=self.catalog.publish_skill_candidate) as publish:
            with patch.object(self.db, '_session_factory', side_effect=factory):
                response = self.switch(payload)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['error']['code'], 'studio_outcome_unknown')
            state = self.service.inspect_switch(self.fixture['series_id'], payload['request_id'],
                                                creator_id=self.fixture['creator_id'])
            self.assertEqual(state['status'], 'committed')
            replay = self.switch(payload)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(replay.json()['skill_id'], candidate['skill_id'])
            self.assertEqual(publish.call_count, 1)

    def test_candidate_edit_after_intent_cannot_switch_stale_content(self):
        candidate = self.candidate()
        payload = self.switch_payload(candidate)
        session_type = self.db._session_factory.class_
        changed = []

        def mark_intent(session, _context):
            if any(isinstance(row, SkillSwitchIntent) for row in session.new):
                session.info['switch_intent_test'] = True

        def edit_after_intent(session):
            if session.info.get('switch_intent_test') and not changed:
                current = self.catalog.read_skill_file(candidate['skill_id'], 'SKILL.md')
                self.catalog.update_skill_file(candidate['skill_id'], 'SKILL.md', current['content'] + '\nNewer edit.\n',
                                               current['digest'])
                changed.append(True)

        event.listen(session_type, 'after_flush', mark_intent)
        event.listen(session_type, 'after_commit', edit_after_intent)
        try:
            response = self.switch(payload)
        finally:
            event.remove(session_type, 'after_flush', mark_intent)
            event.remove(session_type, 'after_commit', edit_after_intent)
        self.assertTrue(changed)
        self.assertEqual(response.status_code, 409, response.text)
        state = self.service.inspect_switch(self.fixture['series_id'], payload['request_id'],
                                            creator_id=self.fixture['creator_id'])
        self.assertEqual(state['status'], 'skill_changed')
        self.assertEqual(state['current_skill_id'], self.fixture['skill_id'])
        self.assertEqual(self.catalog.describe(candidate['skill_id'])['version_status'], 'candidate')

    def test_series_change_after_intent_prevents_publication(self):
        candidate = self.candidate()
        payload = self.switch_payload(candidate)
        session_type = self.db._session_factory.class_
        changed = []

        def mark_intent(session, _context):
            if any(isinstance(row, SkillSwitchIntent) for row in session.new):
                session.info['switch_intent_test'] = True

        def alter_series(session):
            if session.info.get('switch_intent_test') and not changed:
                changed.append(True)
                with self.db.session() as other:
                    other.get(Series, self.fixture['series_id']).name = 'Changed after authorization'

        event.listen(session_type, 'after_flush', mark_intent)
        event.listen(session_type, 'after_commit', alter_series)
        try:
            with patch.object(self.catalog, 'publish_skill_candidate', wraps=self.catalog.publish_skill_candidate) as publish:
                response = self.switch(payload)
        finally:
            event.remove(session_type, 'after_flush', mark_intent)
            event.remove(session_type, 'after_commit', alter_series)
        self.assertTrue(changed)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'studio_outcome_unknown')
        publish.assert_not_called()
        self.assertEqual(self.catalog.describe(candidate['skill_id'])['version_status'], 'candidate')
        state = self.service.inspect_switch(self.fixture['series_id'], payload['request_id'])
        self.assertEqual(state['status'], 'binding_changed')

    def test_account_revoked_during_publication_never_switches_silently(self):
        candidate = self.candidate()
        payload = self.switch_payload(candidate)
        original_publish = self.catalog.publish_skill_candidate

        def revoke_then_publish(*args, **kwargs):
            with self.db.session() as session:
                session.get(Creator, self.fixture['creator_id']).is_active = False
            return original_publish(*args, **kwargs)

        with patch.object(self.catalog, 'publish_skill_candidate', side_effect=revoke_then_publish):
            response = self.switch(payload)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'studio_outcome_unknown')
        self.assertEqual(self.catalog.describe(candidate['skill_id'])['version_status'], 'released')
        with self.db.session() as session:
            self.assertEqual(session.get(Series, self.fixture['series_id']).mind_skill_id, self.fixture['skill_id'])
            self.assertEqual(session.query(WriteReceipt).count(), 0)
        state = self.service.inspect_switch(self.fixture['series_id'], payload['request_id'])
        self.assertEqual(state['status'], 'authorization_changed')

    def test_transfer_after_intent_does_not_publish_or_expose_old_intent(self):
        candidate = self.candidate()
        payload = self.switch_payload(candidate)
        foreign = 'switch-transfer-account'
        ContentRepository(self.db).create_creator(creator_id=foreign, display_name='Other')
        session_type = self.db._session_factory.class_
        changed = []

        def mark_intent(session, _context):
            if any(isinstance(row, SkillSwitchIntent) for row in session.new):
                session.info['switch_intent_test'] = True

        def transfer(session):
            if session.info.get('switch_intent_test') and not changed:
                changed.append(True)
                with self.db.session() as other:
                    other.get(Series, self.fixture['series_id']).creator_id = foreign

        event.listen(session_type, 'after_flush', mark_intent)
        event.listen(session_type, 'after_commit', transfer)
        try:
            with patch.object(self.catalog, 'publish_skill_candidate', wraps=self.catalog.publish_skill_candidate) as publish:
                response = self.switch(payload)
        finally:
            event.remove(session_type, 'after_flush', mark_intent)
            event.remove(session_type, 'after_commit', transfer)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'studio_outcome_unknown')
        publish.assert_not_called()
        endpoint = f"/api/series/{self.fixture['series_id']}/skill-version"
        for owner in (self.fixture['creator_id'], foreign):
            result = self.client.get(endpoint, params={'request_id': payload['request_id'], 'creator_id': owner})
            self.assertEqual(result.status_code, 403)


class SkillSwitchMigrationTests(unittest.TestCase):
    def test_upgrade_adds_empty_intent_table_without_changing_old_rows(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'old.db'
            url = 'sqlite:///' + path.as_posix()
            upgrade_database(url, '20261027_0025')
            db = Database(url)
            repo = ContentRepository(db)
            repo.create_creator(creator_id='old-creator', display_name='Old')
            repo.create_series(series_id='old-series', creator_id='old-creator', name='Old',
                               description='', audience='', skill_name=None)
            repo.add_topic(topic_id='old-topic', series_id='old-series', title='Old', source=TopicSource.MANUAL)
            db.close()

            def old_rows():
                with closing(sqlite3.connect(path)) as connection:
                    return {name: connection.execute(f'SELECT * FROM {name} ORDER BY id').fetchall()
                            for name in ('creators', 'series', 'topics')}

            before = old_rows()
            upgrade_database(url)
            self.assertEqual(old_rows(), before)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM skill_switch_intents').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
