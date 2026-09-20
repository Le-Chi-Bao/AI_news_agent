"""Provider based web search collection, with Tavily as the first provider."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from typing import Any, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from ai_news_agent.models import Article, SourceType


class SearchProviderError(RuntimeError):
    """A provider could not complete a query."""


class SearchProvider(Protocol):
    def search(self, query: str, *, max_results: int, time_range: str | None = None) -> list[dict[str, Any]]:
        """Return raw provider result dictionaries."""


class TavilySearchProvider:
    """Minimal Tavily HTTP client using only the Python standard library."""

    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str | None = None, *, timeout: float = 20):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        self.timeout = timeout
        if not self.api_key:
            raise SearchProviderError("TAVILY_API_KEY is not configured")

    def search(self, query: str, *, max_results: int, time_range: str | None = None) -> list[dict[str, Any]]:
        if not query.strip():
            raise SearchProviderError("search query must not be blank")
        if not 1 <= max_results <= 20:
            raise SearchProviderError("max_results must be between 1 and 20")
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "topic": "news",
            "time_range": time_range,
            "include_answer": False,
            "include_raw_content": False,
            "include_published_date": True,
        }
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "AI-News-Agent/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 401:
                message = "Tavily rejected TAVILY_API_KEY (401 unauthorized)"
            elif exc.code == 429:
                message = "Tavily rate limit reached (429)"
            else:
                message = f"Tavily API HTTP {exc.code}"
            raise SearchProviderError(message) from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise SearchProviderError(f"Tavily request failed: {exc}") from exc
        results = body.get("results")
        if not isinstance(results, list):
            raise SearchProviderError("Tavily response did not contain a results list")
        return [item for item in results if isinstance(item, dict)]


@dataclass(slots=True)
class SearchResult:
    query: str
    articles: list[Article] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def _date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _article_from_result(raw: dict[str, Any]) -> Article:
    title = raw.get("title") if isinstance(raw.get("title"), str) else ""
    url = raw.get("url") if isinstance(raw.get("url"), str) else ""
    title, url = title.strip(), url.strip()
    parsed = urlparse(url)
    if not title:
        raise ValueError("search result missing title")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("search result missing valid URL")
    source = parsed.hostname or parsed.netloc
    return Article(
        source=source,
        source_type=SourceType.SEARCH,
        title=title,
        url=url,
        source_id=str(raw.get("id")) if raw.get("id") else None,
        published_at=_date(raw.get("published_date")),
        summary=(raw.get("content") or raw.get("snippet")) if isinstance(raw.get("content") or raw.get("snippet"), str) else None,
    )


def collect_search(
    queries: Sequence[str],
    provider: SearchProvider,
    *,
    max_results: int = 8,
    time_range: str | None = None,
) -> list[SearchResult]:
    """Run every query independently and normalize valid results to Articles."""

    output: list[SearchResult] = []
    for query in queries:
        result = SearchResult(query=query)
        try:
            raw_results = provider.search(query, max_results=max_results, time_range=time_range)
            for index, raw in enumerate(raw_results, start=1):
                try:
                    result.articles.append(_article_from_result(raw))
                except (TypeError, ValueError) as exc:
                    result.warnings.append(f"result {index} skipped: {exc}")
        except Exception as exc:
            result.error = str(exc) or type(exc).__name__
        output.append(result)
    return output
