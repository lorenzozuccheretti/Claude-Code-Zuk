"""Apify: run a review-scraping actor synchronously and read its dataset.

Apify's store has several Amazon review actors and their input/output field
names differ, so the actor id comes from ``APIFY_REVIEWS_ACTOR`` and the
input is a template; output fields are read through a list of known aliases.
A record without a rating and a body is dropped, never defaulted.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..models import Review
from .base import ProviderError, check, http_client

BASE = "https://api.apify.com/v2"

# Field aliases seen across the popular Amazon review actors.
_RATING = ("ratingScore", "rating", "stars", "reviewRating", "score")
_TEXT = ("reviewDescription", "text", "reviewText", "body", "content")
_TITLE = ("reviewTitle", "title")
_DATE = ("date", "reviewDate", "reviewedIn")


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if record.get(key) not in (None, ""):
            return record[key]
    return None


def _rating(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, str) and value[:1].isdigit():
        return int(value[0])
    return None


def normalise(records: list[dict[str, Any]], asin: str = "") -> list[Review]:
    out = []
    for rec in records:
        rating, text = _rating(_first(rec, _RATING)), _first(rec, _TEXT)
        if rating is None or not text:
            continue
        out.append(Review(
            asin=str(rec.get("asin", asin)),
            rating=rating,
            title=str(_first(rec, _TITLE) or ""),
            text=str(text),
            date=str(_first(rec, _DATE) or ""),
        ))
    return out


class Apify:
    def __init__(
        self, token: str, actor: str, input_template: dict[str, Any] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not (token and actor):
            raise ProviderError("APIFY_TOKEN and APIFY_REVIEWS_ACTOR must both be set")
        self.token = token
        self.actor = actor.replace("/", "~")  # the API wants owner~name
        self.input_template = input_template or {
            "productUrls": [{"url": "https://www.amazon.it/dp/{asin}"}],
            "maxReviews": "{max_reviews}",
            "filterByRatings": ["oneStar", "twoStar", "threeStar"],
            "sort": "recent",
        }
        self.client = client or http_client(timeout=300.0)

    def _input(self, asin: str, max_reviews: int) -> dict[str, Any]:
        def fill(node: Any) -> Any:
            if isinstance(node, str):
                if node == "{max_reviews}":
                    return max_reviews
                return node.replace("{asin}", asin)
            if isinstance(node, list):
                return [fill(n) for n in node]
            if isinstance(node, dict):
                return {k: fill(v) for k, v in node.items()}
            return node

        return fill(self.input_template)

    def reviews(self, asin: str, max_reviews: int = 200) -> list[Review]:
        r = self.client.post(
            f"{BASE}/acts/{self.actor}/run-sync-get-dataset-items",
            params={"token": self.token, "format": "json", "clean": "true"},
            json=self._input(asin, max_reviews),
        )
        data = check(r, "apify")
        if not isinstance(data, list):
            raise ProviderError("apify: expected a list of dataset items")
        return normalise(data, asin)[:max_reviews]
