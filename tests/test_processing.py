import tempfile
import unittest
from datetime import datetime, timezone

from ai_news_agent.models import Article, SourceType
from ai_news_agent.processing import ArticleAnalysis, ArticleProcessor
from ai_news_agent.storage import ArticleRepository


def article(url="https://example.com/a"):
    return Article(source="Example", source_type=SourceType.RSS, title="AI story", url=url,
                   summary="Facts from the article.", collected_at=datetime.now(timezone.utc))


class FakeLLM:
    model = "fake-model"
    def __init__(self, fail=()): self.fail = set(fail); self.calls = []
    def analyze(self, item):
        self.calls.append(item.url)
        if item.url in self.fail: raise RuntimeError("provider failed")
        return ArticleAnalysis("research", 8, "Short factual summary", ("Fact one",), "Relevant research")


class ProcessingTests(unittest.TestCase):
    def test_analysis_validation(self):
        analysis = ArticleAnalysis.from_dict({"category":"product", "relevance_score":7,
            "summary":"Summary", "key_points":["Point"], "reason":"Useful"})
        self.assertEqual(analysis.key_points, ("Point",))
        with self.assertRaises(ValueError): ArticleAnalysis("bad", 2, "x", ("y",), "z")
        with self.assertRaises(ValueError): ArticleAnalysis("other", 11, "x", ("y",), "z")

    def test_batch_continues_after_failure(self):
        provider = FakeLLM({"https://example.com/b"})
        result = ArticleProcessor(provider).analyze_many([article(), article("https://example.com/b"), article("https://example.com/c")])
        self.assertEqual((result.processed, result.failed), (2, 1))
        self.assertEqual(len(result.errors), 1)

    def test_analysis_storage_round_trip_and_existing_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            with ArticleRepository(directory + "/x.db") as repo:
                repo.save(article())
                stored = repo.get_by_url("https://example.com/a")
                self.assertFalse(repo.has_analysis(stored and 1))
                analysis = ArticleAnalysis("research", 8, "Summary", ("Point",), "Reason")
                repo.save_analysis(1, analysis, "fake-model")
                self.assertTrue(repo.has_analysis(1))
                loaded = repo.get_analysis(1)
                self.assertEqual(loaded["category"], "research")
                self.assertEqual(loaded["key_points"], ["Point"])


if __name__ == "__main__": unittest.main()
