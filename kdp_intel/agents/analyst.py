"""Analyst Agent: is there a market, and how weak is the competition?

The analyst only gathers and measures; it never asks a model for a number.
Scores are computed from what the providers returned, and a score whose
inputs are missing is left out (its weight redistributed) and named in the
candidate's notes, so a niche cannot look good merely because nothing was
measured.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from ..models import Competitor, KeywordMetric, NicheCandidate, NicheReport, SourceSpec
from ..providers import Providers

WEIGHTS = {
    "demand": 0.30,  # Amazon search volume (or a Trends proxy)
    "momentum": 0.15,  # last quarter vs first quarter of the Trends year
    "weak_competition": 0.25,  # low ratings and few reviews on the top results
    "proven_sales": 0.15,  # median BSR of the top results
    "freshness": 0.15,  # recent tier 1-2 sources: the topic is moving
}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_demand(keywords: list[KeywordMetric], trend_values: list[int]) -> tuple[float | None, str]:
    volumes = [k.search_volume for k in keywords if k.search_volume is not None]
    if volumes:
        total = sum(volumes)
        return round(_clamp(math.log10(total + 1) / 4) * 10, 2), f"{total} ricerche/mese su {len(volumes)} keyword"
    suggested = [k for k in keywords if k.suggestions is not None]
    if suggested:  # free proxy: how much long tail people actually type
        tail = sum(k.suggestions or 0 for k in suggested)
        on_amazon = sum(1 for k in suggested if k.on_amazon)
        score = _clamp(tail / 30) * 10 * (1.0 if on_amazon else 0.6)
        note = f"proxy autocompletamento: {tail} varianti, {on_amazon}/{len(suggested)} keyword note ad Amazon.it"
        if trend_values:
            score = (score + statistics.mean(trend_values) / 10) / 2
            note += " + media Google Trends"
        return round(score, 2), note
    if trend_values:
        return round(statistics.mean(trend_values) / 10, 2), "proxy: media Google Trends (nessun volume Amazon)"
    return None, "domanda non misurata"


def score_momentum(trend_values: list[int]) -> tuple[float | None, str]:
    if len(trend_values) < 12:
        return None, "Trends insufficiente"
    q = max(3, len(trend_values) // 4)
    first, last = statistics.mean(trend_values[:q]) or 1.0, statistics.mean(trend_values[-q:])
    ratio = last / first
    return round(5 + 5 * _clamp((ratio - 1) / 0.5, -1, 1), 2), f"ultimo trimestre / primo = {ratio:.2f}"


def score_competition(comps: list[Competitor]) -> tuple[float | None, str]:
    rated = [c for c in comps[:10] if c.rating is not None]
    if len(rated) < 3:
        return None, "concorrenti non misurati"
    avg_rating = statistics.mean(c.rating for c in rated)  # type: ignore[misc]
    median_reviews = statistics.median(c.reviews or 0 for c in rated)
    quality_gap = _clamp((4.6 - avg_rating) / 1.0) * 5
    thin = _clamp(1 - math.log10(median_reviews + 1) / 3) * 5
    return round(quality_gap + thin, 2), f"voto medio {avg_rating:.2f}, recensioni mediane {median_reviews:.0f}"


def score_sales(comps: list[Competitor]) -> tuple[float | None, str]:
    ranks = [c.bsr for c in comps[:10] if c.bsr]
    if len(ranks) < 3:
        return None, "BSR non misurato"
    median = statistics.median(ranks)
    return round(_clamp((6 - math.log10(median)) / 3) * 10, 2), f"BSR mediano {median:,.0f}".replace(",", ".")


REQUIRED = ("demand",)  # without a demand measure no total is meaningful
MIN_LEXICAL = 3.0  # BM25 score at which a source is actually about the seed


def combine(scores: dict[str, float | None]) -> float:
    present = {k: v for k, v in scores.items() if v is not None}
    if not present or any(scores.get(k) is None for k in REQUIRED):
        return 0.0
    weight = sum(WEIGHTS[k] for k in present)
    return round(sum(WEIGHTS[k] * v for k, v in present.items()) / weight, 2)


class Analyst:
    def __init__(self, providers: Providers, store=None, ingestor=None, today: date | None = None) -> None:
        self.p = providers
        self.store, self.ingestor = store, ingestor
        self.today = today or date.today()
        self.log: list[str] = list(providers.gaps)

    def discover_sources(self, seeds: list[str], domains: list[str], per_query: int = 3) -> list[SourceSpec]:
        """Fresh official and press pages for the niche, found by web search."""
        if self.p.web_search is None:
            return []
        specs: dict[str, SourceSpec] = {}
        for seed in seeds:
            for dom in domains:
                try:
                    results = self.p.web_search.search(f"{seed} site:{dom}", num=per_query)
                except Exception as exc:  # noqa: BLE001 - a blocked search is a gap, not a crash
                    self.log.append(f"ricerca web non disponibile: {exc}")
                    return list(specs.values())
                for r in results:
                    specs.setdefault(r.url, SourceSpec(url=r.url, title=r.title))
        if self.ingestor is not None:
            self.ingestor.ingest_all(list(specs.values()))
        return list(specs.values())

    def _competitors(self, seeds: list[str]) -> list[Competitor]:
        if self.p.marketplace is None:
            return []
        seen: dict[str, Competitor] = {}
        for seed in seeds[:2]:
            for c in self.p.marketplace.search_books(seed):
                seen.setdefault(c.asin or c.title, c)
        comps = list(seen.values())
        if self.p.bsr is not None:
            for c in comps[:10]:
                if c.asin and c.bsr is None:
                    try:
                        c.bsr = self.p.bsr.bsr(c.asin)
                    except Exception as exc:  # noqa: BLE001
                        self.log.append(f"BSR {c.asin}: {exc}")
        return comps

    def _freshness(self, seeds: list[str]) -> tuple[float | None, str]:
        if self.store is None or self.store.count() == 0:
            return None, "nessuna fonte indicizzata"
        since = self.today - timedelta(days=365)
        fresh = set()
        for seed in seeds:
            for h in self.store.query(seed, 20, max_authority=2, min_published=since):
                if (h.chunk.published and date.fromisoformat(h.chunk.published) >= since
                        and h.lexical >= MIN_LEXICAL):
                    fresh.add(h.chunk.source_id)
        return float(min(10, 2 * len(fresh))), f"{len(fresh)} fonti ufficiali/stampa degli ultimi 12 mesi"

    def assess(self, name: str, seeds: list[str]) -> NicheCandidate:
        trend = self.p.trends.interest_over_time(seeds[0]) if self.p.trends else []
        values = [t.value for t in trend]
        keywords: dict[str, KeywordMetric] = {}
        for source in self.p.volumes:
            try:
                for k in source.volumes(seeds):
                    known = keywords.setdefault(k.keyword.lower(), k)
                    for fname, value in k.model_dump().items():  # merge what each source measured
                        if getattr(known, fname) is None and value is not None:
                            setattr(known, fname, value)
            except Exception as exc:  # noqa: BLE001
                self.log.append(f"volumi {type(source).__name__}: {exc}")
        comps = self._competitors(seeds)

        results = {
            "demand": score_demand(list(keywords.values()), values),
            "momentum": score_momentum(values),
            "weak_competition": score_competition(comps),
            "proven_sales": score_sales(comps),
            "freshness": self._freshness(seeds),
        }
        scores = {k: v for k, (v, _) in results.items()}
        notes = [f"{k}: {note}" for k, (_, note) in results.items()]
        missing = [k for k, v in scores.items() if v is None]
        if any(k in missing for k in REQUIRED):
            notes.append("punteggio non calcolabile: manca la misura della domanda")
        elif missing:
            notes.append(f"pesi ridistribuiti, mancano: {', '.join(missing)}")
        return NicheCandidate(
            name=name, seed_keywords=seeds, trend=trend, keywords=list(keywords.values()),
            competitors=comps, scores={k: v for k, v in scores.items() if v is not None},
            total=combine(scores), notes=notes,
        )

    def research(self, niches: dict[str, list[str]]) -> NicheReport:
        candidates = sorted((self.assess(n, s) for n, s in niches.items()), key=lambda c: -c.total)
        return NicheReport(candidates=candidates, chosen=candidates[0].name if candidates else "")
