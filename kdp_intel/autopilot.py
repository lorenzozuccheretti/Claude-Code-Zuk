"""Autopilot: from a list of broad seeds to a typeset book, with no human step.

    scout ─► project ─► outline ─► listing ─► chapters ⇄ fact-check ─► PDF ─► cover spec
      │                                                                 │
      └─ no topic passes the gates: stop and report ◄── blocked chapter ┘

1. **scout**    picks the topic (``agents.scout``): Amazon.it book-search
                autocomplete, a model groups the phrases, then three gates
                measured in code: keyword, competition, official sources.
2. **project**  writes ``<projects_dir>/<slug>.yaml``: niche, validated
                keywords, the official pages the scout found and fetched.
3. **outline**  persona and outline from the evidence (the existing graph,
                stopped after the architect).
4. **listing**  title, subtitle, description, the seven keyword boxes and two
                categories, held to KDP's rules (``agents.publisher``).
5. **book**     the existing graph to the end: chapters, fact-check with
                revisions, typesetting; then the Canva cover guide.

Every stage saves its output and every model answer and web request is
cached, so a run that stops (a hand-off request waiting, a daily quota
spent) resumes exactly where it stopped when it is started again. A book
whose chapters cannot be proven is *blocked*, never printed.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from .agents.scout import Gates

DEFAULT_SEEDS = [
    "successione", "testamento", "pensione", "730", "partita iva", "regime forfettario", "isee",
    "assegno unico", "invalidità civile", "legge 104", "caregiver", "badante", "condominio",
    "affitto", "mutuo", "separazione", "bonus casa", "privacy gdpr", "intelligenza artificiale",
]
DEFAULT_DOMAINS = ["normattiva.it", "gazzettaufficiale.it", "agenziaentrate.gov.it", "inps.it",
                   "governo.it", "lavoro.gov.it", "mef.gov.it", "notariato.it", "istat.it"]


class AutopilotProfile(BaseModel):
    """What the autopilot may choose from and who signs the book."""

    name: str = "autopilot"
    author: str = ""
    publisher: str = ""
    seeds: list[str] = DEFAULT_SEEDS
    exclude: list[str] = []  # topics already published
    verify_domains: list[str] = DEFAULT_DOMAINS
    gates: Gates = Gates()
    max_ideas: int = 6
    validate_top: int = 3  # topics that get the (expensive) sources gate
    chapters: int = 11
    trim: str = "6x9"
    paper: str = "bw_white"
    engine: str = "typst"
    projects_dir: str = "intel_projects/auto"

    @classmethod
    def load(cls, path: str | Path | None) -> "AutopilotProfile":
        if not path:
            return cls()
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def slugify(text: str, limit: int = 40) -> str:
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")[:limit].strip("-")


def _save(folder: Path, name: str, obj: Any) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, list):
        data = [o.model_dump(mode="json") if hasattr(o, "model_dump") else o for o in obj]
    else:
        data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
    (folder / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def project_data(profile: AutopilotProfile, decision, manifest: dict[str, dict], today: date) -> dict:
    """The project file for the chosen topic: only tier 1-2 pages the scout fetched."""
    by_url = {m["url"]: m for m in manifest.values()}
    sources = []
    for url in decision.source_urls:
        m = by_url.get(url)
        if not m or not m.get("chunks") or m.get("authority", 3) > 2:
            continue
        spec = {"url": url, "title": m.get("title", ""), "publisher": m.get("publisher", ""),
                "kind": m.get("kind")}
        if m.get("published"):
            spec["published"] = m["published"]
        sources.append(spec)
    keywords = [decision.primary_keyword] + [k.keyword for k in decision.keywords
                                             if k.passed and k.keyword != decision.primary_keyword]
    return {
        "slug": f"{slugify(decision.primary_keyword)}-{today.year}",
        "facts_as_of": today.isoformat(),
        "book": {
            "title": decision.primary_keyword.capitalize(), "subtitle": "",
            "author": profile.author or "Autore da definire",
            "publisher": profile.publisher or "Editore da definire",
            "year": today.year, "trim": profile.trim, "paper": profile.paper, "engine": profile.engine,
        },
        "research": {"niches": {decision.idea.name: keywords},
                     "verify_domains": profile.verify_domains,
                     # the scout has already searched; more searching is the writer's evidence
                     # retrieval, not new web queries
                     "discover_sources": False,
                     "reddit_queries": keywords[:3]},
        "sources": sources,
        "index_terms": keywords[:12],
    }


class Autopilot:
    def __init__(self, profile: AutopilotProfile, root: Path, make_llm, creds, today: date,
                 store: str = "chroma", embedder: str = "hashing", providers: Any = None) -> None:
        self.profile, self.root = profile, Path(root)
        self.make_llm, self.creds, self.today = make_llm, creds, today
        self.store_kind, self.embedder = store, embedder
        self.fixed_providers = providers  # tests: one offline provider set for every stage
        self.rundir = self.root / ".kdp_intel" / "autopilot" / slugify(profile.name)
        self.log: list[str] = []

    # ------------------------------------------------------------ helpers

    def _providers(self, llm, cache: Path, project=None):
        from .llm_free import CachedLLM  # noqa: PLC0415
        from .providers import build_providers  # noqa: PLC0415

        if self.fixed_providers is not None:
            return self.fixed_providers
        handoff = llm if isinstance(llm, CachedLLM) and llm.inner is None else None
        return build_providers(self.creds, project, cache_dir=cache, handoff_llm=handoff)

    def _waiting(self, stage: str, pending) -> dict:
        paths = [str(pending.folder / f"{t}.request.md") for t in pending.task_ids]
        self.log.append(f"{stage}: in attesa di risposta ({', '.join(pending.task_ids)})")
        return self._finish({"status": "waiting", "stage": stage, "pending": paths})

    def _finish(self, result: dict) -> dict:
        result["log"] = self.log + result.get("log", [])
        _save(self.rundir, "run.json", result)
        return result

    # ------------------------------------------------------------ stages

    def scout(self):
        from .agents import Analyst  # noqa: PLC0415
        from .agents.scout import Scout  # noqa: PLC0415
        from .rag import open_store  # noqa: PLC0415
        from .rag.ingest import Ingestor  # noqa: PLC0415

        p = self.profile
        llm = self.make_llm(self.rundir / "llm_cache")
        providers = self._providers(llm, self.rundir / "cache")
        store = open_store(self.rundir, self.store_kind, self.embedder, self.creds)
        ingestor = Ingestor(store, self.rundir / "sources.json", providers.fetcher, providers.unblocker,
                            today=self.today)
        analyst = Analyst(providers, store, ingestor, today=self.today)
        scout = Scout(llm, providers.suggest, providers.catalog, analyst, store, p.gates, self.today)
        try:
            harvest = scout.harvest(p.seeds)
            _save(self.rundir, "00_harvest.json", harvest)
            typed = sum(len(h["amazon"]) for h in harvest.values())
            self.log.append(f"scout: {typed} frasi digitate su Amazon.it per {len(p.seeds)} semi")
            ideas = scout.propose(harvest, p.exclude, p.max_ideas)
            _save(self.rundir, "01_ideas.json", ideas)
            self.log.append(f"scout: {len(ideas)} temi proposti: " + "; ".join(i.name for i in ideas))
            chosen, everything = scout.choose(ideas, p.verify_domains, p.validate_top)
        finally:
            self.log.extend(scout.log)
        _save(self.rundir, "02_assessments.json", everything)
        for a in everything:
            verdict = "OK" if a.passed else "scartato"
            self.log.append(f"  {a.score:4.1f} {verdict:8s} {a.idea.name} «{a.primary_keyword}» | "
                            + " | ".join(f"{g.name}: {'ok' if g.passed else 'NO'} ({'; '.join(g.reasons)})"
                                         for g in a.gates))
        if chosen is not None:
            _save(self.rundir, "03_decision.json", chosen)
        return chosen, ingestor.manifest

    def run(self) -> dict:
        from .config import Project  # noqa: PLC0415
        from .llm_free import PendingLLM  # noqa: PLC0415

        try:
            chosen, manifest = self.scout()
        except PendingLLM as pending:
            return self._waiting("scout", pending)
        if chosen is None:
            return self._finish({"status": "no_topic", "stage": "scout",
                                 "log": ["nessun tema supera tutti i controlli: vedi 02_assessments.json"]})
        self.log.append(f"scelto: «{chosen.idea.name}», keyword «{chosen.primary_keyword}»")

        data = project_data(self.profile, chosen, manifest, self.today)
        path = self.root / self.profile.projects_dir / f"{data['slug']}.yaml"
        if path.exists():  # resume: keep the title the listing already set
            project = Project.load(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            project = Project.load(path)
        self.log.append(f"progetto: {path} ({len(project.sources)} fonti ufficiali e di stampa)")
        return self.book(project, path, chosen)

    def _runtime(self, project, stop_after: str = ""):
        from .graph import Runtime  # noqa: PLC0415
        from .rag import open_store  # noqa: PLC0415

        workdir = project.workdir(self.root)
        llm = self.make_llm(workdir / "llm_cache")
        providers = self._providers(llm, workdir / "cache", project)
        store = open_store(workdir, self.store_kind, self.embedder, self.creds)
        return Runtime(project=project, llm=llm, providers=providers, store=store, workdir=workdir,
                       today=self.today, stop_after=stop_after, chapters_hint=self.profile.chapters)

    def book(self, project, path: Path, chosen) -> dict:
        from .agents.publisher import Publisher  # noqa: PLC0415
        from .config import Project  # noqa: PLC0415
        from .graph import run  # noqa: PLC0415
        from .llm_free import PendingLLM  # noqa: PLC0415
        from .models import Outline, PersonaDraft  # noqa: PLC0415

        rt = self._runtime(project, stop_after="architect")
        _save(rt.workdir, "00_topic.json", chosen)
        listing_file = rt.workdir / "07_listing.json"
        if not listing_file.exists():
            first = run(rt, niche=chosen.idea.name)
            self.log.extend(first.get("log", []))
            if first.get("status") != "outline_ready":
                return self._finish({"status": first.get("status"), "stage": "outline",
                                     "pending": first.get("pending", []), "project": str(path)})
            outline = rt.load("04_outline.json", Outline)
            persona = rt.load("03_persona.json", PersonaDraft)
            keywords = next(iter(project.research.niches.values()))
            try:
                listing, problems = Publisher(rt.llm).listing(
                    chosen.primary_keyword, keywords, outline, persona, [b.title for b in chosen.competitors])
            except PendingLLM as pending:
                return self._waiting("listing", pending)
            _save(rt.workdir, "07_listing.json", {"listing": listing.model_dump(), "problems": problems})
            (rt.workdir / "07_listing.md").write_text(listing_markdown(listing, problems), encoding="utf-8")
            self.log.append("listing: " + ("OK" if not problems else f"da rivedere: {'; '.join(problems)}"))
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            data["book"]["title"], data["book"]["subtitle"] = listing.title, listing.subtitle
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            project = Project.load(path)

        rt = self._runtime(project)
        final = run(rt, niche=chosen.idea.name)
        self.log.extend(final.get("log", []))
        result = {"status": final.get("status"), "stage": "book", "pending": final.get("pending", []),
                  "project": str(path), "pdf": final.get("pdf", ""),
                  "listing": str(rt.workdir / "07_listing.md")}
        if final.get("pdf"):
            from .cover import cover_spec, write_spec  # noqa: PLC0415

            cs = cover_spec(final["pdf"], project.book.trim, project.book.paper)
            result["cover"] = {k: str(v) for k, v in write_spec(cs, rt.workdir / "06_cover").items()}
        return self._finish(result)


def listing_markdown(listing, problems: list[str]) -> str:
    boxes = "\n".join(f"{i}. {k}" for i, k in enumerate(listing.backend_keywords, 1))
    cats = "\n".join(f"- {c}" for c in listing.categories)
    warn = ("\n\n## Da rivedere\n\n" + "\n".join(f"- {p}" for p in problems)) if problems else ""
    return (f"# Scheda Amazon KDP\n\n**Titolo:** {listing.title}\n\n**Sottotitolo:** {listing.subtitle}\n\n"
            f"## Descrizione\n\n{listing.description}\n\n## Parole chiave (7 caselle)\n\n{boxes}\n\n"
            f"## Categorie\n\n{cats}{warn}\n")
