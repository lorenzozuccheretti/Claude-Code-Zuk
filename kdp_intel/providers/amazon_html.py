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

from ..models import Competitor, Review
from .base import parse_price

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


_RESULT = re.compile(
    r'<div[^>]+data-asin="([A-Z0-9]{10})"[^>]+data-component-type="s-search-result"[^>]*>(.*?)'
    r'(?=<div[^>]+data-asin="[A-Z0-9]{10}"[^>]+data-component-type="s-search-result"|\Z)', re.S)
_RESULT_ALT = re.compile(  # attribute order varies between page versions
    r'<div[^>]+data-component-type="s-search-result"[^>]+data-asin="([A-Z0-9]{10})"[^>]*>(.*?)'
    r'(?=<div[^>]+data-component-type="s-search-result"|\Z)', re.S)
_H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
_RATING = re.compile(r'class="a-icon-alt">\s*(\d)[,.](\d) su 5 stelle')
_COUNT = re.compile(r'aria-label="([\d.]+) (?:valutazioni|recensioni)"|s-underline-text">\s*\(?([\d.]+)\)?\s*<')
_OFFSCREEN = re.compile(r'class="a-offscreen">([^<]*\d[^<]*)</span>')


def parse_search_results(page: str) -> list[Competitor]:
    """Organic results of a saved Amazon.it search page (sponsored included,
    as the shopper sees them)."""
    blocks = _RESULT.findall(page) or _RESULT_ALT.findall(page)
    out, seen = [], set()
    for asin, block in blocks:
        if asin in seen:
            continue
        seen.add(asin)
        title = _H2.search(block)
        rating = _RATING.search(block)
        count = _COUNT.search(block)
        price = _OFFSCREEN.search(block)
        out.append(Competitor(
            asin=asin,
            title=_clean(title.group(1)) if title else "",
            rating=float(f"{rating.group(1)}.{rating.group(2)}") if rating else None,
            reviews=int((count.group(1) or count.group(2)).replace(".", "")) if count else None,
            price_eur=parse_price(html_lib.unescape(price.group(1))) if price else None,
            url=f"https://www.amazon.it/dp/{asin}",
        ))
    return out
