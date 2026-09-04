from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from creatoros.content import SocialContentPack
from creatoros.runs import ContentRunError
from creatoros.runs.artifacts import validate_artifact
from creatoros.runs.models import ContentRunInput
from creatoros.storage import ContentAttempt, ContentRevision, ContentRun
from sqlalchemy import select

from .schemas import CardView, PublishCopyView, SourceView


class StudioArtifacts:
    """Resolve only a recorded Run's own Revision/Attempt, never a caller's path."""

    def __init__(self, database, output_root: Path):
        self.database = database
        self.output_root = output_root.resolve()

    def locate(self, run_id: str, revision_id: str):
        with self.database.session() as session:
            run = session.get(ContentRun, run_id)
            revision = session.get(ContentRevision, revision_id)
            if run is None or revision is None or revision.content_run_id != run_id:
                raise ContentRunError("内容版本不存在。", status_code=404, code="not_found")
            if not revision.artifact_directory:
                raise ContentRunError("此版本尚无产物。", status_code=404, code="no_artifact")
            data = ContentRunInput.model_validate(revision.production_input_json)
            root = Path(revision.artifact_directory).resolve()
            expected = self.output_root / data.creator_id / data.series_id / run_id / f"revision-{revision.revision_number:03d}"
            attempts = list(session.scalars(select(ContentAttempt).where(ContentAttempt.revision_id == revision.id)))
            valid_paths = {expected / f"attempt-{attempt.attempt_number:03d}" for attempt in attempts}
            if not root.is_relative_to(self.output_root) or root not in valid_paths:
                raise ContentRunError("产物目录与当前内容版本不匹配。", code="artifact_unavailable")
            return root, revision.artifact_digest, data, revision.validation_json or {}

    @staticmethod
    def pack(root: Path, data: ContentRunInput) -> SocialContentPack:
        manifest = (root / "social_content_pack.json").resolve()
        if not manifest.is_relative_to(root):
            raise ValueError("Manifest 越过产物目录。")
        pack = SocialContentPack.load(root)
        if (pack.creator_id, pack.series_id, pack.topic_id) != (data.creator_id, data.series_id, data.topic_id):
            raise ValueError("产物身份与运行输入不匹配。")
        return pack

    def projection(self, run_id: str, revision_id: str) -> dict:
        try:
            root, digest, data, _saved_validation = self.locate(run_id, revision_id)
            if digest is None:
                return dict(artifact_available=False, artifact_error=None, review_digest=None)
            pack = self.pack(root, data)
            checked = validate_artifact(root)
            if checked.artifact_digest != digest:
                raise ValueError("产物已变化。")
            prefix = f"/api/runs/{run_id}/revisions/{revision_id}/cards"
            return dict(
                artifact_available=True, artifact_error=None, review_digest=digest,
                content_summary=pack.content_summary,
                cards=[CardView(order=card.order, headline=card.headline, width=info.width, height=info.height,
                                url=f"{prefix}/{card.order}?digest={digest}&checksum={info.sha256}")
                       for card, info in zip(pack.cards, checked.images)],
                publish_copy=PublishCopyView(**pack.publish_copy.model_dump()),
                sources=[SourceView(title=source.title, url=_safe_url(source.url)) for source in pack.sources],
            )
        except (OSError, ValueError, Image.DecompressionBombError):
            return dict(artifact_available=False, artifact_error="产物缺失、损坏或已变化，请检查文件或提出返工。", review_digest=None)

    def image(self, run_id: str, revision_id: str, order: int, *, digest: str, checksum: str):
        try:
            root, recorded_digest, data, saved_validation = self.locate(run_id, revision_id)
            if digest != recorded_digest:
                raise ContentRunError("图片版本已变化，请重新检查。", code="artifact_changed")
            pack = self.pack(root, data)
            card = next((card for card in pack.cards if card.order == order), None)
            if card is None:
                raise ContentRunError("图片不存在。", status_code=404, code="not_found")
            saved_image = next((item for item in saved_validation.get("images", []) if item.get("order") == order), {})
            expected_checksum = saved_image.get("sha256")
            if expected_checksum is None:
                # Older validation JSON has no per-image hashes; verify its unchanged whole pack.
                checked = validate_artifact(root)
                if checked.artifact_digest != recorded_digest:
                    raise ContentRunError("产物已变化，请重新检查。", code="artifact_changed")
                expected_checksum = checked.images[order - 1].sha256
            if checksum != expected_checksum:
                raise ContentRunError("图片校验和与该版本不匹配。", code="artifact_changed")
            path = (root / card.image_path).resolve()
            if not path.is_relative_to(root):
                raise ValueError("图片越过产物目录。")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise ContentRunError("图片已变化，请重新检查。", code="artifact_changed")
            with Image.open(BytesIO(raw)) as image:
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}.get(image.format)
                image.verify()
            if mime is None:
                raise ValueError("不支持的图片格式。")
            return raw, mime
        except ContentRunError:
            raise
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            raise ContentRunError("图片不可读取，请检查文件或提出返工。", code="artifact_unavailable") from error


def _safe_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None
