from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ContentQueue = Literal["hot", "evergreen", "experiment"]
PositiveInt = Annotated[int, Field(ge=1)]


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


class SelectionModel(BaseModel):
    """Strict input contract for model-produced selection plans."""

    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class CandidateSelector(SelectionModel):
    """Choose candidates by exactly one addressing method."""

    kind: Literal["positions", "hotspot_ranks", "top_n", "all"]
    positions: list[PositiveInt] = Field(default_factory=list)
    hotspot_ranks: list[PositiveInt] = Field(default_factory=list)
    top_n: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_addressing(self) -> "CandidateSelector":
        active = {
            "positions": bool(self.positions),
            "hotspot_ranks": bool(self.hotspot_ranks),
            "top_n": self.top_n is not None,
            "all": not self.positions and not self.hotspot_ranks and self.top_n is None,
        }
        if not active[self.kind] or sum(active.values()) != 1:
            raise ValueError(f"候选选择方式 {self.kind} 与所提供参数不一致。")
        if len(self.positions) != len(set(self.positions)):
            raise ValueError("positions 不能重复。")
        if len(self.hotspot_ranks) != len(set(self.hotspot_ranks)):
            raise ValueError("hotspot_ranks 不能重复。")
        return self


class SelectionGroup(SelectionModel):
    """Apply one candidate rule to selected authors or all authors."""

    authors: list[str] | Literal["all"]
    exclude_authors: list[str] = Field(default_factory=list)
    queue: ContentQueue = "hot"
    candidates: CandidateSelector

    @model_validator(mode="after")
    def validate_authors(self) -> "SelectionGroup":
        if isinstance(self.authors, list):
            if not self.authors or any(not author.strip() for author in self.authors):
                raise ValueError("authors 必须包含至少一个非空作者 ID。")
            if self.exclude_authors:
                raise ValueError("显式选择 authors 时不能再使用 exclude_authors。")
            if len(self.authors) != len(set(self.authors)):
                raise ValueError("authors 不能重复。")
        if any(not author.strip() for author in self.exclude_authors):
            raise ValueError("exclude_authors 不能包含空作者 ID。")
        if len(self.exclude_authors) != len(set(self.exclude_authors)):
            raise ValueError("exclude_authors 不能重复。")
        return self


class SelectionPlan(SelectionModel):
    """Normalized user intent before deterministic candidate expansion."""

    execution_mode: Literal["preview", "confirmed", "auto"] = "preview"
    action: Literal["answer"] = "answer"
    selections: list[SelectionGroup] = Field(min_length=1)
    instruction: str | None = None
    route_snapshot_id: str | None = None
