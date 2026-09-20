"""RSS collector tests with local XML responses, independent of the network."""

import io
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from ai_news_agent.collectors.rss import collect_feed, collect_feeds
from ai_news_agent.models import SourceType


RSS_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>AI Source</title>
<item><guid>story-1</guid><title>New model</title>
<link>https://example.com/story-1</link>
<pubDate>Fri, 19 Sep 2025 12:30:00 GMT</pubDate>
<description>Short description</description></item>
<item><title>Undated article</title><link>https://example.com/story-2</link></item>
<item><title>Missing link</title><description>Skip me</description></item>
</channel></rss>"""


class FakeResponse(io.BytesIO):
    def __init__(self, content: bytes, url: str):
        super().__init__(content)
        self.url = url

    def geturl(self) -> str:
        return self.url


class RssCollectorTests(unittest.TestCase):
    @patch("ai_news_agent.collectors.rss.urlopen")
    def test_maps_entries_and_skips_incomplete_entry(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(RSS_XML, "https://example.com/feed")
        result = collect_feed("https://example.com/feed")

        self.assertIsNone(result.error)
        self.assertEqual(result.name, "AI Source")
        self.assertEqual(len(result.articles), 2)
        first, second = result.articles
        self.assertEqual(first.source, "AI Source")
        self.assertEqual(first.source_type, SourceType.RSS)
        self.assertEqual(first.source_id, "story-1")
        self.assertEqual(first.summary, "Short description")
        self.assertEqual(first.published_at, datetime(2025, 9, 19, 12, 30, tzinfo=timezone.utc))
        self.assertIsNone(second.published_at)
        self.assertIsNone(second.summary)
        self.assertEqual(len(result.warnings), 1)

    @patch("ai_news_agent.collectors.rss.urlopen")
    def test_failed_feed_does_not_stop_other_feeds(self, mock_urlopen):
        def open_feed(request, timeout):
            if request.full_url.endswith("bad"):
                raise OSError("network unavailable")
            return FakeResponse(RSS_XML, request.full_url)

        mock_urlopen.side_effect = open_feed
        results = collect_feeds(["https://example.com/bad", "https://example.com/good"])
        self.assertIn("network unavailable", results[0].error)
        self.assertIsNone(results[1].error)
        self.assertEqual(len(results[1].articles), 2)

    @patch("ai_news_agent.collectors.rss.urlopen")
    def test_malformed_feed_reports_error(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(b"not XML", "https://example.com/feed")
        result = collect_feed("https://example.com/feed")
        self.assertIsNotNone(result.error)
        self.assertEqual(result.articles, [])

    @patch("ai_news_agent.collectors.rss.urlopen")
    def test_content_takes_priority_over_description(self, mock_urlopen):
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">
        <title>Atom Source</title><entry><title>Article</title>
        <link href="https://example.com/article"/><id>abc</id>
        <content type="text">Full content</content><summary>Short description</summary>
        </entry></feed>'''
        mock_urlopen.return_value = FakeResponse(atom, "https://example.com/atom")
        result = collect_feed("https://example.com/atom")
        self.assertIsNone(result.error)
        self.assertEqual(result.articles[0].summary, "Full content")


if __name__ == "__main__":
    unittest.main()
