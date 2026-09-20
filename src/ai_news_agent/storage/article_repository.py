"""SQLite persistence and URL-based deduplication for Article records."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from ai_news_agent.models import Article, SourceType


def normalize_url(url: str) -> str:
    """Trim whitespace/fragment and normalize scheme, host, and default port."""

    if not isinstance(url, str):
        raise TypeError("url must be a string")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("url must be an absolute HTTP(S) URL")
    host = parts.hostname.lower()
    if ":" in host:  # Preserve brackets around IPv6 hosts.
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("url has an invalid port") from exc
    if port and port != {"http": 80, "https": 443}[scheme]:
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parts.path or "/", parts.query, ""))


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return value.astimezone(timezone.utc).isoformat()


@dataclass(slots=True)
class SaveResult:
    inserted: int = 0
    duplicates: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    inserted_articles: list[Article] = field(default_factory=list)
    inserted_ids: list[int] = field(default_factory=list)


class ArticleRepository:
    """Small SQLite repository; use as a context manager or call close()."""

    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT,
                published_at TEXT,
                content TEXT,
                authors TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS article_analyses (
                article_id INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
                model TEXT NOT NULL,
                category TEXT NOT NULL,
                relevance_score INTEGER NOT NULL CHECK(relevance_score BETWEEN 0 AND 10),
                summary TEXT NOT NULL,
                key_points TEXT NOT NULL,
                reason TEXT NOT NULL,
                analyzed_at TEXT NOT NULL
            )
        """)
        self.connection.commit()

    def __enter__(self) -> "ArticleRepository":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _insert(self, article: Article) -> tuple[bool, int | None]:
        if not isinstance(article, Article):
            raise TypeError("expected an Article")
        url = normalize_url(article.url)
        cursor = self.connection.execute("""
            INSERT INTO articles (
                title, url, source, source_type, source_id, published_at,
                content, authors, collected_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO NOTHING
        """, (
            article.title, url, article.source, article.source_type.value,
            article.source_id,
            _utc_text(article.published_at) if article.published_at else None,
            article.summary,
            json.dumps(article.authors, ensure_ascii=False),
            _utc_text(article.collected_at),
            datetime.now(timezone.utc).isoformat(),
        ))
        return cursor.rowcount == 1, (cursor.lastrowid if cursor.rowcount == 1 else None)

    def save(self, article: Article) -> bool:
        """Return True for a new row, False for an existing normalized URL."""

        with self.connection:
            inserted, _ = self._insert(article)
            return inserted

    def save_many(self, articles: Iterable[Article]) -> SaveResult:
        """Save valid records even when another record in the batch fails."""

        result = SaveResult()
        with self.connection:
            for index, article in enumerate(articles, start=1):
                try:
                    inserted, article_id = self._insert(article)
                    if inserted:
                        result.inserted += 1
                        result.inserted_articles.append(article)
                        result.inserted_ids.append(article_id)
                    else:
                        result.duplicates += 1
                except (TypeError, ValueError, sqlite3.Error) as exc:
                    result.failed += 1
                    result.errors.append(f"article {index}: {exc}")
        return result

    def has_analysis(self, article_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM article_analyses WHERE article_id = ?", (article_id,)
        ).fetchone()
        return row is not None

    def save_analysis(self, article_id: int, analysis: object, model: str) -> None:
        """Persist one validated ArticleAnalysis without altering Article rows."""
        with self.connection:
            self.connection.execute("""
                INSERT INTO article_analyses
                (article_id, model, category, relevance_score, summary, key_points, reason, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    model=excluded.model, category=excluded.category,
                    relevance_score=excluded.relevance_score, summary=excluded.summary,
                    key_points=excluded.key_points, reason=excluded.reason,
                    analyzed_at=excluded.analyzed_at
            """, (article_id, model, analysis.category, analysis.relevance_score,
                  analysis.summary, json.dumps(analysis.key_points, ensure_ascii=False),
                  analysis.reason, datetime.now(timezone.utc).isoformat()))

    def get_analysis(self, article_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM article_analyses WHERE article_id = ?", (article_id,)
        ).fetchone()
        if not row:
            return None
        return {**dict(row), "key_points": json.loads(row["key_points"])}

    def exists(self, url: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM articles WHERE url = ?", (normalize_url(url),)
        ).fetchone()
        return row is not None

    def get_by_url(self, url: str) -> Article | None:
        row = self.connection.execute(
            "SELECT * FROM articles WHERE url = ?", (normalize_url(url),)
        ).fetchone()
        return self._to_article(row) if row else None

    def count(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    def list_recent(self, limit: int = 20) -> list[Article]:
        """Return records ordered by published date, falling back to insert date."""

        if limit < 0:
            raise ValueError("limit must be nonnegative")
        rows = self.connection.execute("""
            SELECT * FROM articles
            ORDER BY COALESCE(published_at, created_at) DESC, id DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [self._to_article(row) for row in rows]

    @staticmethod
    def _to_article(row: sqlite3.Row) -> Article:
        return Article(
            source=row["source"],
            source_type=SourceType(row["source_type"]),
            title=row["title"],
            url=row["url"],
            source_id=row["source_id"],
            published_at=datetime.fromisoformat(row["published_at"]) if row["published_at"] else None,
            summary=row["content"],
            authors=tuple(json.loads(row["authors"])),
            collected_at=datetime.fromisoformat(row["collected_at"]),
        )
