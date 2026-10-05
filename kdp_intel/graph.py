"""The pipeline as a LangGraph state graph.

    ingest ─► analyst ─► review_miner ─► architect ─► writer ─► fact_checker ─┐
                                                        ▲                     │
                                                        ├── revise (failed, revisions left)
                                                        ├── next chapter (passed, or out of revisions)
                                                        └─────────────────────┴─► typeset | blocked

Agents are plain objects with their dependencies injected; the graph only
moves state between them and decides the route. Every node writes its
artefact to the project's work directory, so a run can be inspected, and
resumed: chapters whose fact-check already passed are not written again.
A book with any unproven chapter is *blocked* and never typeset, unless the
caller explicitly asks for a draft proof.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, TypedDict

from .agents import Analyst, Architect, FactChecker, ReviewMiner, Writer
from .agents.writer import lint
from .config import BookMeta, Project
from .llm import LLM
from .models import (
    ChapterDraft, FactCheckReport, GapReport, NicheReport, Outline, PersonaDraft,
)
from .providers import Providers
from .rag import retrieve
from .rag.ingest import Ingestor
from .rag.store import VectorStore


class BookState(TypedDict, total=False):
    niche: str
    seeds: list[str]
    niche_report: NicheReport
    gaps: GapReport
    persona: PersonaDraft
    outline: Outline
    chapter: int  # index into outline.chapters
    draft: ChapterDraft
    lint: list[str]
    revision: int
    drafts: dict[int, ChapterDraft]
    reports: dict[int, FactCheckReport]
    blocked: list[int]
    pdf: str
    status: str
    log: list[str]


@dataclass
class Runtime:
    project: Project
    llm: LLM
    providers: Providers
    store: VectorStore
    workdir: Path
    today: date = field(default_factory=date.today)
    draft_proof: bool = False  # typeset even if a chapter failed fact-checking
    resume: bool = True
    chapters_hint: int = 12
    stop_after: str = ""  # "architect": stop for the outline to be approved (and edited)

    def __post_init__(self) -> None:
        self.ingestor = Ingestor(self.store, self.workdir / "sources.json", self.providers.fetcher,
                                 self.providers.unblocker, today=self.today)

    def save(self, name: str, obj: Any) -> None:
        path = self.workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self, name: str, model: Any) -> Any:
        path = self.workdir / name
        return model.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def _log(state: BookState, msg: str) -> list[str]:
    return [*state.get("log", []), msg]


def build_graph(rt: Runtime):
    from langgraph.graph import END, START, StateGraph  # noqa: PLC0415

    p = rt.project
    analyst = Analyst(rt.providers, rt.store, rt.ingestor, today=rt.today)
    miner = ReviewMiner(rt.llm)
    architect = Architect(rt.llm, rt.store)
    writer = Writer(rt.llm, p.quality, p.facts_as_of)
    # A hand-off search would turn every failed claim into a question for the agent; the
    # writer's revision is the cheaper fix, so the checker only cross-checks with a real API.
    web = None if rt.providers.search_is_handoff else rt.providers.web_search
    checker = FactChecker(rt.llm, rt.store, p.quality, web=web, ingestor=rt.ingestor,
                          verify_domains=p.research.verify_domains, today=rt.today)

    def ingest(state: BookState) -> BookState:
        counts = rt.ingestor.ingest_all(p.sources)
        threads = 0
        if rt.providers.community is not None:
            for sub in p.research.subreddits:
                for q in p.research.reddit_queries:
                    try:
                        for t in rt.providers.community.threads(sub, q):
                            threads += rt.ingestor.ingest_thread(t) > 0
                    except Exception as exc:  # noqa: BLE001 - forums are optional evidence
                        rt.ingestor.errors.append(f"reddit r/{sub} «{q}»: {exc}")
        rt.save("ingest_errors.json", rt.ingestor.errors)
        ok = sum(1 for n in counts.values() if n)
        return {"log": _log(state, f"ingest: {ok}/{len(counts)} fonti, {threads} thread, "
                                   f"{rt.store.count()} chunk in archivio")}

    def research(state: BookState) -> BookState:
        niches = p.research.niches
        if not niches:
            raise ValueError("research.niches is empty: name at least one niche and its seed keywords")
        if p.research.discover_sources:
            for seeds in niches.values():
                analyst.discover_sources(seeds, p.research.verify_domains)
        report = analyst.research(niches)
        rt.save("01_niche_report.json", report)
        chosen = state.get("niche") or report.chosen
        return {"niche_report": report, "niche": chosen, "seeds": niches[chosen],
                "log": _log(state, f"analyst: scelta «{chosen}»; " + "; ".join(analyst.log))}

    def mine(state: BookState) -> BookState:
        reviews = miner.collect(rt.providers.reviews, p.research.competitor_asins, p.research.review_csv)
        gaps = miner.mine(reviews) if reviews else GapReport(
            reviews_considered=0, themes=[], missing_content=[], opportunity_statements=[])
        rt.save("02_gap_report.json", gaps)
        return {"gaps": gaps, "log": _log(state, f"review miner: {gaps.reviews_considered} recensioni critiche, "
                                                 f"{len(gaps.themes)} temi")}

    def architect_node(state: BookState) -> BookState:
        persona = rt.load("03_persona.json", PersonaDraft) if rt.resume else None
        persona = persona or architect.persona(state["niche"], state["seeds"], state.get("gaps"))
        rt.save("03_persona.json", persona)
        outline = p.outline or (rt.load("04_outline.json", Outline) if rt.resume else None)
        outline = outline or architect.outline(state["niche"], state["seeds"], persona, state.get("gaps"),
                                               rt.chapters_hint)
        rt.save("04_outline.json", outline)
        return {"persona": persona, "outline": outline, "chapter": 0, "revision": 0,
                "drafts": {}, "reports": {}, "blocked": [],
                "log": _log(state, f"architect: {len(outline.chapters)} capitoli")}

    def write(state: BookState) -> BookState:
        outline, idx = state["outline"], state["chapter"]
        spec = outline.chapters[idx]
        rev = state.get("revision", 0)
        name = f"chapters/{spec.number:02d}"
        evidence = retrieve(rt.store, spec.queries, k=6, max_age_days=p.quality.max_source_age_days,
                            today=rt.today)
        if rev == 0 and rt.resume:
            # Resume from the latest saved draft, never from the first one: a chapter that was
            # revised and verified must not be rewritten from scratch if a later check flags it.
            done = rt.load(f"{name}.check.json", FactCheckReport)
            prior = rt.load(f"{name}.draft.json", ChapterDraft)
            if prior is not None:
                status = "già verificato" if done is not None and done.passed else "ripreso dall'ultima bozza"
                return {"draft": prior, "lint": lint(prior, {h.chunk.source_id for h in evidence}, p.quality),
                        "log": _log(state, f"writer: cap. {spec.number} {status}")}
        previous = state.get("draft") if rev else None
        report = state.get("reports", {}).get(spec.number) if rev else None
        draft = writer.write(spec, outline, state.get("persona"), evidence, previous=previous,
                             failures=report.failures() if report else None,
                             lint_issues=state.get("lint") if rev else None)
        issues = lint(draft, {h.chunk.source_id for h in evidence}, p.quality)
        rt.save(f"{name}.draft.json", draft)
        return {"draft": draft, "lint": issues,
                "log": _log(state, f"writer: cap. {spec.number} rev. {rev}, {len(issues)} problemi di stile")}

    def fact_check(state: BookState) -> BookState:
        spec = state["outline"].chapters[state["chapter"]]
        report = checker.check(spec.number, state["draft"])
        passed = report.passed and not state.get("lint")
        report.passed = passed
        rt.save(f"chapters/{spec.number:02d}.check.json", report)
        return {"reports": {**state.get("reports", {}), spec.number: report},
                "log": _log(state, f"fact-checker: cap. {spec.number} {'OK' if passed else 'KO'} {report.counts}")}

    def route(state: BookState) -> str:
        spec = state["outline"].chapters[state["chapter"]]
        if state["reports"][spec.number].passed:
            return "advance"
        return "revise" if state.get("revision", 0) < p.quality.max_revisions else "give_up"

    def revise(state: BookState) -> BookState:
        return {"revision": state.get("revision", 0) + 1}

    def advance(state: BookState) -> BookState:
        spec = state["outline"].chapters[state["chapter"]]
        drafts = {**state.get("drafts", {}), spec.number: state["draft"]}
        blocked = list(state.get("blocked", []))
        if not state["reports"][spec.number].passed:
            blocked.append(spec.number)
        return {"drafts": drafts, "blocked": blocked, "chapter": state["chapter"] + 1, "revision": 0,
                "draft": None, "lint": []}  # type: ignore[typeddict-item]

    def more(state: BookState) -> str:
        if state["chapter"] < len(state["outline"].chapters):
            return "write"
        return "typeset" if not state.get("blocked") or rt.draft_proof else "blocked"

    def typeset_node(state: BookState) -> BookState:
        from .typeset import BookContent, typeset  # noqa: PLC0415

        outline = state["outline"]
        book = BookContent(
            meta=p.book, outline=outline,
            chapters=[state["drafts"][c.number] for c in outline.chapters],
            sources=json.loads((rt.workdir / "sources.json").read_text(encoding="utf-8")),
            facts_as_of=p.facts_as_of.isoformat(), index_terms=p.index_terms,
        )
        result = typeset(book, rt.workdir / "05_interior")
        problems = result.preflight.problems + metadata_problems(p.book)
        status = "printed" if not problems and not state.get("blocked") else "proof"
        return {"pdf": str(result.pdf), "status": status,
                "log": _log(state, f"typeset: {result.preflight.pages} pagine, "
                                   f"{'OK' if not problems else problems}")}

    def blocked_node(state: BookState) -> BookState:
        rt.save("blocked.json", {"chapters": state["blocked"]})
        return {"status": "blocked",
                "log": _log(state, f"bloccato: capitoli non verificati {state['blocked']}")}

    g = StateGraph(BookState)
    for name, fn in [("ingest", ingest), ("analyst", research), ("review_miner", mine),
                     ("architect", architect_node), ("writer", write), ("fact_checker", fact_check),
                     ("revise", revise), ("advance", advance), ("typeset", typeset_node),
                     ("blocked", blocked_node)]:
        g.add_node(name, fn)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "analyst")
    g.add_edge("analyst", "review_miner")
    g.add_edge("review_miner", "architect")
    g.add_conditional_edges("architect", lambda s: "pause" if rt.stop_after == "architect" else "write",
                            {"pause": "outline_ready", "write": "writer"})
    g.add_node("outline_ready", lambda s: {"status": "outline_ready", "log": _log(
        s, "indice pronto: rivedi 04_outline.json, poi rilancia senza --stop-after")})
    g.add_edge("outline_ready", END)
    g.add_edge("writer", "fact_checker")
    g.add_conditional_edges("fact_checker", route, {"advance": "advance", "revise": "revise",
                                                     "give_up": "advance"})
    g.add_edge("revise", "writer")
    g.add_conditional_edges("advance", more, {"write": "writer", "typeset": "typeset", "blocked": "blocked"})
    g.add_edge("typeset", END)
    g.add_edge("blocked", END)
    return g.compile()


def metadata_problems(book: BookMeta) -> list[str]:
    """Placeholders that must not reach a printed copyright page."""
    out = []
    for name in ("author", "publisher", "title"):
        value = getattr(book, name) or ""
        if not value.strip() or "da definire" in value.lower():
            out.append(f"book.{name} non impostato ({value!r})")
    return out


def run(rt: Runtime, niche: str = "") -> BookState:
    from .llm_free import PendingLLM  # noqa: PLC0415

    graph = build_graph(rt)
    max_chapters = len(rt.project.outline.chapters) if rt.project.outline else 40
    limit = 20 + max_chapters * (rt.project.quality.max_revisions + 1) * 4
    state: BookState = {"log": []}
    if niche:
        state["niche"] = niche
    try:
        final = graph.invoke(state, config={"recursion_limit": limit})
    except PendingLLM as pending:
        # A hand-off request is waiting. Everything answered so far is cached,
        # so the next run replays it for free and stops at the next question.
        final = {"status": "waiting", "pending": [str(pending.folder / f"{t}.request.md")
                                                  for t in pending.task_ids],
                 "log": [f"in attesa di risposta: {', '.join(pending.task_ids)}"]}
    rt.save("run_log.json", {"status": final.get("status"), "log": final.get("log", []),
                             "pdf": final.get("pdf", ""), "pending": final.get("pending", [])})
    return final
