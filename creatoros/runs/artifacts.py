from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from creatoros.content import SocialContentPack
from creatoros.content.models import MANIFEST_FILENAME

from .models import ArtifactValidation, ValidatedImage


def validate_artifact(directory: str | Path, *, composition=None, production_protocol="legacy", execution_spec=None) -> ArtifactValidation:
    root = Path(directory).resolve()
    if production_protocol == "task-v1":
        from .task_contracts import TaskExecutionSpec
        from .task_execution import verify_execution_spec
        if execution_spec is None:
            execution_spec = TaskExecutionSpec.model_validate_json((root / "execution_spec.json").read_text(encoding="utf-8"))
        else:
            execution_spec = TaskExecutionSpec.model_validate(execution_spec)
        if execution_spec.kind == "video":
            from creatoros.integrations.video_production import validate_video_artifact
            return validate_video_artifact(root, execution_spec)
        saved = TaskExecutionSpec.model_validate_json((root / "execution_spec.json").read_text(encoding="utf-8"))
        if saved.public_dict() != execution_spec.public_dict():
            raise ValueError("图片执行规范与当前 Revision 不一致。")
        verify_execution_spec(saved, root, ready=True)
        from creatoros.integrations.native_production import load_checkpoint, request_digest
        checkpoint = load_checkpoint(root)
        expected_skills = {Path(item.path).name: item.digest for item in saved.skills}
        if (checkpoint is None or checkpoint.skill_digests != expected_skills
                or checkpoint.input_digest != request_digest(root)):
            raise ValueError("图片生产回执与完整 Skill/任务输入不匹配。")
        production_protocol = "native-v1"
    if not (root / MANIFEST_FILENAME).resolve().is_relative_to(root):
        raise ValueError("Manifest 路径越过产物目录。")
    pack = SocialContentPack.load(root)
    manifest = json.dumps(
        pack.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(b"creatoros-social-content-pack-v1\0")
    digest.update(manifest)

    images: list[ValidatedImage] = []
    for card in pack.cards:
        image_path = (root / card.image_path).resolve()
        if not image_path.is_relative_to(root):
            raise ValueError("图片路径越过产物目录。")
        image_bytes = image_path.read_bytes()
        with Image.open(BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
        encoded_path = card.image_path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(image_bytes).to_bytes(8, "big"))
        digest.update(image_bytes)
        images.append(
            ValidatedImage(
                order=card.order,
                path=card.image_path,
                width=width,
                height=height,
                byte_size=len(image_bytes),
                sha256=hashlib.sha256(image_bytes).hexdigest(),
            )
        )
    evidence = []
    if production_protocol == "native-v1":
        from creatoros.integrations.native_production import evidence_files, load_checkpoint
        evidence = evidence_files(root, composition)
        saved = load_checkpoint(root)
        if [(p.order, p.sha256) for p in saved.pages] != [(p.order, p.sha256) for p in images]:
            raise ValueError("最终图片与已验收 checkpoint 不一致。")
    elif composition is not None:
        from creatoros.integrations.skill_pair import evidence_files
        evidence = evidence_files(root, composition, [card.order for card in pack.cards])
    for path in evidence:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        raw = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return ArtifactValidation(
        artifact_digest=digest.hexdigest(),
        card_count=len(pack.cards),
        total_image_bytes=sum(image.byte_size for image in images),
        images=images,
    )
