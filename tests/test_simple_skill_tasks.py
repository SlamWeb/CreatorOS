"""Unified task selection: real SQLite and frozen files, no model calls."""
import json
from pathlib import Path
import unittest
from sqlalchemy import func, select
from creatoros.integrations.skill_library import SkillLibraryService
from creatoros.integrations.native_production import composition_rules, TASK_SKILL_RULES
from creatoros.integrations.video_production import video_skill_rules
from creatoros.runs import ContentRunError, ContentRunService
from creatoros.runs.production_tasks import ProductionTaskService
from creatoros.runs.task_contracts import TaskExecutionSpec
from creatoros.runs.task_execution import freeze_execution_spec
from creatoros.storage import ContentRun, MediaCollection, ProductionTask, WriteReceipt
from creatoros.tools.studio import ProductionTaskArgs
from tests import test_selected_skill_run as fixture
from tests.test_staged_tasks import HeldDispatcher

class SimpleSkillTasksTests(unittest.TestCase):
    skill = fixture.SelectedSkillRunTests.skill
    def setUp(self):
        fixture.SelectedSkillRunTests.setUp(self)
        source = self.root / 'undeclared-video'
        source.mkdir()
        (source / 'SKILL.md').write_text('---\nname: edit-no-capability\ndescription: edit\n---\nEdit frames.\n', encoding='utf-8')
        self.edit = self.catalog.register_local(source, role='production')
        self.library = SkillLibraryService(self.db, self.catalog)
        self.library.add('creator',self.edit['id'],'simple-test-add')
        self.pool = self.library.set_series('single',0,[self.base['id'],self.edit['id']],{'image':[],'video':[]})
        self.runs = ContentRunService(self.db, output_root=self.root / 'outputs')
        self.tasks = ProductionTaskService(self.db,self.runs,HeldDispatcher(),library=self.library)

    def test_equal_selection_freezes_and_replays(self):
        payload = {'title':'parallel frame','kind':'image','skill_ids':[self.edit['id'],self.base['id']]}
        result = self.tasks.create('topic',request_id='simple-selection',tasks=[payload])
        self.assertEqual(result['items'][0]['status'],'queued')
        with self.db.session() as session:
            task = session.get(ProductionTask,result['items'][0]['id'])
            spec = TaskExecutionSpec.model_validate(task.definition_json['execution_spec'])
            self.assertEqual([x.role for x in spec.skills],['selected','selected'])
            self.assertEqual([x.skill_id for x in spec.skills],payload['skill_ids'])
            self.assertTrue(all(Path(x.path,'SKILL.md').is_file() for x in spec.skills))
            self.assertIn('没有预设主次', video_skill_rules(spec))
            path = self.root / 'prompt-test'
            path.mkdir()
            (path/'execution_spec.json').write_text(spec.model_dump_json(),encoding='utf-8')
            self.assertIn('没有预设主次',composition_rules(path))
            legacy = spec.model_copy(update={'skills':[spec.skills[0].model_copy(update={'role':'primary'}),spec.skills[1].model_copy(update={'role':'supporting'})]})
            (path/'execution_spec.json').write_text(legacy.model_dump_json(),encoding='utf-8')
            self.assertEqual(composition_rules(path),TASK_SKILL_RULES)
            self.assertIn('辅助不得替换主',video_skill_rules(legacy))
        replay = self.tasks.create('topic',request_id='simple-selection',tasks=[payload])
        self.assertEqual(result['items'][0]['id'],replay['items'][0]['id'])
        ProductionTaskArgs.model_validate({'title':'x','brief':'x','kind':'image','skill_ids':payload['skill_ids']})

    def test_account_skill_outside_column_is_frozen_without_changing_pool(self):
        # This family is bound to another column of the same account. The later
        # account-library contract permits one-off use without adding it here.
        before = self.library.get_series('single')
        self.assertNotIn(self.mind['family_id'], {item['family_id'] for item in before['items']})
        result = self.tasks.create('topic', request_id='simple-account-selection',
                                   tasks=[{'title': 'one-off', 'skill_ids': [self.mind['id']]}])
        run = self.runs.get(result['items'][0]['run_id'])
        chosen = run.input_snapshot_json['execution_spec']['skills']
        self.assertEqual([(item['skill_id'], item['role'], item['digest']) for item in chosen],
                         [(self.mind['id'], 'selected', self.mind['digest'])])
        self.assertTrue(Path(chosen[0]['path'], 'SKILL.md').is_file())
        self.assertEqual(self.library.get_series('single'), before)
        saved = self.library.set_series('single',self.pool['version'],[self.edit['id']],{'image':[],'video':[]})
        self.assertEqual([row['id'] for row in saved['items']],[self.edit['id']])
        self.assertTrue(Path(self.base['local_path'],'SKILL.md').is_file())
        self.assertEqual({item['family_id'] for item in saved['available_versions']},{self.edit['family_id']})

    def test_foreign_account_skill_is_rejected_without_creating_task_or_receipt(self):
        foreign = self.skill('foreign-only', 'production')
        self.repo.create_creator(creator_id='other', display_name='Other', platform=self.repo.get_creator('creator').platform)
        self.library.add('other', foreign['id'], 'foreign-account-add')
        self.assertFalse(self.library.can_use('creator', foreign['id']))
        with self.assertRaises(ContentRunError) as raised:
            self.tasks.create('topic', request_id='foreign-skill-denied',
                              tasks=[{'title': 'not authorized', 'skill_ids': [foreign['id']]}])
        self.assertEqual(raised.exception.code, 'scope_conflict')
        self.assertEqual(self.tasks.list('topic')['items'], [])
        with self.db.session() as session:
            self.assertIsNone(session.get(WriteReceipt, 'foreign-skill-denied'))
            for model in (ProductionTask, ContentRun, MediaCollection):
                self.assertEqual(session.scalar(select(func.count()).select_from(model)), 0)
        self.assertFalse((self.runs.output_root / 'staged-task-inputs').exists())

    def test_new_selection_accepts_undeclared_old_primary_still_rejects(self):
        for kind in ('image', 'video'):
            skill = {'skill_id':self.edit['id'], 'digest':self.edit['digest'], 'role':'selected'}
            selected = TaskExecutionSpec(kind=kind,skills=[skill])
            frozen = freeze_execution_spec(selected,self.catalog,self.root / ('frozen-'+kind))
            self.assertTrue(Path(frozen.skills[0].path,'SKILL.md').is_file())
            old = TaskExecutionSpec(kind=kind,skills=[{**skill,'role':'primary'}])
            with self.assertRaisesRegex(ValueError,'输出能力'):
                freeze_execution_spec(old,self.catalog,self.root / ('old-'+kind))
            old_frozen = TaskExecutionSpec(kind=kind,skills=[{**skill,'role':'primary','path':frozen.skills[0].path}])
            with self.assertRaisesRegex(ValueError,'输出能力'):
                freeze_execution_spec(old_frozen,self.catalog,self.root / ('old-frozen-'+kind),trusted_skill_roots=[self.root])

if __name__=='__main__': unittest.main()
