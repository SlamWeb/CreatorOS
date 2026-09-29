"""A human-entered platform receipt, never an automated publishing client."""

from datetime import datetime, timezone
from threading import RLock
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import select

from creatoros.runs.service import ContentRunError
from creatoros.storage import (
    ContentRun, ContentRunStatus, Database, ManualPublication,
    PublicationMetric, Topic, TopicStatus,
)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class ManualPublicationService:
    def __init__(self, database: Database):
        self.database = database
        self._lock = RLock()

    def record(self, run_id: str, *, expected_version: int, revision_id: str,
               artifact_digest: str, post_url: str, published_at: datetime | None = None) -> None:
        post_url = post_url.strip()
        parsed = urlsplit(post_url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com", "www.xhslink.com",
        } or not parsed.path.strip("/") or parsed.username or parsed.password:
            raise ContentRunError("请填写有效的小红书 HTTPS 笔记链接。", code="invalid_post_url", status_code=422)
        with self._lock, self.database.session() as session:
            run = session.get(ContentRun, run_id)
            if run is None:
                raise ContentRunError("Run 不存在。", code="not_found", status_code=404)
            existing = session.scalar(select(ManualPublication).where(ManualPublication.content_run_id == run_id))
            if existing is not None:
                if (existing.revision_id, existing.artifact_digest, existing.post_url) == (revision_id, artifact_digest, post_url):
                    return
                raise ContentRunError("此 Run 已登记另一条笔记；请先核对原记录。", code="already_published")
            if run.status is not ContentRunStatus.APPROVED or run.version != expected_version:
                raise ContentRunError("请先重新检查并批准当前产物。")
            if (run.approved_revision_id, run.approved_artifact_digest) != (revision_id, artifact_digest):
                raise ContentRunError("发布凭证与已批准产物不一致。")
            now = datetime.now(timezone.utc)
            session.add(ManualPublication(
                id=str(uuid4()), content_run_id=run_id, revision_id=revision_id,
                artifact_digest=artifact_digest, platform="xiaohongshu", post_url=post_url,
                published_at=_utc(published_at), recorded_at=now,
            ))
            topic = session.get(Topic, run.topic_id)
            if topic is not None:
                topic.status = TopicStatus.PUBLISHED

    def add_metrics(self, run_id: str, *, request_id: str, views: int | None = None,
                    likes: int | None = None, favorites: int | None = None,
                    comments: int | None = None, shares: int | None = None) -> None:
        values = (views, likes, favorites, comments, shares)
        if all(value is None for value in values):
            raise ContentRunError("至少填写一个指标。", code="empty_metrics", status_code=422)
        if any(value is not None and value < 0 for value in values):
            raise ContentRunError("指标不能为负数。", code="invalid_metrics", status_code=422)
        with self._lock, self.database.session() as session:
            publication = session.scalar(select(ManualPublication).where(ManualPublication.content_run_id == run_id))
            if publication is None:
                raise ContentRunError("请先登记人工发布的笔记链接。")
            existing = session.scalar(select(PublicationMetric).where(PublicationMetric.request_id == request_id))
            if existing is not None:
                if existing.publication_id == publication.id and values == (
                    existing.views, existing.likes, existing.favorites, existing.comments, existing.shares,
                ):
                    return
                raise ContentRunError("该提交编号已用于另一份指标。", code="request_conflict")
            session.add(PublicationMetric(
                id=str(uuid4()), publication_id=publication.id, request_id=request_id,
                views=views, likes=likes, favorites=favorites, comments=comments,
                shares=shares, measured_at=datetime.now(timezone.utc),
            ))
