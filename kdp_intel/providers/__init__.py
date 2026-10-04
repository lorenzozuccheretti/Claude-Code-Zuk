"""Choose a concrete provider for each capability from the credentials at hand.

Preference order is fixed and visible here: free first (a free API key,
autocomplete, files you export or save yourself), a paid service only when
its credentials are present, and a missing capability is reported as a gap
in the run log instead of being filled with invented data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Credentials, Project
from .base import (
    CommunitySearch, DirectFetcher, KeywordVolumes, MarketplaceSearch, PageFetcher,
    ProviderError, ReviewSource, Trends, WebSearch,
)


@dataclass
class Providers:
    web_search: WebSearch | None = None
    trends: Trends | None = None
    marketplace: MarketplaceSearch | None = None
    reviews: ReviewSource | None = None
    volumes: list[KeywordVolumes] = field(default_factory=list)
    fetcher: PageFetcher | None = None
    unblocker: PageFetcher | None = None  # for pages that refuse plain HTTP
    community: CommunitySearch | None = None
    bsr: Any = None  # anything with .bsr(asin) -> int | None
    gaps: list[str] = field(default_factory=list)

    @classmethod
    def offline(cls, fixtures: Any) -> "Providers":
        return cls(
            web_search=fixtures, trends=fixtures, marketplace=fixtures, reviews=fixtures,
            volumes=[fixtures], fetcher=fixtures, unblocker=fixtures, community=fixtures,
            bsr=fixtures,
        )


def build_providers(creds: Credentials, project: Project | None = None,
                    cache_dir: Path | None = None) -> Providers:
    """Free providers first; a paid one only replaces a free one when its
    credentials are present."""
    from .apify import Apify
    from .brightdata import BrightData
    from .dataforseo import DataForSEO
    from .free import CachedSearch, DuckDuckGoSearch, SavedAmazonPages, Suggestions, TavilySearch, TrendsCSV
    from .helium10 import Helium10Export
    from .reddit import Reddit
    from .serpapi import SerpAPI

    research = project.research if project else None
    cache = Path(cache_dir) if cache_dir else None
    p = Providers(fetcher=DirectFetcher())

    # web search: Tavily (free key) > SerpAPI (paid) > DuckDuckGo (no key, often blocked)
    search = None
    if creds.tavily_key:
        search = TavilySearch(creds.tavily_key)
    elif creds.serpapi_key:
        search = SerpAPI(creds.serpapi_key)
    else:
        search = DuckDuckGoSearch()
        p.gaps.append("nessuna TAVILY_API_KEY (gratuita): ricerca web via DuckDuckGo, spesso bloccata")
    p.web_search = CachedSearch(search, cache / "search") if cache else search

    # trends: exported CSV (free) > SerpAPI
    if research and research.trends_dir:
        p.trends = TrendsCSV(research.trends_dir)
    elif creds.serpapi_key:
        p.trends = SerpAPI(creds.serpapi_key)
    else:
        p.gaps.append("nessun CSV di Google Trends in research.trends_dir: andamento non misurato")

    # competitors, BSR, reviews: pages saved from the browser (free) > paid scrapers
    if research and research.amazon_pages_dir:
        saved = SavedAmazonPages(research.amazon_pages_dir)
        p.marketplace = p.bsr = p.reviews = saved
    if creds.serpapi_key and p.marketplace is None:
        p.marketplace = SerpAPI(creds.serpapi_key)
    if creds.brightdata_token and creds.brightdata_zone:
        bd = BrightData(creds.brightdata_token, creds.brightdata_zone)
        p.unblocker = bd
        p.bsr = p.bsr or bd
        p.reviews = p.reviews or bd
    if creds.apify_token and creds.apify_reviews_actor and p.reviews is None:
        p.reviews = Apify(creds.apify_token, creds.apify_reviews_actor)
    if p.marketplace is None:
        p.gaps.append("nessuna pagina Amazon.it salvata in research.amazon_pages_dir: concorrenti non misurati")

    # demand: autocomplete (free) always; volumes if a paid source or an export exists
    p.volumes.append(Suggestions(folder=cache / "suggest" if cache else None))
    if research and research.helium10_csv:
        p.volumes.append(Helium10Export(research.helium10_csv))
    if creds.dataforseo_login and creds.dataforseo_password:
        p.volumes.append(DataForSEO(creds.dataforseo_login, creds.dataforseo_password))

    if creds.reddit_client_id and creds.reddit_client_secret:
        try:
            p.community = Reddit(creds.reddit_client_id, creds.reddit_client_secret)
        except ProviderError as exc:
            p.gaps.append(f"reddit non disponibile: {exc}")
    else:
        p.gaps.append("nessuna app Reddit (gratuita): niente thread dai forum")
    return p


__all__ = ["Providers", "build_providers", "ProviderError"]
