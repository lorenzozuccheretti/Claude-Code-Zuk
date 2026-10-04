"""What an agent may ask the outside world, independent of who answers.

Each capability is a small protocol. Concrete providers (SerpAPI, Apify,
Bright Data, DataForSEO, Reddit, a Helium 10 export) implement one or more of
them; ``fixtures.py`` implements all of them from files, so the whole
pipeline runs offline in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from ..models import Competitor, KeywordMetric, Review, TrendPoint

USER_AGENT = "kdp-intel/0.1 (+research; contact: publisher)"


class ProviderError(RuntimeError):
    """A provider answered, but not with data we can use."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    date: str = ""


class WebSearch(Protocol):
    def search(self, query: str, num: int = 10) -> list[SearchResult]: ...


class Trends(Protocol):
    def interest_over_time(self, query: str, timeframe: str = "today 12-m") -> list[TrendPoint]: ...


class MarketplaceSearch(Protocol):
    def search_books(self, query: str, pages: int = 1) -> list[Competitor]: ...


class ReviewSource(Protocol):
    def reviews(self, asin: str, max_reviews: int = 200) -> list[Review]: ...


class KeywordVolumes(Protocol):
    def volumes(self, keywords: list[str]) -> list[KeywordMetric]: ...


class PageFetcher(Protocol):
    def fetch(self, url: str) -> str: ...


class CommunitySearch(Protocol):
    def threads(self, subreddit: str, query: str, limit: int = 25) -> list[dict]: ...


def parse_price(value: object) -> float | None:
    """A price as a float: 19.9, "19,90 €", "1.234,56" or "€24.90"."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    m = re.search(r"\d[\d.,]*", value)
    if not m:
        return None
    raw = m.group(0).rstrip(".,")
    if "," in raw:  # Italian: dots group thousands, the comma is decimal
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(".") > 1 or re.search(r"\.\d{3}$", raw):
        raw = raw.replace(".", "")
    return float(raw)


def http_client(timeout: float = 60.0) -> httpx.Client:
    """httpx honours HTTPS_PROXY and SSL_CERT_FILE from the environment."""
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def check(response: httpx.Response, service: str) -> dict | list:
    if response.status_code >= 400:
        raise ProviderError(f"{service}: HTTP {response.status_code}: {response.text[:300]}")
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(f"{service}: response is not JSON") from exc


class DirectFetcher:
    """Plain HTTP GET; enough for government sites and most press."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or http_client()

    def fetch_bytes(self, url: str) -> tuple[bytes, str]:
        r = self.client.get(url)
        if r.status_code >= 400:
            raise ProviderError(f"GET {url}: HTTP {r.status_code}")
        return r.content, r.headers.get("content-type", "")

    def fetch(self, url: str) -> str:
        body, _ = self.fetch_bytes(url)
        return body.decode("utf-8", errors="replace")
