"""SQLite persistence and deduplication."""

from .article_repository import ArticleRepository, SaveResult, normalize_url

__all__ = ["ArticleRepository", "SaveResult", "normalize_url"]
