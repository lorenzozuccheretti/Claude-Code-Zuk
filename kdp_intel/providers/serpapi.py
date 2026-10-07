"""SerpAPI: Google (Italy), Google Trends (geo IT) and Amazon.it search.

One key, three engines. Every request pins the Italian market explicitly
(``google_domain=google.it``, ``gl=it``, ``hl=it``, ``geo=IT``,
``amazon_domain=amazon.it``) so a default never leaks US results in.
"""

from __future__ import annotations

import httpx

from ..models import Competitor, TrendPoint
from .base import ProviderError, SearchResult, check, http_client, parse_price

ENDPOINT = "https://serpapi.com/search.json"


class SerpAPI:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ProviderError("SERPAPI_API_KEY is not set")
        self.api_key = api_key
        self.client = client or http_client()

    def _get(self, params: dict) -> dict:
        data = check(self.client.get(ENDPOINT, params={**params, "api_key": self.api_key}), "serpapi")
        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(f"serpapi: {data['error']}")
        return data  # type: ignore[return-value]

    # -- WebSearch
    def search(self, query: str, num: int = 10) -> list[SearchResult]:
        data = self._get({
            "engine": "google", "q": query, "google_domain": "google.it",
            "gl": "it", "hl": "it", "num": num,
        })
        return [
            SearchResult(r.get("title", ""), r.get("link", ""), r.get("snippet", ""), r.get("date", ""))
            for r in data.get("organic_results", [])
            if r.get("link")
        ]

    # -- Trends
    def interest_over_time(self, query: str, timeframe: str = "today 12-m") -> list[TrendPoint]:
        data = self._get({
            "engine": "google_trends", "q": query, "geo": "IT", "hl": "it",
            "data_type": "TIMESERIES", "date": timeframe,
        })
        points = []
        for row in data.get("interest_over_time", {}).get("timeline_data", []):
            values = row.get("values") or [{}]
            points.append(TrendPoint(date=row.get("date", ""), value=int(values[0].get("extracted_value", 0))))
        return points

    # -- MarketplaceSearch
    def search_books(self, query: str, pages: int = 1) -> list[Competitor]:
        out: list[Competitor] = []
        for page in range(1, pages + 1):
            data = self._get({
                "engine": "amazon", "amazon_domain": "amazon.it", "k": query,
                "language": "it_IT", "page": page,
            })
            for r in data.get("organic_results", []):
                out.append(Competitor(
                    asin=r.get("asin", ""),
                    title=r.get("title", ""),
                    rating=r.get("rating"),
                    reviews=r.get("reviews"),
                    price_eur=parse_price(r.get("extracted_price", r.get("price"))),
                    url=r.get("link_clean", r.get("link", "")),
                ))
        return out
