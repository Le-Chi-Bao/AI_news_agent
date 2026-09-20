"""Fetch RSS/Atom feeds and convert their entries to the shared Article model."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import struct_time
from typing import Iterable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import feedparser

from ai_news_agent.models import Article, SourceType


@dataclass(slots=True)
class FeedResult:
    """Articles and diagnostics for one RSS feed."""

    url: str
    name: str
    articles: list[Article] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _published_at(entry: dict) -> datetime | None:
    parsed: struct_time | None = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is None:
        return None
    try:
        # feedparser normalizes parsed dates to UTC.
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _summary(entry: dict) -> str | None:
    for part in entry.get("content") or ():
        value = _text(part.get("value")) if hasattr(part, "get") else ""
        if value:
            return value
    return _text(entry.get("summary")) or _text(entry.get("description")) or None


def _article_from_entry(entry: dict, feed_url: str, source_name: str) -> Article:
    title = _text(entry.get("title"))
    raw_link = _text(entry.get("link"))
    if not raw_link:
        raise ValueError("entry is missing a title or valid HTTP(S) link")
    link = urljoin(feed_url, raw_link)
    parsed_url = urlparse(link)
    if not title or parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise ValueError("entry is missing a title or valid HTTP(S) link")

    authors = tuple(
        name for author in entry.get("authors") or ()
        if (name := _text(author.get("name")))
    )
    if not authors and _text(entry.get("author")):
        authors = (_text(entry.get("author")),)

    return Article(
        source=source_name,
        source_type=SourceType.RSS,
        title=title,
        url=link,
        source_id=_text(entry.get("id")) or None,
        published_at=_published_at(entry),
        summary=_summary(entry),
        authors=authors,
    )


def collect_feed(url: str, *, timeout: float = 15) -> FeedResult:
    """Collect one feed; failures are returned in FeedResult.error."""

    result = FeedResult(url=url, name=url)
    try:
        if urlparse(url).scheme not in ("http", "https"):
            raise ValueError("feed URL must use HTTP or HTTPS")
        request = Request(url, headers={"User-Agent": "AI-News-Agent/0.1 RSS collector"})
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
            final_url = response.geturl()
        parsed = feedparser.parse(payload)
        result.name = _text(parsed.feed.get("title")) or urlparse(final_url).netloc

        if not parsed.version:
            raise ValueError("response is not a recognized RSS or Atom feed")
        if parsed.bozo:
            result.warnings.append(f"feed XML warning: {parsed.bozo_exception}")

        for index, entry in enumerate(parsed.entries, start=1):
            try:
                result.articles.append(_article_from_entry(entry, final_url, result.name))
            except (AttributeError, TypeError, ValueError) as exc:
                result.warnings.append(f"entry {index} skipped: {exc}")
        if parsed.bozo and not result.articles:
            raise ValueError("feed XML is malformed and yielded no valid articles")
    except Exception as exc:
        # This is the boundary for a single external feed. Continue with other URLs.
        result.error = str(exc) or type(exc).__name__
    return result


def collect_feeds(urls: Iterable[str], *, timeout: float = 15) -> list[FeedResult]:
    """Collect several feeds independently, preserving input order."""

    return [collect_feed(url, timeout=timeout) for url in urls]
