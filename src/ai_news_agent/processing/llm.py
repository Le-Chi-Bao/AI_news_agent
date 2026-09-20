"""Provider abstraction and structured ArticleAnalysis processing."""

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from typing import Any, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_news_agent.models import Article


CATEGORIES = {"research", "model_release", "product", "company", "infrastructure", "regulation", "other"}


@dataclass(frozen=True, slots=True)
class ArticleAnalysis:
    category: str
    relevance_score: int
    summary: str
    key_points: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"invalid category: {self.category}")
        if not isinstance(self.relevance_score, int) or not 0 <= self.relevance_score <= 10:
            raise ValueError("relevance_score must be an integer from 0 to 10")
        if not self.summary.strip() or not self.reason.strip() or not self.key_points:
            raise ValueError("summary, reason, and key_points are required")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ArticleAnalysis":
        points = value.get("key_points")
        if not isinstance(points, list) or not all(isinstance(p, str) and p.strip() for p in points):
            raise ValueError("key_points must be a non-empty list of strings")
        return cls(value.get("category"), value.get("relevance_score"), value.get("summary"), tuple(points), value.get("reason"))


class LLMProvider(Protocol):
    model: str
    def analyze(self, article: Article) -> ArticleAnalysis: ...


class LLMProviderError(RuntimeError):
    pass


class OpenAIProvider:
    endpoint = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str | None = None, model: str | None = None, *, timeout: float = 45):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.timeout = timeout
        if not self.api_key:
            raise LLMProviderError("OPENAI_API_KEY is not configured")

    def analyze(self, article: Article) -> ArticleAnalysis:
        content = (article.summary or "")[:12000]
        prompt = (f"Title: {article.title}\nSource: {article.source}\nURL: {article.url}\n"
                  f"Published: {article.published_at or 'unknown'}\nContent: {content}")
        payload = {"model": self.model, "temperature": 0, "messages": [
            {"role": "system", "content": "Analyze only the supplied article. Do not invent facts, URLs, numbers, or events. If evidence is insufficient, express uncertainty. Return only JSON matching the requested schema."},
            {"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}}
        request = Request(self.endpoint, data=json.dumps(payload).encode(), headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
        }, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
            text = body["choices"][0]["message"]["content"]
            return ArticleAnalysis.from_dict(json.loads(text))
        except (HTTPError, URLError, TimeoutError, OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMProviderError(f"OpenAI analysis failed: {exc}") from exc


@dataclass(slots=True)
class BatchResult:
    processed: int = 0
    failed: int = 0
    results: list[ArticleAnalysis] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ArticleProcessor:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def analyze(self, article: Article) -> ArticleAnalysis:
        return self.provider.analyze(article)

    def analyze_many(self, articles: Sequence[Article]) -> BatchResult:
        batch = BatchResult()
        for index, article in enumerate(articles, start=1):
            try:
                batch.results.append(self.analyze(article))
                batch.processed += 1
            except Exception as exc:
                batch.failed += 1
                batch.errors.append(f"article {index}: {exc}")
        return batch
