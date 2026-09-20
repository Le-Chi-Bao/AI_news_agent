"""Search arXiv's Atom API and map paper metadata to Article."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from time import sleep
from typing import Sequence
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import feedparser

from ai_news_agent.arxiv_config import ARXIV_CATEGORIES, ARXIV_SCAN_LIMIT
from ai_news_agent.models import Article, SourceType


API_URL = "https://export.arxiv.org/api/query"
_CATEGORY = re.compile(r"^[A-Za-z][A-Za-z0-9-]*\.[A-Za-z][A-Za-z0-9-]*$")


@dataclass(slots=True)
class ArxivResult:
    """Papers and diagnostics for one arXiv collection run."""

    articles: list[Article] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def _clean(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _article_from_entry(entry: dict) -> Article:
    title = _clean(entry.get("title"))
    link = _clean(entry.get("link")) or _clean(entry.get("id"))
    parsed_url = urlparse(link)
    if not title or parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise ValueError("paper is missing a title or valid URL")

    published = entry.get("published_parsed")
    published_at = None
    if published is not None:
        try:
            published_at = datetime(*published[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass

    authors = tuple(
        name for author in entry.get("authors") or ()
        if (name := _clean(author.get("name")))
    )
    paper_id = _clean(entry.get("id"))
    source_id = urlparse(paper_id).path.removeprefix("/abs/") if paper_id else None
    return Article(
        source="arXiv",
        source_type=SourceType.ARXIV,
        title=title,
        url=link,
        source_id=source_id or None,
        published_at=published_at,
        summary=_clean(entry.get("summary")) or None,
        authors=authors,
    )


def _fetch_category(category: str, count: int, timeout: float) -> list[dict]:
    url = API_URL + "?" + urlencode({
        "search_query": f"cat:{category}",
        "start": 0,
        "max_results": count,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    request = Request(url, headers={"User-Agent": "AI-News-Agent/0.1 (arXiv metadata collector)"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
    parsed = feedparser.parse(payload)
    if parsed.version != "atom10":
        raise ValueError("arXiv API returned an invalid Atom response")
    if parsed.bozo:
        raise ValueError(f"arXiv API returned malformed Atom: {parsed.bozo_exception}")
    return parsed.entries


def collect_arxiv(
    *,
    categories: Sequence[str] = ARXIV_CATEGORIES,
    keywords: Sequence[str] = (),
    max_results: int = 20,
    recent_days: int | None = None,
    timeout: float = 20,
) -> ArxivResult:
    """Collect newest papers per category, then filter and combine locally.

    arXiv requests are spaced by three seconds. Errors in one category do not
    discard papers from other categories. Keyword and date filters inspect only
    the bounded recent metadata fetched from each category.
    """

    result = ArxivResult()
    try:
        if not 1 <= max_results <= 2000:
            raise ValueError("max_results must be between 1 and 2000")
        if recent_days is not None and recent_days < 1:
            raise ValueError("recent_days must be positive")
        categories = tuple(dict.fromkeys(categories or ARXIV_CATEGORIES))
        for category in categories:
            if not isinstance(category, str) or not _CATEGORY.fullmatch(category):
                raise ValueError(f"invalid arXiv category: {category!r}")
        wanted = tuple(term for word in keywords if (term := _clean(word).casefold()))
        cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days) if recent_days else None
        scan_count = min(ARXIV_SCAN_LIMIT, max_results * 5)
    except (TypeError, ValueError) as exc:
        result.error = str(exc)
        return result

    seen: set[str] = set()
    successes = 0
    for index, category in enumerate(categories):
        if index:
            sleep(3)
        try:
            entries = _fetch_category(category, scan_count, timeout)
            successes += 1
        except Exception as exc:
            result.warnings.append(f"{category} failed: {str(exc) or type(exc).__name__}")
            continue

        for entry_index, entry in enumerate(entries, start=1):
            try:
                article = _article_from_entry(entry)
            except (AttributeError, TypeError, ValueError) as exc:
                result.warnings.append(f"{category} paper {entry_index} skipped: {exc}")
                continue

            if cutoff and (article.published_at is None or article.published_at < cutoff):
                continue
            haystack = f"{article.title} {article.summary or ''}".casefold()
            if wanted and not any(term in haystack for term in wanted):
                continue
            key = article.source_id or article.url
            if key in seen:
                continue
            seen.add(key)
            result.articles.append(article)
            missing = [
                name for name, absent in (
                    ("authors", not article.authors),
                    ("abstract", not article.summary),
                    ("published date", article.published_at is None),
                ) if absent
            ]
            if missing:
                result.warnings.append(f"{category} paper {entry_index} missing {', '.join(missing)}")

    result.articles.sort(key=lambda a: a.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    del result.articles[max_results:]
    if not successes:
        result.error = "all arXiv category queries failed: " + "; ".join(result.warnings)
    return result
