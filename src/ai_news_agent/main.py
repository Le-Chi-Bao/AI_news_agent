"""Run the RSS or arXiv collector from the command line."""

import argparse
import os
import sqlite3

from dotenv import load_dotenv

# Load only the real .env file (python-dotenv does not load .env.example).
load_dotenv()

from ai_news_agent.arxiv_config import (
    ARXIV_CATEGORIES, ARXIV_KEYWORDS, ARXIV_MAX_RESULTS, ARXIV_RECENT_DAYS,
)
from ai_news_agent.collectors.search import SearchProviderError, TavilySearchProvider, collect_search
from ai_news_agent.collectors.arxiv import collect_arxiv
from ai_news_agent.collectors.rss import collect_feeds
from ai_news_agent.rss_sources import RSS_FEED_URLS
from ai_news_agent.storage import ArticleRepository
from ai_news_agent.search_config import SEARCH_MAX_RESULTS, SEARCH_QUERIES, SEARCH_TIME_RANGE
from ai_news_agent.processing import ArticleProcessor, LLMProviderError, OpenAIProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect and store RSS articles or arXiv papers")
    parser.add_argument("--source", choices=("rss", "arxiv", "search", "all"), default="rss")
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "data/ai_news.db"),
                        metavar="PATH", help="SQLite database path")
    parser.add_argument(
        "--feed", action="append", metavar="URL",
        help="RSS URL to collect; repeat for multiple feeds (replaces defaults)",
    )
    parser.add_argument("--category", action="append", help="arXiv category; repeat to add more")
    parser.add_argument("--keyword", action="append", help="arXiv keyword or phrase; repeat to add more")
    parser.add_argument("--max-results", type=int, default=ARXIV_MAX_RESULTS)
    parser.add_argument("--recent-days", type=int, default=ARXIV_RECENT_DAYS,
                        help="arXiv submission window in days; 0 disables date filter")
    parser.add_argument("--query", action="append", help="Web search query; repeat to add more")
    parser.add_argument("--search-max-results", type=int, default=SEARCH_MAX_RESULTS)
    parser.add_argument("--analyze", action="store_true", help="Analyze only newly inserted articles with the LLM")
    args = parser.parse_args(argv)

    articles = []
    source_errors = 0
    if args.source in ("rss", "all"):
        rss_articles, rss_errors = _run_rss(args)
        articles.extend(rss_articles)
        source_errors += rss_errors
    if args.source in ("arxiv", "all"):
        arxiv_articles, arxiv_errors = _run_arxiv(args)
        articles.extend(arxiv_articles)
        source_errors += arxiv_errors
    if args.source in ("search", "all"):
        search_articles, search_errors = _run_search(args)
        articles.extend(search_articles)
        source_errors += search_errors

    try:
        with ArticleRepository(args.database) as repository:
            saved = repository.save_many(articles)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"\n[ERROR] Database: {exc}")
        return 1

    print(f"\nCollected: {len(articles)}")
    print(f"New: {saved.inserted}")
    print(f"Duplicates: {saved.duplicates}")
    print(f"Failed: {saved.failed}")
    print(f"Database: {args.database}")
    for error in saved.errors:
        print(f"  [ERROR] {error}")
    analysis_failed = 0
    if args.analyze:
        try:
            processor = ArticleProcessor(OpenAIProvider())
        except LLMProviderError as exc:
            print(f"[ERROR] LLM configuration: {exc}")
            return 1
        with ArticleRepository(args.database) as repository:
            # Persist one by one so failures retain the correct article_id relationship.
            from ai_news_agent.processing.llm import BatchResult
            batch = BatchResult()
            for article_id, article in zip(saved.inserted_ids, saved.inserted_articles):
                try:
                    analysis = processor.analyze(article)
                    repository.save_analysis(article_id, analysis, processor.provider.model)
                    batch.processed += 1
                    batch.results.append(analysis)
                except Exception as exc:
                    batch.failed += 1
                    batch.errors.append(f"article {article_id}: {exc}")
        print(f"Analysis processed: {batch.processed}")
        print(f"Analysis failed: {batch.failed}")
        analysis_failed = batch.failed
        for error in batch.errors:
            print(f"  [ERROR] {error}")
    return 1 if source_errors or saved.failed or analysis_failed else 0


def _run_rss(args: argparse.Namespace) -> tuple[list, int]:
    print("Collecting RSS feeds...\n")
    results = collect_feeds(args.feed if args.feed is not None else RSS_FEED_URLS)
    for result in results:
        if result.error:
            print(f"[ERROR] {result.name}: {result.error}")
        else:
            print(f"[OK] {result.name}: {len(result.articles)} articles")
        for warning in result.warnings:
            print(f"  [WARN] {warning}")

    articles = [article for result in results for article in result.articles]
    samples = articles[:5]
    if samples:
        print("\nSample articles:")
        for article in samples:
            print(f"Title: {article.title}")
            print(f"Source: {article.source}")
            print(f"Published: {article.published_at.isoformat() if article.published_at else 'unknown'}")
            print(f"URL: {article.url}\n")
    return articles, sum(bool(result.error) for result in results)


def _run_arxiv(args: argparse.Namespace) -> tuple[list, int]:
    print("Collecting arXiv papers...\n")
    categories = args.category if args.category is not None else (
        () if args.keyword else ARXIV_CATEGORIES
    )
    result = collect_arxiv(
        categories=categories,
        keywords=args.keyword if args.keyword is not None else ARXIV_KEYWORDS,
        max_results=args.max_results,
        recent_days=args.recent_days or None,
    )
    if result.error:
        print(f"[ERROR] arXiv: {result.error}")
        return result.articles, 1
    print(f"[OK] Collected: {len(result.articles)} papers")
    for warning in result.warnings:
        print(f"  [WARN] {warning}")
    for article in result.articles[:5]:
        print(f"\nTitle: {article.title}")
        print(f"Authors: {', '.join(article.authors) if article.authors else 'unknown'}")
        print(f"Published: {article.published_at.isoformat() if article.published_at else 'unknown'}")
        print(f"URL: {article.url}")
    return result.articles, 0


def _run_search(args: argparse.Namespace) -> tuple[list, int]:
    print("Collecting Web Search...\n")
    try:
        provider = TavilySearchProvider()
    except SearchProviderError as exc:
        print(f"[ERROR] search configuration: {exc}")
        return [], 1
    results = collect_search(
        args.query if args.query is not None else SEARCH_QUERIES,
        provider,
        max_results=args.search_max_results,
        time_range=SEARCH_TIME_RANGE,
    )
    articles = []
    for result in results:
        if result.error:
            print(f"[ERROR] {result.query}: {result.error}")
        else:
            print(f"[OK] {result.query}: {len(result.articles)} results")
            articles.extend(result.articles)
        for warning in result.warnings:
            print(f"  [WARN] {warning}")
    return articles, sum(bool(result.error) for result in results)


if __name__ == "__main__":
    raise SystemExit(main())
