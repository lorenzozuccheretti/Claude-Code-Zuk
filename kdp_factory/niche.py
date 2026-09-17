"""The niche: the engine's one input, and the scoring sheet it is judged by.

Ten weighted signals, each 0–10, each carrying the evidence it came from. The
checklist in the pack is blunt about the bar: "You can name the actual demand
signal — not a hunch." So a signal without evidence scores, but the niche gate
fails it.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from .errors import ConfigError
from .naming import slugify


@dataclass(frozen=True)
class SignalDef:
    key: str
    weight: float
    label: str
    inverse: bool = False  # 10 = worst rather than best (risk-shaped signals)
    help: str = ""


# The scoring sheet, with the weighting built in. Weights sum to 1.0.
SIGNALS: tuple[SignalDef, ...] = (
    SignalDef("demand_volume", 0.18, "Search demand",
              help="Volume of people actually looking. Needs a named source."),
    SignalDef("proven_sales", 0.14, "Proven sales",
              help="Best-seller ranks of the top results: is anyone earning here?"),
    SignalDef("competition_gap", 0.12, "Weakness of incumbents",
              help="What the top books get wrong, in their own reviews."),
    SignalDef("differentiation", 0.12, "Our angle",
              help="The specific thing this book does that they do not."),
    SignalDef("review_moat", 0.10, "Review moat", inverse=True,
              help="How buried a new book gets. 10 = thousands of reviews to outrank."),
    SignalDef("price_headroom", 0.08, "Price headroom",
              help="Room between printing cost and what the niche pays."),
    SignalDef("format_fit", 0.08, "Format fit",
              help="How naturally this book type serves this audience."),
    SignalDef("longevity", 0.07, "Evergreen",
              help="Still sells in two years, or a fad."),
    SignalDef("seasonality", 0.05, "Seasonality risk", inverse=True,
              help="10 = sells only in one short window."),
    SignalDef("trademark_risk", 0.06, "Trademark exposure", inverse=True,
              help="10 = the obvious title is somebody's registered mark."),
)

SIGNAL_KEYS = tuple(s.key for s in SIGNALS)
SIGNALS_BY_KEY = {s.key: s for s in SIGNALS}


@dataclass(frozen=True)
class Signal:
    """One scored signal and the evidence behind it."""

    key: str
    score: float
    evidence: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if self.key not in SIGNALS_BY_KEY:
            raise ConfigError(
                f"unknown signal {self.key!r}; known signals: {', '.join(SIGNAL_KEYS)}"
            )
        if not 0 <= self.score <= 10:
            raise ConfigError(
                f"signal {self.key!r} scored {self.score}; the scale is 0–10"
            )

    @property
    def definition(self) -> SignalDef:
        return SIGNALS_BY_KEY[self.key]

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence.strip())

    @property
    def effective(self) -> float:
        """The value that goes into the weighted total (inverse signals flip)."""
        return 10.0 - self.score if self.definition.inverse else self.score

    @property
    def contribution(self) -> float:
        return self.definition.weight * self.effective


@dataclass(frozen=True)
class Competitor:
    """The incumbent this book is aimed at."""

    title: str = ""
    asin: str = ""
    price: float | None = None
    reviews: int | None = None
    rating: float | None = None
    page_count: int | None = None
    gaps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "asin": self.asin,
            "price": self.price,
            "reviews": self.reviews,
            "rating": self.rating,
            "page_count": self.page_count,
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True)
class TrademarkCheck:
    """Was the brand exposure actually checked, and by whom."""

    checked: bool = False
    source: str = ""
    checked_on: str = ""
    hits: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "source": self.source,
            "checked_on": self.checked_on,
            "hits": list(self.hits),
        }


@dataclass(frozen=True)
class Niche:
    """One niche, ready to be poured through the six stations."""

    niche: str
    book_type: str
    audience: str = ""
    # The audience field is a research note ("first-time mothers in the first
    # year, usually buying for themselves at 11pm"). A subtitle needs the first
    # few words of it, not the whole observation.
    audience_short: str = ""
    # The book's name. Left empty, the book type proposes one from the niche's
    # own words; set here, it is used verbatim. A press that has settled on a
    # name should not have to re-roll a seed to keep it.
    title: str = ""
    subtitle: str = ""
    promise: str = ""
    signals: dict[str, Signal] = field(default_factory=dict)
    keywords_seed: list[str] = field(default_factory=list)
    competitor: Competitor = field(default_factory=Competitor)
    trademark: TrademarkCheck = field(default_factory=TrademarkCheck)
    constraints: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    source_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.niche.strip():
            raise ConfigError("niche must have a non-empty 'niche' field")
        if not self.book_type.strip():
            raise ConfigError(f"niche {self.niche!r} does not name a book_type")

    @property
    def slug(self) -> str:
        return slugify(self.niche)

    @property
    def audience_phrase(self) -> str:
        """A short audience phrase fit to print on a cover.

        Uses ``audience_short`` when the niche file sets one; otherwise takes
        the first clause of the audience note, capped at seven words.
        """
        if self.audience_short.strip():
            return self.audience_short.strip()
        text = self.audience.split(",")[0].strip()
        for marker in (" who ", " usually ", " often ", " typically ", " buying "):
            if marker in f" {text} ":
                text = text.split(marker.strip())[0].strip()
                break
        words = text.split()
        return " ".join(words[:7]) if words else ""

    def signal(self, key: str) -> Signal | None:
        return self.signals.get(key)

    def score(self) -> "NicheScore":
        return score_niche(self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "niche": self.niche,
            "slug": self.slug,
            "book_type": self.book_type,
            "audience": self.audience,
            "audience_short": self.audience_phrase,
            "title": self.title,
            "subtitle": self.subtitle,
            "promise": self.promise,
            "keywords_seed": list(self.keywords_seed),
            "signals": {
                key: {
                    "score": s.score,
                    "evidence": s.evidence,
                    "source": s.source,
                }
                for key, s in self.signals.items()
            },
            "competitor": self.competitor.as_dict(),
            "trademark": self.trademark.as_dict(),
            "constraints": dict(self.constraints),
            "options": dict(self.options),
            "notes": self.notes,
            "source_path": str(self.source_path) if self.source_path else None,
        }


@dataclass(frozen=True)
class NicheScore:
    """The weighted verdict, with every missing piece named."""

    total: float
    contributions: dict[str, float]
    missing_signals: list[str]
    unevidenced: list[str]

    @property
    def out_of(self) -> float:
        return 10.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 3),
            "out_of": self.out_of,
            "contributions": {k: round(v, 4) for k, v in self.contributions.items()},
            "missing_signals": list(self.missing_signals),
            "unevidenced_signals": list(self.unevidenced),
            "weights": {s.key: s.weight for s in SIGNALS},
        }

    def table(self) -> str:
        rows = [f"{'signal':<18}{'w':>6}{'score':>7}{'eff':>6}{'contrib':>9}  evidence"]
        for definition in SIGNALS:
            contribution = self.contributions.get(definition.key)
            if contribution is None:
                rows.append(f"{definition.key:<18}{definition.weight:>6}{'—':>7}{'—':>6}{'0.0000':>9}  MISSING")
                continue
            effective = contribution / definition.weight
            score = 10 - effective if definition.inverse else effective
            flag = "no evidence" if definition.key in self.unevidenced else "ok"
            rows.append(
                f"{definition.key:<18}{definition.weight:>6}{score:>7.1f}"
                f"{effective:>6.1f}{contribution:>9.4f}  {flag}"
            )
        rows.append(f"{'TOTAL':<18}{'':>6}{'':>7}{'':>6}{self.total:>9.2f}  / 10")
        return "\n".join(rows)


def score_niche(niche: Niche) -> NicheScore:
    """Weighted sum of the ten signals. A missing signal scores zero."""
    contributions: dict[str, float] = {}
    missing: list[str] = []
    unevidenced: list[str] = []
    for definition in SIGNALS:
        signal = niche.signals.get(definition.key)
        if signal is None:
            missing.append(definition.key)
            continue
        contributions[definition.key] = signal.contribution
        if not signal.has_evidence:
            unevidenced.append(definition.key)
    return NicheScore(
        total=sum(contributions.values()),
        contributions=contributions,
        missing_signals=missing,
        unevidenced=unevidenced,
    )


# ------------------------------------------------------------------ loading
def _parse_signals(raw: Any, niche_name: str) -> dict[str, Signal]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{niche_name}: 'signals' must be a mapping")
    signals: dict[str, Signal] = {}
    for key, value in raw.items():
        if isinstance(value, (int, float)):
            signals[key] = Signal(key=key, score=float(value))
        elif isinstance(value, dict):
            if "score" not in value:
                raise ConfigError(f"{niche_name}: signal {key!r} has no 'score'")
            signals[key] = Signal(
                key=key,
                score=float(value["score"]),
                evidence=str(value.get("evidence", "") or ""),
                source=str(value.get("source", "") or ""),
            )
        else:
            raise ConfigError(
                f"{niche_name}: signal {key!r} must be a number or a mapping"
            )
    return signals


def niche_from_dict(data: dict[str, Any], source_path: Path | None = None) -> Niche:
    if not isinstance(data, dict):
        raise ConfigError("a niche file must contain a YAML mapping")
    name = str(data.get("niche", "")).strip()
    competitor_raw = data.get("competitor") or {}
    trademark_raw = data.get("trademark") or {}
    return Niche(
        niche=name,
        book_type=str(data.get("book_type", "")).strip(),
        audience=str(data.get("audience", "") or ""),
        audience_short=str(data.get("audience_short", "") or ""),
        title=str(data.get("title", "") or "").strip(),
        subtitle=str(data.get("subtitle", "") or "").strip(),
        promise=str(data.get("promise", "") or ""),
        signals=_parse_signals(data.get("signals"), name or "<unnamed>"),
        keywords_seed=[str(k) for k in (data.get("keywords_seed") or [])],
        competitor=Competitor(
            title=str(competitor_raw.get("title", "") or ""),
            asin=str(competitor_raw.get("asin", "") or ""),
            price=competitor_raw.get("price"),
            reviews=competitor_raw.get("reviews"),
            rating=competitor_raw.get("rating"),
            page_count=competitor_raw.get("page_count"),
            gaps=[str(g) for g in (competitor_raw.get("gaps") or [])],
        ),
        trademark=TrademarkCheck(
            checked=bool(trademark_raw.get("checked", False)),
            source=str(trademark_raw.get("source", "") or ""),
            checked_on=str(trademark_raw.get("checked_on", "") or ""),
            hits=[str(h) for h in (trademark_raw.get("hits") or [])],
        ),
        constraints=dict(data.get("constraints") or {}),
        options=dict(data.get("options") or {}),
        notes=str(data.get("notes", "") or ""),
        source_path=source_path,
    )


def load_niche(path: str | Path) -> Niche:
    """Load one niche from YAML (or JSON — YAML is a superset)."""
    niche_path = Path(path)
    if not niche_path.is_file():
        raise ConfigError(f"niche file not found: {niche_path}")
    with niche_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return niche_from_dict(data or {}, source_path=niche_path)


def load_niche_csv(path: str | Path) -> list[Niche]:
    """Load many niches from a scout CSV.

    Columns: ``niche``, ``book_type``, plus ``signal.<key>`` and optional
    ``evidence.<key>`` pairs. Everything else is carried into ``options``.
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise ConfigError(f"niche CSV not found: {csv_path}")
    niches: list[Niche] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "niche" not in reader.fieldnames:
            raise ConfigError(f"{csv_path} needs at least a 'niche' column")
        for row in reader:
            signals: dict[str, Any] = {}
            options: dict[str, Any] = {}
            for column, value in row.items():
                if column is None or value is None or value == "":
                    continue
                if column.startswith("signal."):
                    signals.setdefault(column[7:], {})["score"] = float(value)
                elif column.startswith("evidence."):
                    signals.setdefault(column[9:], {})["evidence"] = value
                elif column not in {"niche", "book_type", "audience", "promise", "notes"}:
                    options[column] = value
            niches.append(
                niche_from_dict(
                    {
                        "niche": row.get("niche", ""),
                        "book_type": row.get("book_type", "") or "journal",
                        "audience": row.get("audience", ""),
                        "promise": row.get("promise", ""),
                        "notes": row.get("notes", ""),
                        "signals": signals,
                        "options": options,
                    },
                    source_path=csv_path,
                )
            )
    return niches


def rank(niches: Iterable[Niche]) -> list[tuple[Niche, NicheScore]]:
    scored = [(n, n.score()) for n in niches]
    scored.sort(key=lambda pair: pair[1].total, reverse=True)
    return scored


def weights_sum() -> float:
    return round(sum(s.weight for s in SIGNALS), 6)
