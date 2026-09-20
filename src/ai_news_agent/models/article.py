"""Common article shape for every future collector."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class SourceType(str, Enum):
    """Method used to collect an article."""

    RSS = "rss"
    ARXIV = "arxiv"
    SEARCH = "search"


ArticleSource = SourceType  # Compatibility for code importing the Phase 2 enum name.


@dataclass(slots=True)
class Article:
    """Normalized input record before persistence or enrichment.

    ``source_id`` is the identifier supplied by an upstream source, when available.
    Timestamps are timezone-aware; no URL normalization or deduplication happens here.
    """

    source: str
    source_type: SourceType
    title: str
    url: str
    source_id: str | None = None
    published_at: datetime | None = None
    summary: str | None = None
    authors: tuple[str, ...] = ()
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a nonblank name")
        if not isinstance(self.source_type, SourceType):
            raise TypeError("source_type must be a SourceType")
        if not self.title.strip():
            raise ValueError("title must not be blank")
        if not self.url.strip():
            raise ValueError("url must not be blank")
        for name in ("published_at", "collected_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must include timezone information")
