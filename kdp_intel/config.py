"""Project files and credentials.

A *project* is one book: what to research, which documents to trust, how to
print it. Credentials never live in the project file; they come from the
environment, one variable per service, and a missing one switches that
provider off rather than failing the run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .models import Outline, SourceSpec


class BookMeta(BaseModel):
    title: str
    subtitle: str = ""
    author: str
    publisher: str = ""
    edition: str = "Prima edizione"
    year: int = Field(default_factory=lambda: date.today().year)
    isbn: str = ""  # empty: KDP assigns a free ISBN
    language: str = "it"
    trim: str = "6x9"
    paper: str = "bw_white"
    engine: str = "typst"  # typst | weasyprint
    disclaimer: str = (
        "Le informazioni contenute in questo libro hanno scopo divulgativo e non "
        "sostituiscono la consulenza di un professionista abilitato. Norme, importi "
        "e scadenze possono cambiare dopo la data di aggiornamento indicata."
    )


class ResearchPlan(BaseModel):
    niches: dict[str, list[str]] = {}  # niche name -> seed keywords
    competitor_asins: list[str] = []
    review_csv: str = ""  # optional local export of reviews
    helium10_csv: str = ""  # optional Cerebro/Magnet export
    trends_dir: str = ""  # Google Trends CSV exports (free)
    amazon_pages_dir: str = ""  # Amazon.it pages saved from the browser (free)
    subreddits: list[str] = ["italy", "ItaliaPersonalFinance", "commercialisti"]
    reddit_queries: list[str] = []
    verify_domains: list[str] = [
        "gazzettaufficiale.it",
        "normattiva.it",
        "agenziaentrate.gov.it",
        "inps.it",
        "istat.it",
    ]


class QualityBar(BaseModel):
    max_unsupported_ratio: float = 0.0  # share of checkable claims allowed unproven
    max_revisions: int = 2
    min_authority_for_numbers: int = 2  # numbers need a tier 1 or 2 source
    max_source_age_days: int = 540  # press older than this is not evidence
    banned_phrases: list[str] = [
        "in un mondo in continua evoluzione",
        "è fondamentale ricordare",
        "è importante sottolineare",
        "in conclusione,",
        "nel panorama attuale",
        "al giorno d'oggi",
        "senza ombra di dubbio",
        "a 360 gradi",
    ]


class Project(BaseModel):
    slug: str
    book: BookMeta
    facts_as_of: date = Field(default_factory=date.today)
    research: ResearchPlan = ResearchPlan()
    sources: list[SourceSpec] = []
    index_terms: list[str] = []
    outline: Outline | None = None
    quality: QualityBar = QualityBar()
    persist_dir: str = ".kdp_intel"

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        outline_file = data.pop("outline_file", None)
        project = cls.model_validate(data)
        if outline_file and project.outline is None:
            outline_path = (path.parent / outline_file).resolve()
            project.outline = Outline.model_validate(
                yaml.safe_load(outline_path.read_text(encoding="utf-8"))
            )
        return project

    def workdir(self, root: str | Path = ".") -> Path:
        out = Path(root) / self.persist_dir / self.slug
        out.mkdir(parents=True, exist_ok=True)
        return out


@dataclass(frozen=True)
class Credentials:
    """Which services this run may call. Read once from the environment.
    The first block is free (no card); the second is paid and optional."""

    gemini_key: str = ""
    tavily_key: str = ""
    openrouter_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""

    serpapi_key: str = ""
    apify_token: str = ""
    apify_reviews_actor: str = ""
    brightdata_token: str = ""
    brightdata_zone: str = ""
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    pinecone_key: str = ""
    pinecone_index: str = ""

    @classmethod
    def from_env(cls) -> "Credentials":
        env = os.environ.get
        return cls(
            gemini_key=env("GEMINI_API_KEY", ""),
            tavily_key=env("TAVILY_API_KEY", ""),
            openrouter_key=env("OPENROUTER_API_KEY", ""),
            serpapi_key=env("SERPAPI_API_KEY", ""),
            apify_token=env("APIFY_TOKEN", ""),
            apify_reviews_actor=env("APIFY_REVIEWS_ACTOR", ""),
            brightdata_token=env("BRIGHTDATA_API_TOKEN", ""),
            brightdata_zone=env("BRIGHTDATA_ZONE", ""),
            dataforseo_login=env("DATAFORSEO_LOGIN", ""),
            dataforseo_password=env("DATAFORSEO_PASSWORD", ""),
            pinecone_key=env("PINECONE_API_KEY", ""),
            pinecone_index=env("PINECONE_INDEX", ""),
            reddit_client_id=env("REDDIT_CLIENT_ID", ""),
            reddit_client_secret=env("REDDIT_CLIENT_SECRET", ""),
        )

    def available(self) -> dict[str, bool]:
        return {
            "gemini (gratis)": bool(self.gemini_key),
            "tavily (gratis)": bool(self.tavily_key),
            "openrouter (gratis)": bool(self.openrouter_key),
            "reddit_oauth (gratis)": bool(self.reddit_client_id and self.reddit_client_secret),
            "serpapi": bool(self.serpapi_key),
            "apify": bool(self.apify_token and self.apify_reviews_actor),
            "brightdata": bool(self.brightdata_token and self.brightdata_zone),
            "dataforseo": bool(self.dataforseo_login and self.dataforseo_password),
            "pinecone": bool(self.pinecone_key and self.pinecone_index),
        }
