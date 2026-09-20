"""arXiv API tests using local Atom responses instead of network calls."""

import io
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from ai_news_agent.collectors.arxiv import collect_arxiv
from ai_news_agent.models import SourceType


ATOM_XML = b'''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<id>https://export.arxiv.org/api/query</id><title>arXiv Query</title>
<updated>2025-09-19T13:00:00Z</updated>
<entry><id>https://arxiv.org/abs/2509.12345v1</id>
<title>  Learning   with AI\n systems </title>
<link href="https://arxiv.org/abs/2509.12345v1" rel="alternate" type="text/html"/>
<published>2025-09-19T12:30:00Z</published>
<updated>2025-09-19T12:30:00Z</updated>
<summary>  An abstract\n with details.  </summary>
<author><name>Ada Lovelace</name></author><author><name>Alan Turing</name></author>
</entry>
<entry><title>Incomplete</title>
<published>2025-09-19T12:30:00Z</published></entry>
</feed>'''

EMPTY_ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom">
<id>https://export.arxiv.org/api/query</id><title>No papers</title>
<updated>2025-09-19T13:00:00Z</updated></feed>'''


class FakeResponse(io.BytesIO):
    pass


class ArxivCollectorTests(unittest.TestCase):
    @patch("ai_news_agent.collectors.arxiv.urlopen")
    def test_maps_paper_and_skips_missing_metadata(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(ATOM_XML)
        result = collect_arxiv(categories=["cs.AI"], keywords=["learning"],
                               max_results=5)

        self.assertIsNone(result.error)
        self.assertEqual(len(result.articles), 1)
        paper = result.articles[0]
        self.assertEqual(paper.source, "arXiv")
        self.assertEqual(paper.source_type, SourceType.ARXIV)
        self.assertEqual(paper.title, "Learning with AI systems")
        self.assertEqual(paper.url, "https://arxiv.org/abs/2509.12345v1")
        self.assertEqual(paper.source_id, "2509.12345v1")
        self.assertEqual(paper.authors, ("Ada Lovelace", "Alan Turing"))
        self.assertEqual(paper.summary, "An abstract with details.")
        self.assertEqual(paper.published_at, datetime(2025, 9, 19, 12, 30, tzinfo=timezone.utc))
        self.assertIn("paper 2 skipped", result.warnings[0])

        request = mock_urlopen.call_args.args[0]
        params = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(params["max_results"], ["25"])
        self.assertEqual(params["sortBy"], ["submittedDate"])
        self.assertEqual(params["search_query"], ["cat:cs.AI"])

    @patch("ai_news_agent.collectors.arxiv.urlopen")
    def test_no_results_is_success(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(EMPTY_ATOM)
        result = collect_arxiv(categories=["cs.CL"], max_results=10)
        self.assertIsNone(result.error)
        self.assertEqual(result.articles, [])

    @patch("ai_news_agent.collectors.arxiv.urlopen")
    def test_network_error_is_returned(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("timed out")
        result = collect_arxiv(categories=["cs.AI"])
        self.assertIn("timed out", result.error)

    @patch("ai_news_agent.collectors.arxiv.urlopen")
    def test_malformed_response_is_error(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(b"not an Atom feed")
        result = collect_arxiv(categories=["cs.AI"])
        self.assertIsNotNone(result.error)

    @patch("ai_news_agent.collectors.arxiv.urlopen")
    def test_invalid_query_does_not_make_request(self, mock_urlopen):
        result = collect_arxiv(categories=["invalid category"], max_results=10)
        self.assertIn("invalid arXiv category", result.error)
        mock_urlopen.assert_not_called()
        result = collect_arxiv(categories=["cs.AI"], max_results=0)
        self.assertIn("max_results must be", result.error)

    @patch("ai_news_agent.collectors.arxiv.urlopen")
    def test_keyword_only_query(self, mock_urlopen):
        mock_urlopen.side_effect = lambda request, timeout: FakeResponse(ATOM_XML)
        with patch("ai_news_agent.collectors.arxiv.sleep"):
            result = collect_arxiv(categories=[], keywords=["learning"], max_results=3)
        self.assertIsNone(result.error)
        self.assertEqual(len(result.articles), 1)
        request = mock_urlopen.call_args.args[0]
        query = parse_qs(urlparse(request.full_url).query)["search_query"][0]
        self.assertTrue(query.startswith("cat:"))

    @patch("ai_news_agent.collectors.arxiv.sleep")
    @patch("ai_news_agent.collectors.arxiv.urlopen")
    def test_one_category_failure_keeps_other_results(self, mock_urlopen, mock_sleep):
        def open_query(request, timeout):
            if "cs.AI" in request.full_url:
                raise TimeoutError("timed out")
            return FakeResponse(ATOM_XML)

        mock_urlopen.side_effect = open_query
        result = collect_arxiv(categories=["cs.AI", "cs.LG"], max_results=2)
        self.assertIsNone(result.error)
        self.assertEqual(len(result.articles), 1)
        self.assertIn("cs.AI failed", result.warnings[0])
        mock_sleep.assert_called_once_with(3)

    @patch("ai_news_agent.collectors.arxiv.urlopen")
    def test_recent_window_excludes_old_papers(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(ATOM_XML)
        result = collect_arxiv(categories=["cs.AI"], recent_days=7)
        self.assertIsNone(result.error)
        self.assertEqual(result.articles, [])


if __name__ == "__main__":
    unittest.main()
