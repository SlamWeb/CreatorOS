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
