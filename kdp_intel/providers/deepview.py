"""Amazon.it search pages exported from the browser (DeepView and similar extensions).

Amazon.it answers search and product pages from cloud networks with a captcha, so
the engine reads what you export from your own browser: one CSV per keyword, with
one row per search result (ASIN, title, format, price, reviews, rating, BSR,
estimated sales and royalty, pages, publication date, publisher type, URL).

Sales and royalty are the extension's estimates from the BSR, not Amazon data, and
are reported as such. Merchandise that Amazon mixes into book searches (t-shirts,
phone cases) is dropped by format, and "on topic" means the title is about the
keyword, so a search that also returns exam manuals is not counted as demand.

Put the files in one folder; each file's keyword is read from the ``keywords=``
parameter of its URLs, or else from the file name (``deepview-<keyword>.csv``).
"""

from __future__ import annotations

import csv
import re
import statistics
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlparse

from ..models import CatalogBook, Competitor, MarketBook, MarketSummary
from .base import ProviderError

BOOK_FORMATS = {"paperback", "hardcover", "kindle", "copertina flessibile", "copertina rigida", "ebook"}
_COLUMNS = {
    "asin": ("ASIN",), "title": ("Title", "Titolo"), "format": ("Format", "Formato"),
    "price": ("Price", "Prezzo"), "reviews": ("Reviews", "Recensioni"), "rating": ("Rating", "Valutazione"),
    "bsr": ("BSR",), "sales": ("Est sales/mo", "Est. sales/mo", "Sales/mo"),
    "royalty": ("Est royalty/mo", "Est. royalty/mo", "Royalty/mo"), "pages": ("Pages", "Pagine"),
    "published": ("Published", "Pubblicato"), "self": ("Self-published",), "ptype": ("Publisher type",),
    "url": ("URL",),
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def _stem_words(text: str) -> set[str]:
    """Word stems long enough to mean something: "amministratore" and "amministrazione" meet."""
    return {w[:7] for w in re.findall(r"\w+", _norm(text)) if len(w) > 3}


def is_about(title: str, keyword: str) -> bool:
    words = _stem_words(keyword)
    return bool(words) and words <= _stem_words(title)


def _num(value: str) -> float | None:
    value = (value or "").strip().replace("€", "").replace("\xa0", "")
    if not value:
        return None
    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    elif "," in value:
        value = value.replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _clean_title(raw: str) -> str:
    # Extensions copy the title cell with the byline markup that follows it on the page.
    return re.split(r"\s*<", raw, maxsplit=1)[0].strip()


def _keyword_of(path: Path, rows: list[dict[str, str]], url_col: str | None) -> str:
    for row in rows:
        query = parse_qs(urlparse(row.get(url_col or "", "")).query)
        if query.get("keywords"):
            return _norm(unquote_plus(query["keywords"][0]))
    stem = path.stem
    stem = re.sub(r"^[0-9a-f]{6,}-", "", stem)  # upload prefixes
    stem = re.sub(r"^deepview-", "", stem, flags=re.I)
    return _norm(stem.replace("-", " ").replace("_", " "))


def parse_export(path: str | Path) -> tuple[str, list[MarketBook]]:
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip() for h in reader.fieldnames or []]
        rows = [{(k or "").strip(): v for k, v in r.items()} for r in reader]
    col = {key: next((h for h in header if h in names), None) for key, names in _COLUMNS.items()}
    if not col["asin"] or not col["title"]:
        raise ProviderError(f"{path.name}: non sembra un export di ricerca Amazon (colonne: {', '.join(header)})")

    def get(row: dict[str, str], key: str) -> str:
        return (row.get(col[key]) or "") if col[key] else ""

    books = []
    for row in rows:
        fmt = get(row, "format").strip().lower()
        if col["format"] and fmt not in BOOK_FORMATS:
            continue  # merchandise and unknown formats are not books
        num = {k: _num(get(row, k)) for k in ("price", "reviews", "rating", "bsr", "sales", "royalty", "pages")}
        self_pub = get(row, "self").strip().lower() in {"yes", "si", "sì", "true"} or \
            "self" in get(row, "ptype").lower()
        books.append(MarketBook(
            asin=get(row, "asin").strip(), title=_clean_title(get(row, "title")), format=fmt,
            price_eur=num["price"], reviews=int(num["reviews"]) if num["reviews"] is not None else None,
            rating=num["rating"], bsr=int(num["bsr"]) if num["bsr"] else None,
            est_sales_month=num["sales"], est_royalty_month=num["royalty"],
            pages=int(num["pages"]) if num["pages"] else None,
            published=get(row, "published").strip()[:10], self_published=self_pub, url=get(row, "url").strip(),
        ))
    return _keyword_of(path, rows, col["url"]), books


