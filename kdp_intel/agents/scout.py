"""Scout Agent: from broad seeds to one validated book topic, unattended.

1. **harvest**  - what Italians type into Amazon.it's *book* search box
   (autocomplete of the "Libri" department) and into Google, for each seed.
2. **propose**  - a model groups those phrases into candidate book topics. It
   may only use phrases it was given: code drops every other keyword.
3. **validate** - gates measured in code, never asked of a model, cheapest
   first so the expensive one runs on few topics:
   * *keyword*: a phrase Amazon.it suggests in the book search, with a long
     tail of completions people type after it;
   * *competition*: the Italian catalogue is not already crowded with recent
     titles for that phrase;
   * *sources*: enough official (tier 1) pages about the topic, found by web
     search and actually fetched, and at least one recent tier 1-2 page: the
     topic is current and the book can be fact-checked.
4. **choose**   - the best score among the topics that pass every gate. When
   none passes, the scout says so and stops: it never lowers its own bar.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel

from ..llm import LLM
from ..models import (
    CatalogBook, Gate, KeywordCheck, NicheIdea, NicheIdeas, SourceSpec, TopicAssessment,
)
from ..rag.ingest import source_id
from ..rag.lexical import tokens

ALPHABET = "abcdefghilmnopqrstuvz"  # the Italian alphabet: what people type after the seed

PROPOSE_SYSTEM = """Sei un editor che sceglie i temi per manuali pratici italiani di fascia \
premium (19,90-29,90 €) da pubblicare su Amazon KDP. Ricevi le frasi che le persone digitano \
davvero su Amazon.it (reparto Libri e tutti i reparti) e su Google. Molte frasi di Amazon \
riguardano prodotti che non sono libri (gadget, oggetti): ignorale. Raggruppa le altre in temi di libro.
Un buon tema: un problema concreto, che costa soldi o tempo se affrontato male, regolato da \
norme o procedure italiane verificabili su fonti ufficiali, possibilmente cambiato di recente.
Scarta narrativa, libri scolastici, test e concorsi, temi medici di diagnosi o cura.
Nel campo keywords copia SOLO frasi presenti nell'elenco ricevuto, identiche, dalla più \
specifica alla più generale; almeno due per tema."""


class Gates(BaseModel):
    min_longtail: int = 3  # completions typed after the keyword
    max_recent_titles: int = 12  # catalogue books from the last two years for the keyword
    recent_years: int = 2
    min_official_sources: int = 3  # tier 1 pages about the topic, fetched
    min_fresh_sources: int = 1  # tier 1-2 pages published within fresh_days
    fresh_days: int = 540


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


