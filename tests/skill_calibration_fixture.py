"""Isolated real SQLite/files for calibration UI; controlled SDKs are not quality evidence."""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

from PIL import Image
from sqlalchemy import func, select

from creatoros.integrations.producer_skills import _digest
from creatoros.integrations.skill_extraction import DraftSkill, ExtractionResult
from creatoros.storage import ContentRepository, ContentRun, ContentRevision, ContentAttempt, Series, Topic, SkillCalibration
from creatoros.web.chat_image_actions import ImportRequest, import_pack
from tests.smoke_skill_workbench import TrialProducer


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob('*')) if p.is_file()}


async def controlled_calibration_revise(directory, images, mode, instruction, cancel, on_thread, current_skills):
    on_thread("controlled-calibration-revision-" + uuid4().hex)
    old = current_skills[0]
    return ExtractionResult(note="[受控模型边界] 校准接线，不评价创作质量。" + "这里验证长说明默认折叠、完整保留，不应把草稿推到页面底部。" * 7,
        suggested_topic="一次小小的改变，用开场道具收尾。",
        skills=[DraftSkill(name=old['name'], role=old['role'], output_kind=old.get('output_kind', 'image-carousel'),
                           skill_md=old['skill_md'] + '\n\n受控校准改稿：用开场的道具完成一个具体行动。\n')])


class ControlledCalibrationProducer(TrialProducer):
    def produce_to(self, **request):
        from creatoros.integrations.native_production import Checkpoint, Delivery, Artifact, SavedArtifact
        from creatoros.integrations.producer_skills import freeze_skill
        from creatoros.integrations.visual_production import input_digest
        thread_id = 'controlled-calibration-trial-' + uuid4().hex
        request['on_thread_started'](thread_id)
        pack = super().produce_to(**request)
        directory = Path(request['directory'])
        composition = request.get('composition')
        if composition:
            versions = {role: getattr(composition, role) for role in ('mind', 'production')}
            for role, value in versions.items():
                freeze_skill(Path(value.local_path), directory / 'skills' / role, value.digest)
        else:
            freeze_skill(Path(request['skill_directory']), directory / 'skills/single', request['skill_digest'])
        refs = [(p.parent.name, p) for p in sorted((directory / 'skills').glob('*/SKILL.md'))]
        prompt = '[受控native边界] ' + request['topic_title']
        (directory / 'production_request.txt').write_text(prompt, encoding='utf-8')
        image = directory / pack.cards[0].image_path
        raw = image.read_bytes()
        partial = directory / 'partial-images/01.png'
        partial.parent.mkdir()
        partial.write_bytes(raw)
        artifact = Artifact(order=1, source_image_path=str(image.resolve()), image_prompt='受控接线图片')
        page = SavedArtifact(**artifact.model_dump(), image_path=str(partial.resolve()),
                             sha256=hashlib.sha256(raw).hexdigest())
        checkpoint = Checkpoint(input_digest=input_digest(prompt, refs), thread_id=thread_id,
            skill_digests={role: _digest(path.parent) for role, path in refs}, pages=[page],
            delivery=Delivery(title=pack.publish_copy.title, text=pack.publish_copy.body,
                              complete=True, artifacts=[artifact]), turn_completed=True)
        (directory / 'native_checkpoint.json').write_text(checkpoint.model_dump_json(), encoding='utf-8')
        return pack