def summarize(keyword: str, books: list[MarketBook], today: date, source: str = "") -> MarketSummary:
    on = [b for b in books if is_about(b.title, keyword)]
    since = date(today.year - 2, today.month, min(today.day, 28)).isoformat()
    prices = [b.price_eur for b in on if b.price_eur]
    pages = [b.pages for b in on if b.pages]
    bsrs = [b.bsr for b in on if b.bsr]
    return MarketSummary(
        keyword=keyword, books=len(books), on_topic=len(on),
        est_sales_month=round(sum(b.est_sales_month or 0 for b in on), 1),
        est_royalty_month=round(sum(b.est_royalty_month or 0 for b in on), 1),
        median_price_eur=round(statistics.median(prices), 2) if prices else None,
        median_pages=int(statistics.median(pages)) if pages else None,
        recent_titles=sum(1 for b in on if b.published and b.published >= since),
        best_bsr=min(bsrs) if bsrs else None,
        total_reviews=sum(b.reviews or 0 for b in on),
        self_published=sum(1 for b in on if b.self_published),
        source=source,
    )


class AmazonExports:
    """Every export in a folder, indexed by keyword."""

    def __init__(self, folder: str | Path, today: date | None = None) -> None:
        self.today = today or date.today()
        self.by_keyword: dict[str, tuple[Path, list[MarketBook]]] = {}
        for path in sorted(Path(folder).glob("*.csv")):
            keyword, books = parse_export(path)
            self.by_keyword[keyword] = (path, books)

    def _find(self, keyword: str) -> tuple[Path, list[MarketBook]] | None:
        return self.by_keyword.get(_norm(keyword))

    def has(self, keyword: str) -> bool:
        return self._find(keyword) is not None

    def summary(self, keyword: str) -> MarketSummary | None:
        found = self._find(keyword)
        return summarize(_norm(keyword), found[1], self.today, found[0].name) if found else None

    def on_topic(self, keyword: str) -> list[MarketBook]:
        found = self._find(keyword)
        return [b for b in found[1] if is_about(b.title, keyword)] if found else []

    # --- the interfaces the rest of the engine already uses

    def search_catalog(self, keyword: str) -> tuple[int | None, list[CatalogBook]]:
        """Competition from Amazon.it itself: the on-topic books of the exported search."""
        if not self.has(keyword):
            return None, []
        books = self.on_topic(keyword)
        return len(books), [CatalogBook(
            title=b.title, year=int(b.published[:4]) if b.published[:4].isdigit() else None,
            price_eur=b.price_eur, ratings=b.reviews, url=b.url) for b in books]

    def search_books(self, query: str, pages: int = 1) -> list[Competitor]:
        return [Competitor(asin=b.asin, title=b.title, rating=b.rating, reviews=b.reviews, price_eur=b.price_eur,
                           bsr=b.bsr, published=b.published, url=b.url) for b in self.on_topic(query)]

    def bsr(self, asin: str) -> int | None:
        for _, books in self.by_keyword.values():
            for b in books:
                if b.asin == asin and b.bsr:
                    return b.bsr
        return None
