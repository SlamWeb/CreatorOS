from dataclasses import dataclass


@dataclass(frozen=True)
class HotTopic:
    rank: int
    title: str
    url: str
    summary: str = ""
    thumbnail_url: str = ""


@dataclass(frozen=True)
class HotListSnapshot:
    source: str
    total: int
    topics: tuple[HotTopic, ...]


@dataclass(frozen=True)
class ZhihuSearchItem:
    title: str
    content_type: str
    content_id: str
    content_text: str
    url: str
    author_name: str
    vote_up_count: int
    comment_count: int
    edit_time: int
    authority_level: str
    ranking_score: float


@dataclass(frozen=True)
class ZhihuSearchSnapshot:
    query: str
    search_hash_id: str
    has_more: bool
    empty_reason: str
    items: tuple[ZhihuSearchItem, ...]
