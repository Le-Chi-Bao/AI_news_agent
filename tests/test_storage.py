"""SQLite repository and CLI integration tests using temporary databases."""

from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ai_news_agent.collectors.arxiv import ArxivResult
from ai_news_agent.collectors.rss import FeedResult
from ai_news_agent.main import main
from ai_news_agent.models import Article, SourceType
from ai_news_agent.storage import ArticleRepository, normalize_url


def article(url="https://example.com/story", *, title="A story", published_at=None):
    return Article(
        source="Example News",
        source_type=SourceType.RSS,
        title=title,
        url=url,
        source_id="story-1",
        published_at=published_at,
        summary="Original content",
        authors=("Ada", "Bình"),
        collected_at=datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc),
    )


class ArticleRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "nested" / "articles.db"
        self.repository = ArticleRepository(self.path)
        self.addCleanup(self.repository.close)

    def test_new_article_and_count(self):
        self.assertTrue(self.repository.save(article()))
        self.assertTrue(self.path.exists())
        self.assertEqual(self.repository.count(), 1)

    def test_duplicate_url_uses_database_unique_constraint(self):
        self.assertTrue(self.repository.save(article("HTTPS://EXAMPLE.COM:443/story#section")))
        self.assertFalse(self.repository.save(article("https://example.com/story")))
        self.assertEqual(self.repository.count(), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.connection.execute(
                "INSERT INTO articles (title,url,source,source_type,authors,collected_at,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("Other", "https://example.com/story", "Other", "rss", "[]",
                 "2026-09-19T00:00:00+00:00", "2026-09-19T00:00:00+00:00"),
            )

    def test_save_many_continues_after_invalid_record(self):
        bad = article("not a URL")
        result = self.repository.save_many([
            article(), bad, article("https://example.com/story#duplicate"),
            article("https://example.com/second"),
        ])
        self.assertEqual((result.inserted, result.duplicates, result.failed), (2, 1, 1))
        self.assertIn("article 2", result.errors[0])
        self.assertEqual(self.repository.count(), 2)

    def test_get_by_url_and_exists(self):
        self.repository.save(article("https://example.com/story#one"))
        self.assertTrue(self.repository.exists("HTTPS://EXAMPLE.COM:443/story#two"))
        self.assertFalse(self.repository.exists("https://example.com/missing"))
        loaded = self.repository.get_by_url("https://example.com/story")
        self.assertEqual(loaded.url, "https://example.com/story")
        self.assertEqual(loaded.source, "Example News")
        self.assertEqual(loaded.source_type, SourceType.RSS)
        self.assertEqual(loaded.summary, "Original content")
        self.assertIsNone(self.repository.get_by_url("https://example.com/missing"))

    def test_list_recent_orders_by_published_date(self):
        older = datetime(2026, 9, 17, 8, tzinfo=timezone.utc)
        newer = datetime(2026, 9, 19, 8, tzinfo=timezone.utc)
        self.repository.save(article("https://example.com/old", title="Old", published_at=older))
        self.repository.save(article("https://example.com/new", title="New", published_at=newer))
        self.assertEqual([item.title for item in self.repository.list_recent(1)], ["New"])
        self.assertEqual(len(self.repository.list_recent()), 2)

    def test_datetime_and_authors_round_trip(self):
        published = datetime(2026, 9, 19, 17, 30, tzinfo=timezone.utc)
        original = article(published_at=published)
        self.repository.save(original)
        loaded = self.repository.get_by_url(original.url)
        self.assertEqual(loaded.published_at, published)
        self.assertEqual(loaded.collected_at, original.collected_at)
        self.assertEqual(loaded.authors, ("Ada", "Bình"))

    def test_minimal_url_normalization(self):
        self.assertEqual(normalize_url(" https://EXAMPLE.com:443/a?b=1#part "),
                         "https://example.com/a?b=1")
        self.assertEqual(normalize_url("https://example.com"), "https://example.com/")


class CliStorageTests(unittest.TestCase):
    def test_all_sources_second_run_is_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "ai_news.db")
            rss = [FeedResult(url="https://example.com/feed", name="Example News",
                              articles=[article()])]
            arxiv_paper = Article(
                source="arXiv", source_type=SourceType.ARXIV,
                title="Paper", url="https://arxiv.org/abs/2609.12345",
            )
            with patch("ai_news_agent.main.collect_feeds", return_value=rss), \
                 patch("ai_news_agent.main.collect_arxiv", return_value=ArxivResult(articles=[arxiv_paper])), \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--source", "all", "--database", path]), 0)
                first = output.getvalue()
                output.seek(0)
                output.truncate(0)
                self.assertEqual(main(["--source", "all", "--database", path]), 0)
                second = output.getvalue()
            self.assertIn("Collected: 2\nNew: 2\nDuplicates: 0\nFailed: 0", first)
            self.assertIn("Collected: 2\nNew: 0\nDuplicates: 2\nFailed: 0", second)


if __name__ == "__main__":
    unittest.main()
