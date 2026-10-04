"""Providers that cost nothing.

| capability        | provider                | cost                                   |
|-------------------|-------------------------|----------------------------------------|
| web search        | ``TavilySearch``        | free key, 1,000 searches/month, no card |
| web search        | ``DuckDuckGoSearch``    | no key; often refused from cloud IPs    |
| demand signal     | ``Suggestions``         | Amazon.it + Google autocomplete, no key |
| trends            | ``TrendsCSV``           | CSV exported from trends.google.it      |
| competitors, BSR, | ``SavedAmazonPages``    | pages you save from your own browser    |
| reviews           |                         | (Ctrl+S, "pagina web completa")         |
| forum threads     | ``providers.reddit``    | free Reddit "script" app credentials    |

``CachedSearch`` keeps every web search answer on disk: reruns and resumed
runs spend no quota.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from ..models import Competitor, KeywordMetric, Review, TrendPoint
from .amazon_html import parse_bsr, parse_reviews, parse_search_results
from .base import ProviderError, SearchResult, check, http_client

_SITE = re.compile(r"\s*site:(\S+)")


class TavilySearch:
    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ProviderError("TAVILY_API_KEY non impostata (gratuita su tavily.com)")
        self.api_key = api_key
        self.client = client or http_client()

    def search(self, query: str, num: int = 10) -> list[SearchResult]:
        domains = _SITE.findall(query)
        body = {"query": _SITE.sub("", query).strip(), "max_results": min(num, 20),
                "search_depth": "basic", "country": "italy", "include_published_date": True}
        if domains:
            body["include_domains"] = domains
        data = check(self.client.post(self.ENDPOINT, json=body,
                                      headers={"Authorization": f"Bearer {self.api_key}"}), "tavily")
        assert isinstance(data, dict)
        return [SearchResult(r.get("title", ""), r["url"], r.get("content", ""), r.get("published_date") or "")
                for r in data.get("results", []) if r.get("url")]


class DuckDuckGoSearch:
    """The HTML endpoint. No key, but it answers bots with a challenge page
    (HTTP 202) from many cloud networks; then it raises instead of
    pretending there were no results."""

    ENDPOINT = "https://html.duckduckgo.com/html/"
    _LINK = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    _SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True,
                                             headers={"User-Agent": "Mozilla/5.0 (kdp-intel)"})

    def search(self, query: str, num: int = 10) -> list[SearchResult]:
        r = self.client.post(self.ENDPOINT, data={"q": query, "kl": "it-it"})
        if r.status_code != 200 or "result__a" not in r.text:
            raise ProviderError(f"duckduckgo: HTTP {r.status_code}, nessun risultato leggibile")
        out = []
        snippets = self._SNIPPET.findall(r.text)
        for i, (href, title) in enumerate(self._LINK.findall(r.text)[:num]):
            if "uddg=" in href:
                href = unquote(parse_qs(urlparse(href).query)["uddg"][0])
            clean = re.sub(r"<[^>]+>", "", title).strip()
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
            out.append(SearchResult(clean, href, snippet))
        return out


class CachedSearch:
    def __init__(self, inner, folder: Path) -> None:
        self.inner, self.folder = inner, Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def search(self, query: str, num: int = 10) -> list[SearchResult]:
        key = hashlib.sha256(f"{query}|{num}".encode()).hexdigest()[:16]
        path = self.folder / f"{key}.json"
        if path.exists():
            return [SearchResult(**r) for r in json.loads(path.read_text(encoding="utf-8"))["results"]]
        results = self.inner.search(query, num)
        path.write_text(json.dumps({"query": query, "results": [r.__dict__ for r in results]},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        return results


class Suggestions:
    """Autocomplete as a free demand signal.

    Amazon and Google only suggest what people actually type. For each seed
    we ask both engines for the seed and the seed followed by a space, and
    count distinct long-tail suggestions that extend it. It is not a search
    volume and is reported as a proxy, but a seed nobody types gets none.
    """

    AMAZON = "https://completion.amazon.it/api/2017/suggestions"
    GOOGLE = "https://suggestqueries.google.com/complete/search"

    def __init__(self, client: httpx.Client | None = None, folder: Path | None = None) -> None:
        self.client = client or http_client(timeout=20.0)
        self.folder = folder
        if folder:
            Path(folder).mkdir(parents=True, exist_ok=True)

    def _cached(self, key: str, fetch) -> list[str]:
        if self.folder:
            path = Path(self.folder) / (hashlib.sha256(key.encode()).hexdigest()[:16] + ".json")
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            value = fetch()
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            return value
        return fetch()

    def amazon(self, prefix: str) -> list[str]:
        def fetch() -> list[str]:
            data = check(self.client.get(self.AMAZON, params={
                "limit": 11, "prefix": prefix, "suggestion-type": "KEYWORD", "page-type": "Search",
                "alias": "stripbooks", "site-variant": "desktop", "mid": "APJ6JRA9NG5V4", "lop": "it_IT"}),
                "amazon-suggest")
            return [s["value"] for s in data.get("suggestions", []) if s.get("value")]  # type: ignore[union-attr]
        return self._cached(f"amazon|{prefix}", fetch)

    def google(self, prefix: str) -> list[str]:
        def fetch() -> list[str]:
            data = check(self.client.get(self.GOOGLE, params={"client": "firefox", "hl": "it", "gl": "it",
                                                              "q": prefix}), "google-suggest")
            return list(data[1]) if isinstance(data, list) and len(data) > 1 else []
        return self._cached(f"google|{prefix}", fetch)

    def volumes(self, keywords: list[str]) -> list[KeywordMetric]:
        out = []
        for kw in keywords:
            low = kw.lower()
            amazon = {s.lower() for p in (kw, f"{kw} ") for s in self.amazon(p)}
            google = {s.lower() for p in (kw, f"{kw} ") for s in self.google(p)}
            longtail = {s for s in amazon | google if s.startswith(low) and s != low}
            out.append(KeywordMetric(
                keyword=kw, search_volume=None, source="autocomplete",
                suggestions=len(longtail), on_amazon=low in amazon or bool(amazon & longtail),
            ))
        return out


class TrendsCSV:
    """Google Trends exports ("Scarica CSV" on trends.google.it, Italia,
    ultimi 12 mesi). One file per search or one file with several terms;
    each column is matched to a seed by its header ("<term>: (Italia)")."""

    def __init__(self, folder: str | Path) -> None:
        self.series: dict[str, list[TrendPoint]] = {}
        for path in sorted(Path(folder).glob("*.csv")):
            self._read(path.read_text(encoding="utf-8-sig"))

    def _read(self, text: str) -> None:
        rows = list(csv.reader(io.StringIO(text)))
        header_at = next((i for i, r in enumerate(rows) if len(r) > 1 and ":" in r[1]), None)
        if header_at is None:
            return
        terms = [h.split(":")[0].strip().lower() for h in rows[header_at][1:]]
        for row in rows[header_at + 1:]:
            if len(row) < 2 or not re.match(r"\d{4}-\d{2}", row[0]):
                continue
            for term, value in zip(terms, row[1:]):
                v = 0 if value.strip().startswith("<") else int(value or 0)
                self.series.setdefault(term, []).append(TrendPoint(date=row[0], value=v))

    def interest_over_time(self, query: str, timeframe: str = "today 12-m") -> list[TrendPoint]:
        return self.series.get(query.lower(), [])


class SavedAmazonPages:
    """Amazon.it pages saved from your browser into one folder: search
    results for your keywords, competitors' product pages (for the BSR) and
    their review pages ("Vedi altre recensioni", filtered by 1-3 stars)."""

    def __init__(self, folder: str | Path) -> None:
        self.pages = [(p, p.read_text(encoding="utf-8", errors="replace"))
                      for p in sorted(Path(folder).glob("*.htm*"))]

    def search_books(self, query: str, pages: int = 1) -> list[Competitor]:
        words = set(query.lower().split())
        out: dict[str, Competitor] = {}
        for path, page in self.pages:
            if "s-search-result" not in page:
                continue
            title = re.search(r"<title>(.*?)</title>", page, re.S)
            label = (title.group(1) if title else path.stem).lower()
            if words and not words & set(re.findall(r"\w+", label)):
                continue  # a saved search for another keyword
            for c in parse_search_results(page):
                out.setdefault(c.asin, c)
        return list(out.values())

    def bsr(self, asin: str) -> int | None:
        for _, page in self.pages:
            if asin in page and "s-search-result" not in page:
                rank = parse_bsr(page)
                if rank:
                    return rank
        return None

    def reviews(self, asin: str, max_reviews: int = 200) -> list[Review]:
        out: list[Review] = []
        for _, page in self.pages:
            if asin in page and 'data-hook="review"' in page:
                out.extend(parse_reviews(page, asin))
        return out[:max_reviews]
