"""Parsers for Amazon.it pages fetched through an unblocker (Bright Data).

Amazon's markup changes, but two things have been stable for years and are
all the engine relies on: review blocks carry ``data-hook`` attributes, and
the product details list the Best Sellers Rank as
"Posizione nella classifica Bestseller di Amazon: n. 12.345 in Libri".
When a parser finds nothing it returns nothing; it never guesses.
"""

from __future__ import annotations

import html as html_lib
import re

from ..models import Review

_TAG = re.compile(r"<[^>]+>")
_REVIEW_BLOCK = re.compile(r'<div[^>]+data-hook="review"[^>]*>(.*?)(?=<div[^>]+data-hook="review"|\Z)', re.S)
_STARS = re.compile(r'data-hook="(?:review|cmps-review)-star-rating"[^>]*>.*?(\d)[,.]0 su 5', re.S)
_TITLE = re.compile(r'data-hook="review-title"[^>]*>(.*?)</(?:a|span)>\s*</', re.S)
_BODY = re.compile(r'data-hook="review-body"[^>]*>(.*?)</span>\s*</', re.S)
_DATE = re.compile(r'data-hook="review-date"[^>]*>(.*?)</span>', re.S)
_BSR = re.compile(
    r"(?:Posizione nella classifica Bestseller di Amazon|Best Sellers Rank)\s*:?\s*(?:</span>)?\s*"
    r"(?:n\.\s*)?([\d.]+)\s+in\s+(Libri|Kindle Store|Books)",
    re.S | re.I,
)


def _clean(fragment: str) -> str:
    text = html_lib.unescape(_TAG.sub(" ", fragment))
    return re.sub(r"\s+", " ", text).strip()


def parse_reviews(page: str, asin: str = "") -> list[Review]:
    out = []
    for block in _REVIEW_BLOCK.findall(page):
        stars, body = _STARS.search(block), _BODY.search(block)
        if not (stars and body):
            continue
        title, when = _TITLE.search(block), _DATE.search(block)
        out.append(Review(
            asin=asin,
            rating=int(stars.group(1)),
            title=_clean(title.group(1)) if title else "",
            text=_clean(body.group(1)),
            date=_clean(when.group(1)) if when else "",
        ))
    return out


def parse_bsr(page: str) -> int | None:
    """The book's rank in the whole Libri store, or None if absent."""
    m = _BSR.search(_TAG.sub(" ", page).replace("\xa0", " ") if "<" in page else page)
    return int(m.group(1).replace(".", "")) if m else None
