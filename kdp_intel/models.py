"""The shapes every agent reads and writes.

Models that a language model fills in (``*Draft``, ``*Labels``, ``Outline``)
have no defaults on purpose: structured outputs require every property, and a
field the model may silently skip is a field nobody checks.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------- sources

SourceKind = Literal["law", "official", "press", "community", "review", "other"]


class SourceSpec(BaseModel):
    """One document a project wants in its vector store."""

    url: str
    title: str = ""
    publisher: str = ""
    kind: SourceKind | None = None  # inferred from the domain when omitted
    published: date | None = None


class SourceDoc(BaseModel):
    """A fetched document, before chunking."""

    id: str
    url: str
    title: str
    publisher: str
    kind: SourceKind
    authority: int = Field(ge=1, le=3)  # 1 official, 2 professional press, 3 community
    published: date | None = None
    retrieved: date
    text: str


class Chunk(BaseModel):
    id: str
    source_id: str
    ordinal: int
    text: str
    url: str
    title: str
    publisher: str
    kind: SourceKind
    authority: int
    published: str = ""  # ISO date or ""; vector stores want flat scalars
    retrieved: str = ""

    def metadata(self) -> dict[str, str | int]:
        return self.model_dump(exclude={"id", "text"})


class Hit(BaseModel):
    chunk: Chunk
    score: float  # vector similarity
    lexical: float = 0.0  # BM25 against the whole archive; 0 means no query term occurs


# --------------------------------------------------------------------- market


class TrendPoint(BaseModel):
    date: str
    value: int


class KeywordMetric(BaseModel):
    keyword: str
    search_volume: int | None = None
    competing_products: int | None = None
    suggestions: int | None = None  # long-tail autocomplete completions (free proxy)
    on_amazon: bool | None = None  # Amazon.it autocomplete knows the phrase
    source: str = ""


class Competitor(BaseModel):
    asin: str = ""
    title: str
    rating: float | None = None
    reviews: int | None = None
    price_eur: float | None = None
    bsr: int | None = None
    published: str = ""
    url: str = ""


class Review(BaseModel):
    asin: str = ""
    rating: int
    title: str = ""
    text: str
    date: str = ""


class NicheCandidate(BaseModel):
    name: str
    seed_keywords: list[str]
    trend: list[TrendPoint] = []
    keywords: list[KeywordMetric] = []
    competitors: list[Competitor] = []
    scores: dict[str, float] = {}
    total: float = 0.0
    notes: list[str] = []


class NicheReport(BaseModel):
    candidates: list[NicheCandidate]
    chosen: str = ""


# --------------------------------------------------------------------- review mining


class Theme(BaseModel):
    id: str
    label: str
    description: str


class ReviewLabel(BaseModel):
    review_index: int
    theme_ids: list[str]
    quote: str  # must be a verbatim substring of the review


class ReviewThemesDraft(BaseModel):
    themes: list[Theme]
    labels: list[ReviewLabel]
    missing_content: list[str]
    opportunity_statements: list[str]


class ThemeCount(BaseModel):
    theme: Theme
    count: int
    share: float
    quotes: list[str]


class GapReport(BaseModel):
    reviews_considered: int
    themes: list[ThemeCount]
    missing_content: list[str]
    opportunity_statements: list[str]
    rejected_quotes: int = 0


# --------------------------------------------------------------------- persona & outline


class PersonaDraft(BaseModel):
    name: str
    demographics: str
    competence_level: str
    vocabulary: list[str]
    frustrations: list[str]
    tried_and_failed: list[str]
    desired_outcome: str


class ChapterSpec(BaseModel):
    number: int
    part: str
    title: str
    goal: str
    beats: list[str]
    queries: list[str]
    target_words: int


class Outline(BaseModel):
    title: str
    subtitle: str
    value_proposition: str
    parts: list[str]
    chapters: list[ChapterSpec]


# --------------------------------------------------------------------- book content

BlockType = Literal[
    "heading", "paragraph", "bullets", "numbered", "table", "callout", "checklist"
]
CalloutKind = Literal["attenzione", "caso_pratico", "consiglio", "dato_chiave", "none"]


class Block(BaseModel):
    """One unit of chapter content.

    Inline text may carry ``**bold**``, ``*italic*`` and citation markers
    ``[[S:<source_id>]]``. A flat shape with every field present is easier for
    a model to fill reliably than a union of block types.
    """

    type: BlockType
    text: str  # heading/paragraph text, or the callout body
    title: str  # callout title or table caption; "" when unused
    kind: CalloutKind  # "none" unless type == "callout"
    items: list[str]  # bullets / numbered / checklist
    header: list[str]  # table header
    rows: list[list[str]]  # table body


class ChapterDraft(BaseModel):
    title: str
    blocks: list[Block]


class Chapter(BaseModel):
    spec: ChapterSpec
    draft: ChapterDraft
    revision: int = 0


# --------------------------------------------------------------------- fact checking

ClaimStatus = Literal[
    "supported", "contradicted", "unsupported", "uncited", "number_mismatch", "weak_source"
]


class Claim(BaseModel):
    id: str
    chapter: int
    block_index: int
    text: str
    cited: list[str]
    signals: list[str]  # number, percent, money, law, date
    # In a worked example: the figures stated before this sentence (the hypothetical premises and
    # earlier results), from which this sentence's own figures may be computed.
    given: list[str] = []


class EntailmentDraft(BaseModel):
    claim_id: str
    verdict: Literal["supported", "contradicted", "not_enough_info"]
    source_id: str
    quote: str  # verbatim from the evidence, or "" when not supported
    note: str


class EntailmentBatch(BaseModel):
    results: list[EntailmentDraft]


class ClaimVerdict(BaseModel):
    claim: Claim
    status: ClaimStatus
    source_id: str = ""
    quote: str = ""
    note: str = ""


class FactCheckReport(BaseModel):
    chapter: int
    verdicts: list[ClaimVerdict]
    passed: bool
    counts: dict[str, int]

    def failures(self) -> list[ClaimVerdict]:
        return [v for v in self.verdicts if v.status != "supported"]