def seed_calibration(app, root: Path, *, live=False, reference_path: Path | None = None):
    """Seed only the passed isolated app; never uses defaults/formal database."""
    key = uuid4().hex[:12]
    db, runs = app.state.database, app.state.executor.service
    catalog, extractions = app.state.skill_installs.catalog, app.state.skill_extractions
    creator, series_id = 'calibration-account-' + key, 'calibration-series-' + key
    ContentRepository(db).create_creator(creator_id=creator, display_name='[隔离验证] 校准账号')
    records = {}
    for role in ('mind', 'production'):
        source = root / 'calibration-source' / key / role
        (source / 'assets').mkdir(parents=True)
        name = 'calibration-' + role + '-' + key
        text = (f'---\nname: {name}\ndescription: 隔离故事与呈现校准\n'
                + ('creatoros-output: social-content-pack.image-carousel\n' if role == 'production' else '')
                + '---\n\n')
        if role == 'mind':
            text += '用具体事件讲故事，结尾回扣开场道具；不要用抽象道理替代人物行动。\n'
        else:
            text += '制作一张暖纸色手绘漫画，角色和呈现参考 assets/reference.png；文字清楚可读。\n'
        if reference_path:
            (source / 'assets/reference.png').write_bytes(reference_path.read_bytes())
        else:
            Image.new('RGB', (320, 420), '#dfeafa').save(source / 'assets/reference.png')
        (source / 'SKILL.md').write_text(text, encoding='utf-8')
        records[role] = catalog.register_local(source, role=role)
    with db.session() as session:
        session.add(Series(id=series_id, creator_id=creator, name='[隔离验证] 故事校准', description='', audience='',
            skill_name=None, mind_skill_id=records['mind']['id'], production_skill_id=records['production']['id']))
    imported = []
    for title, color in [('结尾变成说教', '#dfeafa'), ('开场道具在结尾回扣', '#fae8df')]:
        if reference_path:
            raw = reference_path.read_bytes()
        else:
            buf = io.BytesIO()
            Image.new('RGB', (320, 420), color).save(buf, format='PNG')
            raw = buf.getvalue()
        payload = ImportRequest(series_id=series_id, attachment_ids=['fixture'], title=title,
            body='[隔离] 已有成品导入，不代表本轮模型生成。', request_id=uuid4().hex)
        imported.append(import_pack(runs, 'isolated-calibration-' + key, payload,
            [({'mime_type': 'image/png'}, raw)]))
    if not live:
        extractions.reviser = controlled_calibration_revise
        extractions.producer_factory = ControlledCalibrationProducer
    with db.session() as session:
        revision = session.get(ContentRevision, imported[0]['revision_id'])
        artifact_digest = revision.artifact_digest
    upload = extractions.upload('reference.png', base64.b64encode(raw).decode())
    setup = dict(series_id=series_id, creator_id=creator, skill_id=records['mind']['id'],
        companion_id=records['production']['id'], run_id=imported[0]['run_id'],
        revision_id=imported[0]['revision_id'], artifact_digest=artifact_digest,
        second_run_id=imported[1]['run_id'], reference_upload=upload,
        skill_digest=records['mind']['digest'], target_path=str(catalog.locate(records['mind']['id'])),
        baseline_files=tree_hashes(catalog.locate(records['mind']['id'])),
        companion_files=tree_hashes(catalog.locate(records['production']['id'])),
        artifact_files=tree_hashes(runs.output_root), live=live)
    app.state.calibration_fixture = setup
    return setup


def calibration_evidence(app):
    setup = app.state.calibration_fixture
    db, catalog = app.state.database, app.state.skill_installs.catalog
    target, companion = catalog.locate(setup['skill_id']), catalog.locate(setup['companion_id'])
    with db.session() as session:
        counts = {model.__tablename__: session.scalar(select(func.count()).select_from(model))
                  for model in (Topic, ContentRun, ContentRevision, ContentAttempt)}
        calibrations = [dict(id=row.id, job_id=row.job_id, status=row.status,
                             base_skill_digest=row.base_skill_digest, applied_digest=row.applied_digest,
                             apply_receipt=row.apply_json)
                        for row in session.scalars(select(SkillCalibration).where(
                            SkillCalibration.series_id == setup['series_id']))]
    for item in calibrations:
        job = app.state.skill_extractions.get(item['job_id'])
        item['trials'] = job.get('trials', [])
        item['thread_id'] = job.get('thread_id')
        item['draft_digest'] = job.get('digest')
    return dict(setup=setup, counts=counts, target_digest=_digest(target), target_files=tree_hashes(target),
        companion_files=tree_hashes(companion), artifact_files=tree_hashes(app.state.executor.service.output_root),
        calibrations=calibrations)
