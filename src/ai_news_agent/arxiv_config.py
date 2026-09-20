"""Defaults for the arXiv CLI; change these to adjust its regular search."""

ARXIV_CATEGORIES: tuple[str, ...] = (
    "cs.AI",
    "cs.LG",
    "cs.CL",
    "cs.CV",
    "stat.ML",
)
ARXIV_KEYWORDS: tuple[str, ...] = ()
ARXIV_MAX_RESULTS = 20
ARXIV_RECENT_DAYS: int | None = 7
ARXIV_SCAN_LIMIT = 100  # Maximum metadata records examined per category.
