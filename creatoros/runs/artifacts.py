from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from creatoros.content import SocialContentPack
from creatoros.content.models import MANIFEST_FILENAME

from .models import ArtifactValidation, ValidatedImage


def validate_artifact(directory: str | Path) -> ArtifactValidation:
    root = Path(directory).resolve()
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
        image_path = root / card.image_path
        image_bytes = image_path.read_bytes()
        with Image.open(image_path) as image:
            image.verify()
        with Image.open(image_path) as image:
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
            )
        )
    return ArtifactValidation(
        artifact_digest=digest.hexdigest(),
        card_count=len(pack.cards),
        total_image_bytes=sum(image.byte_size for image in images),
        images=images,
    )
