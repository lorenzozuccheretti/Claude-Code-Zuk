"""Bright Data Web Unlocker: fetch pages that block plain HTTP clients.

Uses the direct API (``POST https://api.brightdata.com/request``) with a
Web Unlocker zone. Amazon.it product and review pages go through here; the
parsers in ``amazon_html`` turn them into reviews and a BSR.
"""

from __future__ import annotations

import httpx

from ..models import Review
from .amazon_html import parse_bsr, parse_reviews
from .base import ProviderError, http_client

ENDPOINT = "https://api.brightdata.com/request"


class BrightData:
    def __init__(self, token: str, zone: str, client: httpx.Client | None = None) -> None:
        if not (token and zone):
            raise ProviderError("BRIGHTDATA_API_TOKEN and BRIGHTDATA_ZONE must both be set")
        self.token, self.zone = token, zone
        self.client = client or http_client(timeout=120.0)

    def fetch(self, url: str) -> str:
        r = self.client.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {self.token}"},
            json={"zone": self.zone, "url": url, "format": "raw", "country": "it"},
        )
        if r.status_code >= 400:
            raise ProviderError(f"brightdata: HTTP {r.status_code}: {r.text[:300]}")
        return r.text

    def fetch_bytes(self, url: str) -> tuple[bytes, str]:
        return self.fetch(url).encode("utf-8"), "text/html"

    # -- ReviewSource: critical reviews first, which is what the miner wants
    def reviews(self, asin: str, max_reviews: int = 200) -> list[Review]:
        out: list[Review] = []
        for star in ("one_star", "two_star", "three_star"):
            page = 1
            while len(out) < max_reviews and page <= 10:
                url = (
                    f"https://www.amazon.it/product-reviews/{asin}/?filterByStar={star}"
                    f"&reviewerType=all_reviews&sortBy=recent&pageNumber={page}"
                )
                batch = parse_reviews(self.fetch(url), asin)
                if not batch:
                    break
                out.extend(batch)
                page += 1
        return out[:max_reviews]

    def bsr(self, asin: str) -> int | None:
        return parse_bsr(self.fetch(f"https://www.amazon.it/dp/{asin}"))
