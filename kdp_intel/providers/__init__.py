"""Choose a concrete provider for each capability from the credentials at hand.

Preference order is fixed and visible here: a paid API beats a scraper, a
scraper beats nothing, and nothing is reported as a gap in the run log
instead of being filled with invented data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


def build_providers(creds: Credentials, project: Project | None = None) -> Providers:
    from .apify import Apify
    from .brightdata import BrightData
    from .dataforseo import DataForSEO
    from .helium10 import Helium10Export
    from .reddit import Reddit
    from .serpapi import SerpAPI

    p = Providers(fetcher=DirectFetcher())
    if creds.serpapi_key:
        serp = SerpAPI(creds.serpapi_key)
        p.web_search = p.trends = p.marketplace = serp
    else:
        p.gaps.append("no SERPAPI_API_KEY: Google, Trends and Amazon.it search are off")

    if creds.brightdata_token and creds.brightdata_zone:
        bd = BrightData(creds.brightdata_token, creds.brightdata_zone)
        p.unblocker = bd
        p.bsr = bd
        p.reviews = bd
    if creds.apify_token and creds.apify_reviews_actor:
        p.reviews = Apify(creds.apify_token, creds.apify_reviews_actor)  # preferred over HTML
    if p.reviews is None:
        p.gaps.append("no Apify actor or Bright Data zone: competitor reviews must come from a CSV")
    if p.bsr is None:
        p.gaps.append("no Bright Data zone: BSR is not measured")

    if creds.dataforseo_login and creds.dataforseo_password:
        p.volumes.append(DataForSEO(creds.dataforseo_login, creds.dataforseo_password))
    if project and project.research.helium10_csv:
        p.volumes.append(Helium10Export(project.research.helium10_csv))
    if not p.volumes:
        p.gaps.append("no DataForSEO login or Helium 10 export: Amazon search volume unknown")

    try:
        p.community = Reddit(creds.reddit_client_id, creds.reddit_client_secret)
    except ProviderError as exc:
        p.gaps.append(f"reddit unavailable: {exc}")
    return p


__all__ = ["Providers", "build_providers", "ProviderError"]