class Scout:
    def __init__(self, llm: LLM, suggest: Any, catalog: Any = None, analyst: Any = None,
                 store: Any = None, gates: Gates | None = None, today: date | None = None,
                 market: Any = None) -> None:
        self.llm, self.suggest, self.catalog = llm, suggest, catalog
        self.market = market  # Amazon.it search exports (AmazonExports), preferred to the catalogue
        self.analyst, self.store = analyst, store
        self.gates = gates or Gates()
        self.today = today or date.today()
        self.log: list[str] = []
        self._amazon: dict[str, list[str]] = {}

    # ------------------------------------------------------------ harvest

    def amazon(self, prefix: str) -> list[str]:
        key = _norm(prefix) + (" " if prefix.endswith(" ") else "")
        if key not in self._amazon:
            try:
                self._amazon[key] = [_norm(s) for s in self.suggest.amazon(prefix)]
            except Exception as exc:  # noqa: BLE001 - a refused prefix is a gap, not a crash
                self.log.append(f"autocompletamento Amazon «{prefix}»: {exc}")
                self._amazon[key] = []
        return self._amazon[key]

    def google(self, prefix: str) -> list[str]:
        try:
            return [_norm(s) for s in self.suggest.google(prefix)]
        except Exception as exc:  # noqa: BLE001
            self.log.append(f"autocompletamento Google «{prefix}»: {exc}")
            return []

    def books(self, prefix: str) -> list[str]:
        try:
            return [_norm(s) for s in self.suggest.amazon_books(prefix)]
        except Exception as exc:  # noqa: BLE001
            self.log.append(f"autocompletamento Amazon Libri «{prefix}»: {exc}")
            return []

    def harvest(self, seeds: list[str]) -> dict[str, dict[str, list[str]]]:
        """Per seed: the Books department suggestions (few, but typed by book buyers), all of
        Amazon.it (seed, seed + each letter, seed + "libro"/"guida"/"manuale") and Google."""
        out = {}
        for seed in seeds:
            books = dict.fromkeys(b for p in (seed, f"{seed} ") for b in self.books(p))
            amazon: dict[str, None] = {}
            for prefix in [seed, f"{seed} ", *(f"{seed} {c}" for c in ALPHABET),
                           f"{seed} libro", f"{seed} guida", f"{seed} manuale"]:
                amazon.update(dict.fromkeys(a for a in self.amazon(prefix) if a not in books))
            google = dict.fromkeys(self.google(f"{seed} ") + self.google(seed))
            out[seed] = {"amazon_books": list(books), "amazon": list(amazon),
                         "google": [g for g in google if g not in amazon and g not in books]}
        return out

    # ------------------------------------------------------------ propose

    def propose(self, harvest: dict[str, dict[str, list[str]]], exclude: list[str],
                max_ideas: int = 6) -> list[NicheIdea]:
        listing = "\n".join(
            f"## {seed}\nAmazon.it, reparto Libri: {'; '.join(h.get('amazon_books', [])) or '-'}\n"
            f"Amazon.it, tutti i reparti: {'; '.join(h['amazon']) or '-'}\nGoogle: {'; '.join(h['google']) or '-'}"
            for seed, h in harvest.items())
        avoid = f"\n\nTemi già pubblicati, da non riproporre: {'; '.join(exclude)}" if exclude else ""
        ideas = self.llm.structured(
            system=PROPOSE_SYSTEM, schema=NicheIdeas, effort="medium",
            prompt=f"Proponi fino a {max_ideas} temi.{avoid}\n\nFrasi digitate dagli utenti:\n{listing}",
        ).ideas
        typed_amazon = {p for h in harvest.values() for p in h["amazon"] + h.get("amazon_books", [])}
        typed = typed_amazon | {p for h in harvest.values() for p in h["google"]}
        kept = []
        for idea in ideas:
            if any(_norm(x) in _norm(idea.name) or _norm(idea.name) in _norm(x) for x in exclude):
                self.log.append(f"scartato «{idea.name}»: già pubblicato")
                continue
            keywords = list(dict.fromkeys(k for k in map(_norm, idea.keywords) if k in typed))
            dropped = len(idea.keywords) - len(keywords)
            if dropped:
                self.log.append(f"«{idea.name}»: {dropped} keyword non digitate da nessuno, scartate")
            if len(keywords) < 2 or not set(keywords) & typed_amazon:
                self.log.append(f"scartato «{idea.name}»: meno di due keyword reali o nessuna da Amazon")
                continue
            kept.append(idea.model_copy(update={"keywords": keywords}))
        return kept[:max_ideas]

    # ------------------------------------------------------------ keyword gate

    def check_keyword(self, keyword: str) -> KeywordCheck:
        kw = _norm(keyword)
        words = kw.split()
        prefixes = [" ".join(words[:i]) for i in range(1, len(words) + 1)]
        prefixes = [q for w in prefixes for q in (w, f"{w} ")]
        found = next((p for p in prefixes if kw in self.amazon(p) or kw in self.books(p)), "")
        tail = {s for p in (kw, f"{kw} ") for s in self.amazon(p) if s.startswith(kw) and s != kw}
        tail |= {s for s in self.google(f"{kw} ") if s.startswith(kw) and s != kw}
        check = KeywordCheck(keyword=kw, amazon_prefix=found, longtail=len(tail))
        if not found:
            check.notes.append("Amazon.it non la suggerisce")
        if check.longtail < self.gates.min_longtail:
            check.notes.append(f"coda lunga {check.longtail} < {self.gates.min_longtail}")
        check.passed = bool(found) and check.longtail >= self.gates.min_longtail
        return check

    def competition(self, keyword: str) -> tuple[int | None, list[CatalogBook]]:
        if self.market is not None and self.market.has(keyword):
            return self.market.search_catalog(keyword)  # what Amazon.it itself sells
        if self.catalog is None:
            return None, []
        try:
            return self.catalog.search_catalog(keyword)
        except Exception as exc:  # noqa: BLE001
            self.log.append(f"catalogo «{keyword}»: {exc}")
            return None, []

    # ------------------------------------------------------------ sources gate

    def sources(self, idea: NicheIdea, keyword: str, domains: list[str]) -> list[SourceSpec]:
        """Official pages for the topic: one search per authority domain plus one open search
        for recent news, all fetched into the store by the analyst."""
        if self.analyst is None:
            return []
        from ..llm_free import PendingLLM  # noqa: PLC0415

        found, waiting = [], []
        for seeds, doms, n in (([keyword], domains, 3), ([f"{keyword} novità {self.today.year}"], [""], 5)):
            try:
                found += self.analyst.discover_sources(seeds, doms, per_query=n)
            except PendingLLM as pending:  # one round of questions for the whole topic
                waiting.append(pending)
        if waiting:
            raise PendingLLM([t for w in waiting for t in w.task_ids], waiting[0].folder)
        return list({s.url: s for s in found}.values())

    def measure_sources(self, keyword: str, specs: list[SourceSpec]) -> tuple[list[str], list[str]]:
        if self.store is None or not specs:
            return [], []
        ids = [source_id(s.url) for s in specs]
        official, fresh = set(), set()
        since = self.today - timedelta(days=self.gates.fresh_days)
        terms = set(tokens(keyword))  # a page is about the topic when it uses every word of it
        for h in self.store.query(keyword, 60, source_ids=ids):
            if not terms <= set(tokens(f"{h.chunk.title} {h.chunk.text}")):
                continue
            c = h.chunk
            if c.authority == 1:
                official.add(c.source_id)
            if c.authority <= 2 and c.published and date.fromisoformat(c.published) >= since:
                fresh.add(c.source_id)
        return sorted(official), sorted(fresh)

    # ------------------------------------------------------------ assess and choose

    def assess(self, idea: NicheIdea, domains: list[str], fetch_sources: bool = True) -> TopicAssessment:
        g = self.gates
        checks = [self.check_keyword(k) for k in idea.keywords[:6]]
        passing = sorted((c for c in checks if c.passed),
                         key=lambda c: (-c.longtail, len(c.amazon_prefix), -len(c.keyword)))
        a = TopicAssessment(idea=idea, keywords=checks)
        a.gates.append(Gate(name="keyword", passed=bool(passing), reasons=(
            [f"«{passing[0].keyword}»: suggerita digitando «{passing[0].amazon_prefix.strip()}», "
             f"{passing[0].longtail} varianti"] if passing else
            [f"«{c.keyword}»: {', '.join(c.notes)}" for c in checks])))
        if not passing:
            return a
        primary = passing[0]
        a.primary_keyword = primary.keyword

        total, books = self.competition(primary.keyword)
        recent = [b for b in books if b.year and b.year > self.today.year - g.recent_years]
        primary.catalog_total, primary.recent_titles = total, (len(recent) if total is not None else None)
        a.competitors = books[:10]
        if total is None:
            a.gates.append(Gate(name="competition", passed=True, reasons=["catalogo non raggiungibile: non misurata"]))
        else:
            newest = max((b.year for b in books if b.year), default=None)
            reasons = [f"{total} titoli in catalogo, {len(recent)} degli ultimi {g.recent_years} anni "
                       f"(massimo {g.max_recent_titles}); il più recente è del {newest or 'n.d.'}"]
            if self.market is not None and (m := self.market.summary(primary.keyword)) is not None:
                a.market = m
                reasons = [f"Amazon.it ({m.source}): " + reasons[0],
                           f"vendite stimate {m.est_sales_month:g} copie/mese in tutto il tema, "
                           f"prezzo mediano {m.median_price_eur} €, {m.median_pages} pagine, "
                           f"miglior BSR {m.best_bsr}, {m.total_reviews} recensioni in tutto, "
                           f"{m.self_published} autopubblicati"]
            a.gates.append(Gate(name="competition", passed=len(recent) <= g.max_recent_titles, reasons=reasons))
        if not a.gates[-1].passed or not fetch_sources:
            a.score = self._score(a)
            return a

        specs = self.sources(idea, primary.keyword, domains)
        a.source_urls = [s.url for s in specs]
        a.official_sources, a.fresh_sources = self.measure_sources(primary.keyword, specs)
        a.gates.append(Gate(
            name="sources",
            passed=(len(a.official_sources) >= g.min_official_sources
                    and len(a.fresh_sources) >= g.min_fresh_sources),
            reasons=[f"{len(a.official_sources)} fonti ufficiali sul tema (minimo {g.min_official_sources}), "
                     f"{len(a.fresh_sources)} fonti recenti (minimo {g.min_fresh_sources}) "
                     f"su {len(specs)} pagine trovate"]))
        a.score = self._score(a)
        return a

    def _score(self, a: TopicAssessment) -> float:
        """0-10, from measures only: demand (long tail), room (few recent titles), currency
        (fresh official pages). Shown with its parts in the report."""
        primary = next((k for k in a.keywords if k.keyword == a.primary_keyword), None)
        if primary is None:
            return 0.0
        demand = min(10.0, primary.longtail)  # ten or more completions is a lot for a book niche
        recent = primary.recent_titles
        room = 5.0 if recent is None else max(0.0, 10 - 10 * recent / max(1, self.gates.max_recent_titles))
        currency = min(10.0, 2.5 * len(a.fresh_sources) + 0.5 * len(a.official_sources))
        return round(0.4 * demand + 0.3 * room + 0.3 * currency, 2)

    def choose(self, ideas: list[NicheIdea], domains: list[str], validate_top: int = 3
               ) -> tuple[TopicAssessment | None, list[TopicAssessment]]:
        """Cheap gates for every idea; the sources gate only for the best ``validate_top``."""
        first = [self.assess(i, domains, fetch_sources=False) for i in ideas]
        ranked = sorted((a for a in first if a.passed), key=lambda a: -a.score)
        finals = [self.assess(a.idea, domains) for a in ranked[:validate_top]]
        by_name = {a.idea.name: a for a in finals}
        everything = [by_name.get(a.idea.name, a) for a in first]
        winners = sorted((a for a in finals if a.passed), key=lambda a: -a.score)
        return (winners[0] if winners else None), sorted(everything, key=lambda a: -a.score)
