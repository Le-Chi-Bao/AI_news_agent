import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_news_agent.collectors.search import (
    SearchProviderError, TavilySearchProvider, collect_search,
)
from ai_news_agent.main import main
from ai_news_agent.models import SourceType


RAW = [
    {"title": "AI release", "url": "https://Example.com/news?id=1#read", "content": "Snippet", "published_date": "2026-09-20T10:00:00Z", "id": "x"},
    {"title": "Missing URL", "content": "skip"},
    {"url": "https://example.com/no-title"},
]


class FakeProvider:
    def __init__(self, failures=()): self.failures = set(failures); self.calls = []
    def search(self, query, *, max_results, time_range=None):
        self.calls.append((query, max_results, time_range))
        if query in self.failures: raise SearchProviderError("provider failed")
        return RAW


class SearchTests(unittest.TestCase):
    def test_normalizes_results_and_source(self):
        provider = FakeProvider()
        results = collect_search(["AI news"], provider, max_results=3, time_range="week")
        self.assertEqual(len(results[0].articles), 1)
        article = results[0].articles[0]
        self.assertEqual(article.source, "example.com")
        self.assertEqual(article.source_type, SourceType.SEARCH)
        self.assertEqual(article.summary, "Snippet")
        self.assertEqual(len(results[0].warnings), 2)
        self.assertEqual(provider.calls, [("AI news", 3, "week")])

    def test_query_failure_does_not_stop_batch(self):
        provider = FakeProvider(["bad"])
        results = collect_search(["bad", "good"], provider)
        self.assertIn("provider failed", results[0].error)
        self.assertEqual(len(results[1].articles), 1)

    def test_tavily_missing_key_is_clear(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SearchProviderError) as error:
                TavilySearchProvider()
        self.assertIn("TAVILY_API_KEY", str(error.exception))

    @patch("ai_news_agent.collectors.search.urlopen")
    def test_tavily_request_and_response(self, mock_urlopen):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): pass
        mock_urlopen.return_value = Response(json.dumps({"results": RAW}).encode())
        provider = TavilySearchProvider("tvly-test")
        results = provider.search("AI", max_results=2, time_range="day")
        self.assertEqual(len(results), 3)
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer tvly-test")
        self.assertEqual(json.loads(request.data)["time_range"], "day")

    def test_search_cli_without_key_returns_clean_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            self.assertEqual(main(["--source", "search", "--database", str(Path(directory) / "x.db")]), 1)


if __name__ == "__main__": unittest.main()
