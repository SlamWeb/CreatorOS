"""Isolated real SQLite/files for calibration UI; controlled SDKs are not quality evidence."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from PIL import Image
from sqlalchemy import func, select

from creatoros.integrations.producer_skills import _digest
from creatoros.integrations.skill_extraction import DraftSkill, ExtractionResult
from creatoros.storage import ContentRepository, ContentRun, ContentRevision, ContentAttempt, Creator, Series, Topic, SkillCalibration
from creatoros.web.chat_image_actions import ImportRequest, import_pack
from tests.smoke_skill_workbench import TrialProducer


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob('*')) if p.is_file()}


PROTECTED_MODELS = (Creator, Series, Topic, ContentRun, ContentRevision, ContentAttempt)


def protected_records(database, identifiers=None):
    """Every stored column of the original rows, including JSON, status and timestamps."""
    with database.session() as session:
        result = {}
        for model in PROTECTED_MODELS:
            query = select(model).order_by(model.id)
            if identifiers is not None:
                query = query.where(model.id.in_(identifiers.get(model.__tablename__, {})))
            result[model.__tablename__] = {
                row.id: jsonable_encoder({column.name: getattr(row, column.name)
                                          for column in model.__table__.columns})
                for row in session.scalars(query)
            }
        return result


def add_newer_revision(runs, imported):
    """Seed a valid distinct revision without invoking a worker/model."""
    from creatoros.content import SocialContentPack
    from creatoros.runs.artifacts import validate_artifact
    with runs.database.session() as session:
        old = session.get(ContentRevision, imported["revision_id"])
        root = Path(old.artifact_directory)
        old_manifest = Path(old.manifest_path)
        old_pack = SocialContentPack.model_validate_json(old_manifest.read_text(encoding="utf-8"))
        for card, story in zip(old_pack.cards, ("拿出蛋糕准备比赛。", "看到别人的蛋糕更漂亮，犹豫。", "把自己的蛋糕送给朋友，一起分享。")):
            card.headline = f"旧版第 {card.order} 页"
            card.body = story
        old_pack.publish_copy.body = "旧版完整故事：准备蛋糕 → 比较失落 → 分享给朋友。"
        old_manifest.write_text(old_pack.model_dump_json(indent=2), encoding="utf-8")
        old_checked = validate_artifact(root, production_protocol="external-import")
        old.artifact_digest = old_checked.artifact_digest
        old.validation_json = old_checked.model_dump(mode="json")
        newer = root.parent.parent / "revision-002" / "attempt-001"
        shutil.copytree(root, newer)
        manifest = newer / Path(old.manifest_path).name
        pack = SocialContentPack.model_validate_json(manifest.read_text(encoding="utf-8"))
        # Newest revision has only two pages, with distinct pixels and wording.
        pack.cards = pack.cards[:2]
        for card, color in zip(pack.cards, ("#fb80a0", "#e0f080")):
            Image.new("RGB", (320, 420), color).save(newer / card.image_path)
            card.headline = f"新版第 {card.order} 页"
            card.body = f"不能混入旧版的新版内容 {card.order}"
        pack.publish_copy.body = "新版完整文案，不应替代用户选择的旧版。"
        manifest.write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        checked = validate_artifact(newer, production_protocol="external-import")
        revision_id = str(uuid4())
        session.add(ContentRevision(id=revision_id, content_run_id=old.content_run_id,
            revision_number=2, instruction="隔离夹具：新版不是模型质量证据。",
            production_input_json=old.production_input_json, artifact_directory=str(newer),
            manifest_path=str(manifest), artifact_digest=checked.artifact_digest,
            validation_json=checked.model_dump(mode="json"), validated_at=old.validated_at))
        session.flush()
        session.add(ContentAttempt(id=str(uuid4()), revision_id=revision_id, attempt_number=1,
            status="succeeded", output_directory=str(newer), completed_at=old.validated_at,
            usage_json={"source": "controlled-fixture", "model_calls": 0}, duration_ms=0))
        session.get(ContentRun, old.content_run_id).active_revision_number = 2
        return revision_id


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


def seed_calibration(app, root: Path, *, live=False, reference_path: Path | None = None,
                     multi_revision=False):
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
        images = [({'mime_type': 'image/png'}, raw)]
        if multi_revision and not imported:
            for page_color in ("#a0f0c0", "#d0a0f0"):
                buf = io.BytesIO()
                Image.new("RGB", (320, 420), page_color).save(buf, format="PNG")
                images.append(({"mime_type": "image/png"}, buf.getvalue()))
        imported.append(import_pack(runs, 'isolated-calibration-' + key, payload, images))
    newer_revision_id = add_newer_revision(runs, imported[0]) if multi_revision else None
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
        artifact_files=tree_hashes(runs.output_root), live=live,
        newer_revision_id=newer_revision_id, baseline_records=protected_records(db))
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
        protected_records=protected_records(db, setup["baseline_records"]), calibrations=calibrations)
