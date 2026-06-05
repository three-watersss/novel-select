from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchSeed:
    value: str
    strategy: str


@dataclass
class NovelCandidate:
    title: str
    author: str
    source_id: str
    source_name: str
    book_url: str
    intro: str = ""
    kind: str = ""
    latest_chapter: str = ""
    word_count: str = ""
    cover_url: str = ""
    completed: bool = False
    completion_evidence: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Chapter:
    title: str
    url: str
    content: str = ""


@dataclass
class Recommendation:
    novel_id: int
    score: float
    reason: str
    risks: str
    style: str
    pacing: str
    verdict: str

