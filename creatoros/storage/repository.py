from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import Database
from .models import (
    Creator,
    CreatorPlatform,
    OperationPolicy,
    Series,
    Topic,
    TopicSource,
    TopicStatus,
)


class ContentRepository:
    def __init__(self, database: Database, *, session: Session | None = None):
        self.database = database
        self._bound_session = session

    @contextmanager
    def _session(self) -> Iterator[Session]:
        if self._bound_session is not None:
            yield self._bound_session
            return
        with self.database.session() as session:
            yield session

    @contextmanager
    def transaction(self) -> Iterator["ContentRepository"]:
        if self._bound_session is not None:
            raise RuntimeError("不能在已绑定事务的 Repository 中再次开启事务。")
        with self.database.session() as session:
            yield ContentRepository(self.database, session=session)

    def create_creator(
        self,
        *,
        creator_id: str,
        display_name: str,
        platform: CreatorPlatform = CreatorPlatform.XIAOHONGSHU,
        account_handle: str | None = None,
        timezone: str = "Asia/Shanghai",
        daily_content_limit: int | None = None,
    ) -> Creator:
        with self._session() as session:
            creator = Creator(
                id=creator_id,
                display_name=display_name,
                platform=platform,
                account_handle=account_handle,
                timezone=timezone,
                daily_content_limit=daily_content_limit,
            )
            session.add(creator)
            session.flush()
            return creator

    def get_creator(self, creator_id: str) -> Creator | None:
        with self._session() as session:
            return session.get(Creator, creator_id)

    def create_series(
        self,
        *,
        series_id: str,
        creator_id: str,
        name: str,
        description: str,
        audience: str,
        skill_name: str,
        selection_policy: OperationPolicy = OperationPolicy.APPROVAL,
        publish_policy: OperationPolicy = OperationPolicy.APPROVAL,
        replenish_threshold: int = 5,
    ) -> Series:
        with self._session() as session:
            series = Series(
                id=series_id,
                creator_id=creator_id,
                name=name,
                description=description,
                audience=audience,
                skill_name=skill_name,
                selection_policy=selection_policy,
                publish_policy=publish_policy,
                replenish_threshold=replenish_threshold,
            )
            session.add(series)
            session.flush()
            return series

    def get_series(self, series_id: str) -> Series | None:
        with self._session() as session:
            return session.get(Series, series_id)

    def list_series(self, creator_id: str | None = None) -> tuple[Series, ...]:
        with self._session() as session:
            statement = select(Series)
            if creator_id is not None:
                statement = statement.where(Series.creator_id == creator_id)
            return tuple(session.scalars(statement.order_by(Series.created_at, Series.id)))

    def get_topic(self, topic_id: str) -> Topic | None:
        with self._session() as session:
            return session.get(Topic, topic_id)

    def add_topic(
        self,
        *,
        topic_id: str,
        series_id: str,
        title: str,
        source: TopicSource,
        brief: str | None = None,
    ) -> Topic:
        with self._session() as session:
            current_max = session.scalar(
                select(func.max(Topic.position)).where(Topic.series_id == series_id)
            )
            topic = Topic(
                id=topic_id,
                series_id=series_id,
                title=title,
                brief=brief,
                source=source,
                status=TopicStatus.QUEUED,
                position=(current_max or 0) + 1,
            )
            session.add(topic)
            session.flush()
            return topic

    def list_topics(self, series_id: str) -> tuple[Topic, ...]:
        with self._session() as session:
            return tuple(
                session.scalars(
                    select(Topic)
                    .where(Topic.series_id == series_id)
                    .order_by(Topic.position, Topic.created_at, Topic.id)
                )
            )

    def reorder_topics(self, series_id: str, ordered_topic_ids: list[str]) -> tuple[Topic, ...]:
        if not ordered_topic_ids or len(ordered_topic_ids) != len(set(ordered_topic_ids)):
            raise ValueError("ordered_topic_ids 必须是非空且不重复的完整列表。")
        with self._session() as session:
            topics = list(
                session.scalars(select(Topic).where(Topic.series_id == series_id))
            )
            by_id = {topic.id: topic for topic in topics}
            if set(ordered_topic_ids) != set(by_id):
                raise ValueError("调序必须包含该 Series 当前全部且仅包含其自身的 Topic。")

            temporary_offset = len(topics)
            for temporary_position, topic_id in enumerate(ordered_topic_ids, start=1):
                by_id[topic_id].position = temporary_offset + temporary_position
            session.flush()
            for final_position, topic_id in enumerate(ordered_topic_ids, start=1):
                by_id[topic_id].position = final_position
            session.flush()
            return tuple(by_id[topic_id] for topic_id in ordered_topic_ids)
