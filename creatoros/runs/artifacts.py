from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from creatoros.content import SocialContentPack
from creatoros.content.models import MANIFEST_FILENAME

from .models import ArtifactValidation, ValidatedImage


def validate_artifact(directory: str | Path) -> ArtifactValidation:
    root = Path(directory).resolve()
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
    return ArtifactValidation(
        artifact_digest=digest.hexdigest(),
        card_count=len(pack.cards),
        total_image_bytes=sum(image.byte_size for image in images),
        images=images,
    )
