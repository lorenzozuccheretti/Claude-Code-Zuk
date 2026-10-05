"""Every provider protocol, answered from one JSON file.

Used by the tests and by ``--offline`` runs. The file mirrors what the live
providers return after normalisation, so a fixture is also documentation of
exactly what each agent consumes:

    {"search":   {"<query>": [{"title", "url", "snippet"}]},
     "trends":   {"<query>": [{"date", "value"}]},
     "books":    {"<query>": [Competitor...]},
     "reviews":  {"<asin>":  [Review...]},
     "volumes":  {"<keyword>": {"search_volume", "competing_products"}},
     "pages":    {"<url>": "<html or text>"},
     "threads":  {"<subreddit>": [{"url", "title", "text", "comments", "created"}]},
     "bsr":      {"<asin>": 12345},
     "suggest":  {"amazon": ["<phrase people type>", ...], "google": [...]},
     "catalog":  {"<query>": {"total": 27, "books": [CatalogBook...]}}}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import CatalogBook, Competitor, KeywordMetric, Review, TrendPoint
from .base import ProviderError, SearchResult


class Fixtures:
    def __init__(self, data: dict[str, Any] | str | Path) -> None:
        if not isinstance(data, dict):
            data = json.loads(Path(data).read_text(encoding="utf-8"))
        self.data = data
        self.fetched: list[str] = []

    def _section(self, name: str) -> dict[str, Any]:
        return self.data.get(name, {})

    def search(self, query: str, num: int = 10) -> list[SearchResult]:
        rows = self._section("search").get(query)
        if rows is None:  # live search matches loosely; so does the fixture
            rows = [r for q, rs in self._section("search").items()
                    if set(q.lower().split()) & set(query.lower().split()) for r in rs]
        return [SearchResult(r["title"], r["url"], r.get("snippet", ""), r.get("date", ""))
                for r in rows[:num]]

    def interest_over_time(self, query: str, timeframe: str = "today 12-m") -> list[TrendPoint]:
        return [TrendPoint(**p) for p in self._section("trends").get(query, [])]

    def search_books(self, query: str, pages: int = 1) -> list[Competitor]:
        return [Competitor(**c) for c in self._section("books").get(query, [])]

    def reviews(self, asin: str, max_reviews: int = 200) -> list[Review]:
        return [Review(asin=asin, **r) for r in self._section("reviews").get(asin, [])][:max_reviews]

    def volumes(self, keywords: list[str]) -> list[KeywordMetric]:
        table = self._section("volumes")
        return [KeywordMetric(keyword=k, source="fixture", **table[k]) for k in keywords if k in table]

    def fetch_bytes(self, url: str) -> tuple[bytes, str]:
        pages = self._section("pages")
        if url not in pages:
            raise ProviderError(f"fixture has no page for {url}")
        self.fetched.append(url)
        return pages[url].encode("utf-8"), "text/html"

    def fetch(self, url: str) -> str:
        return self.fetch_bytes(url)[0].decode("utf-8")

    def threads(self, subreddit: str, query: str, limit: int = 25) -> list[dict]:
        return self._section("threads").get(subreddit, [])[:limit]

    def bsr(self, asin: str) -> int | None:
        return self._section("bsr").get(asin)

    # autocomplete: the phrases of the pool that start with what was typed, as the engines do
    def _complete(self, engine: str, prefix: str) -> list[str]:
        low = prefix.lower()
        return [s for s in self._section("suggest").get(engine, []) if s.lower().startswith(low)][:10]

    def amazon(self, prefix: str) -> list[str]:
        return self._complete("amazon", prefix)

    def google(self, prefix: str) -> list[str]:
        return self._complete("google", prefix)

    def search_catalog(self, query: str) -> tuple[int | None, list[CatalogBook]]:
        row = self._section("catalog").get(query)
        if row is None:
            return 0, []
        return row.get("total"), [CatalogBook(**b) for b in row.get("books", [])]
