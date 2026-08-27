from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ContentQueue = Literal["hot", "evergreen", "experiment"]


@dataclass(frozen=True)
class ContentOpportunity:
    """One hotspot candidate shown in an author's queue."""

    author_id: str
    queue: ContentQueue
    hotspot_rank: int
    hotspot_title: str
    hotspot_url: str
    hotspot_summary: str
    score: float
    matched_prototype_id: str
    matched_domain_label: str
    profile_corpus_version: str


@dataclass(frozen=True)
class DailyPlan:
    """The three queue lanes reserved for one author's daily plan."""

    author_id: str
    hot: tuple[ContentOpportunity, ...] = ()
    evergreen: tuple[ContentOpportunity, ...] = ()
    experiment: tuple[ContentOpportunity, ...] = ()

    def queue(self, name: ContentQueue) -> tuple[ContentOpportunity, ...]:
        if name == "hot":
            return self.hot
        if name == "evergreen":
            return self.evergreen
        if name == "experiment":
            return self.experiment
        raise ValueError(f"未知内容队列：{name}")
