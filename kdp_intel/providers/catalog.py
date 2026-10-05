"""The Italian book catalogue as a free competition signal.

Amazon.it answers search pages from cloud networks with HTTP 503, so the
books already on sale for a keyword are read from IBS.it, the largest
Italian online bookshop, whose search pages are public. It lists the same
publishers' books Amazon sells (KDP titles excepted), with year, price and
the number of ratings: enough to tell a crowded topic from one where the
newest manual predates the rules it explains.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote_plus

import httpx

from ..models import CatalogBook
from .base import ProviderError, parse_price

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_ITEM = re.compile(r'<div class="cc-product-list-item\b')
_TITLE = re.compile(r'class="cc-title"[^>]*>(.*?)</a>', re.S)
_HREF = re.compile(r'<a[^>]+href="(/[^"]+/e/\d+)"[^>]*class="cc-title"')
_AUTHOR = re.compile(r'class="cc-author-name"[^>]*>(.*?)</a>', re.S)
_PUBLISHER = re.compile(r'class="cc-publisher-name"[^>]*>(.*?)</a>\s*(\d{4})?', re.S)
_PRICE = re.compile(r'class="cc-price"[^>]*>(.*?)</span>', re.S)
_RATINGS = re.compile(r'class="cc-rating-number"[^>]*>\((\d+)\)')
_CATEGORY = re.compile(r'class="cc-category"[^>]*>(.*?)</span>', re.S)
_TOTAL = re.compile(r"([\d.]+)\s*</strong>\s*risultat", re.S)


def _text(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip().rstrip(",").strip()


def parse_ibs(page: str) -> tuple[int | None, list[CatalogBook]]:
    total = _TOTAL.search(page)
    books = []
    for block in _ITEM.split(page)[1:]:
        category = _CATEGORY.search(block)
        if category and not re.match(r"(Libri|Ebook)\b", _text(category.group(1))):
            continue  # films, music, stationery
        title = _TITLE.search(block)
        if not title:
            continue
        pub = _PUBLISHER.search(block)
        price = _PRICE.search(block)
        ratings = _RATINGS.search(block)
        href = _HREF.search(block)
        books.append(CatalogBook(
            title=_text(title.group(1)),
            author=_text(a.group(1)) if (a := _AUTHOR.search(block)) else "",
            publisher=_text(pub.group(1)) if pub else "",
            year=int(pub.group(2)) if pub and pub.group(2) else None,
            price_eur=parse_price(_text(price.group(1))) if price else None,
            ratings=int(ratings.group(1)) if ratings else None,
            url=f"https://www.ibs.it{href.group(1)}" if href else "",
        ))
    return (int(total.group(1).replace(".", "")) if total else None), books


class IbsCatalog:
    SEARCH = "https://www.ibs.it/search/?ts=as&query="

    def __init__(self, client: httpx.Client | None = None, folder=None) -> None:
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True,
                                             headers={"User-Agent": BROWSER_UA, "Accept-Language": "it-IT"})
        self.folder = folder

    def search_catalog(self, query: str) -> tuple[int | None, list[CatalogBook]]:
        from pathlib import Path  # noqa: PLC0415
        import hashlib  # noqa: PLC0415

        cache = None
        if self.folder:
            Path(self.folder).mkdir(parents=True, exist_ok=True)
            cache = Path(self.folder) / (hashlib.sha256(query.encode()).hexdigest()[:16] + ".html")
            if cache.exists():
                return parse_ibs(cache.read_text(encoding="utf-8"))
        r = self.client.get(self.SEARCH + quote_plus(query))
        if r.status_code != 200 or ("cc-product-list" not in r.text and "risultat" not in r.text):
            raise ProviderError(f"ibs.it: HTTP {r.status_code}")
        if cache:
            cache.write_text(r.text, encoding="utf-8")
        return parse_ibs(r.text)
