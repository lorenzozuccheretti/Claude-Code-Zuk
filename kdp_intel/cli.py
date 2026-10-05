"""``kdpi``: the KDP-Intelligence-Engine command line.

    kdpi providers                         which services this environment can call
    kdpi autopilot [PROFILE]               choose a topic, validate it, write and typeset the book
    kdpi ingest   PROJECT                  fetch the project's sources into the vector store
    kdpi research PROJECT                  Analyst only: score the candidate niches
    kdpi mine     PROJECT                  Review Miner only: what competitors' readers miss
    kdpi run      PROJECT [--niche N]      the whole graph, ingest to PDF
    kdpi pending  PROJECT                  hand-off requests waiting for an answer
    kdpi answer   PROJECT TASK FILE        validate an answer and file it for the next run
    kdpi check    PROJECT --chapter N      fact-check a saved chapter draft again
    kdpi typeset  PROJECT [--engine E]     set the saved, verified chapters into a PDF
    kdpi cover-spec  PROJECT               wrap size + Canva guide for the final interior
    kdpi cover-check PROJECT COVER.pdf     verify the cover exported from Canva

``--llm`` picks the model backend: ``auto`` (Gemini free tier if
GEMINI_API_KEY is set, otherwise hand-off), ``handoff``, ``gemini``,
``openrouter``, ``ollama`` or ``claude`` (paid). Every answer is cached, so
nothing is ever asked twice. ``--offline FIXTURES.json`` swaps every data
provider for the fixture file.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .config import Credentials, Project
from .providers import Providers, build_providers


def _runtime(args: argparse.Namespace, need_llm: bool = True):
    from .graph import Runtime
    from .llm_free import make_llm
    from .providers.fixtures import Fixtures
    from .rag import open_store

    project = Project.load(args.project)
    workdir = project.workdir(args.root)
    creds = Credentials.from_env()
    llm = make_llm(args.llm, workdir / "llm_cache", args.model) if need_llm else None
    handoff = llm if llm is not None and llm.inner is None else None  # an agent answers, and can search
    providers = (Providers.offline(Fixtures(args.offline)) if args.offline
                 else build_providers(creds, project, cache_dir=workdir / "cache", handoff_llm=handoff))
    store = open_store(workdir, args.store, args.embedder, creds)
    return Runtime(project=project, llm=llm, providers=providers, store=store, workdir=workdir,
                   today=date.fromisoformat(args.today) if args.today else date.today(),
                   draft_proof=getattr(args, "draft_proof", False),
                   resume=not getattr(args, "fresh", False),
                   stop_after=getattr(args, "stop_after", ""))


def cmd_providers(args: argparse.Namespace) -> int:
    for name, ok in Credentials.from_env().available().items():
        print(f"{'✔' if ok else '✘'} {name}")
    print("✔ handoff (gratis: risponde questa sessione Claude Code o una chat)")
    print("✔ autocompletamento Amazon.it e Google (gratis, senza chiave)")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    rt = _runtime(args, need_llm=False)
    rt.ingestor.refresh = args.refresh
    counts = rt.ingestor.ingest_all(rt.project.sources)
    for url, n in counts.items():
        print(f"{n:4d} chunk  {url}")
    for err in rt.ingestor.errors:
        print(f"  ! {err}", file=sys.stderr)
    print(f"archivio: {rt.store.count()} chunk")
    return 0 if all(counts.values()) else 1


def cmd_research(args: argparse.Namespace) -> int:
    from .agents import Analyst

    rt = _runtime(args, need_llm=False)
    analyst = Analyst(rt.providers, rt.store, rt.ingestor, today=rt.today)
    for seeds in rt.project.research.niches.values():
        analyst.discover_sources(seeds, rt.project.research.verify_domains)
    report = analyst.research(rt.project.research.niches)
    rt.save("01_niche_report.json", report)
    for c in report.candidates:
        print(f"{c.total:5.2f}  {c.name}")
        for note in c.notes:
            print(f"       {note}")
    for gap in analyst.log:
        print(f"  ! {gap}", file=sys.stderr)
    return 0


def cmd_mine(args: argparse.Namespace) -> int:
    from .agents import ReviewMiner

    rt = _runtime(args)
    miner = ReviewMiner(rt.llm)
    research = rt.project.research
    reviews = miner.collect(rt.providers.reviews, research.competitor_asins, research.review_csv)
    gaps = miner.mine(reviews)
    rt.save("02_gap_report.json", gaps)
    for t in gaps.themes:
        print(f"{t.count:4d} ({t.share:.0%})  {t.theme.label}")
        for q in t.quotes[:2]:
            print(f"        «{q}»")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .graph import run

    rt = _runtime(args)
    final = run(rt, niche=args.niche)
    for line in final.get("log", []):
        print(line)
    for path in final.get("pending", []):
        print(f"  → rispondi a: {path}")
    print(f"stato: {final.get('status')}  {final.get('pdf', '')}")
    return {"printed": 0, "waiting": 3, "outline_ready": 4}.get(final.get("status", ""), 2)


def cmd_pending(args: argparse.Namespace) -> int:
    from .llm_free import CachedLLM

    project = Project.load(args.project)
    pending = CachedLLM(None, project.workdir(args.root) / "llm_cache").pending()
    for path in pending:
        print(path)
    print(f"{len(pending)} richieste in attesa")
    return 0


def cmd_answer(args: argparse.Namespace) -> int:
    from . import models
    from .llm_free import _json_from

    project = Project.load(args.project)
    folder = project.workdir(args.root) / "llm_cache"
    if not (folder / f"{args.task}.request.md").exists():
        print(f"nessuna richiesta {args.task} in {folder}", file=sys.stderr)
        return 1
    name = args.task.rsplit("-", 1)[0]
    schema = next(getattr(models, n) for n in dir(models) if n.lower() == name)
    text = _json_from(Path(args.file).read_text(encoding="utf-8"))
    try:
        parsed = schema.model_validate_json(text)
    except ValueError as exc:
        print(f"la risposta non rispetta {schema.__name__}:\n{exc}", file=sys.stderr)
        return 1
    (folder / f"{args.task}.json").write_text(parsed.model_dump_json(indent=1), encoding="utf-8")
    print(f"ok: {args.task} ({schema.__name__})")
    return 0


def cmd_cover_spec(args: argparse.Namespace) -> int:
    from .cover import cover_spec, write_spec

    project = Project.load(args.project)
    workdir = project.workdir(args.root)
    pdf = Path(args.pdf) if args.pdf else workdir / "05_interior" / "interior.pdf"
    cs = cover_spec(pdf, project.book.trim, project.book.paper)
    paths = write_spec(cs, workdir / "06_cover")
    d = cs.as_dict()
    print(f"pagine {d['page_count']}, dorso {d['spine_width_in']} in ({d['spine_width_mm']} mm)")
    print(f"Canva, dimensioni personalizzate: {d['canva']['dimensioni_personalizzate']}")
    print(f"testo sul dorso: {d['canva']['testo_sul_dorso']}")
    for p in paths.values():
        print(p)
    return 0


def cmd_cover_check(args: argparse.Namespace) -> int:
    from .cover import check_cover, cover_spec

    project = Project.load(args.project)
    workdir = project.workdir(args.root)
    pdf = Path(args.pdf) if args.pdf else workdir / "05_interior" / "interior.pdf"
    problems = check_cover(args.cover, cover_spec(pdf, project.book.trim, project.book.paper))
    for problem in problems:
        print(f"  ! {problem}", file=sys.stderr)
    print("copertina OK" if not problems else "copertina da correggere")
    return 0 if not problems else 1


def cmd_check(args: argparse.Namespace) -> int:
    from .agents import FactChecker
    from .models import ChapterDraft

    rt = _runtime(args)
    draft = rt.load(f"chapters/{args.chapter:02d}.draft.json", ChapterDraft)
    if draft is None:
        print(f"nessuna bozza per il capitolo {args.chapter}", file=sys.stderr)
        return 1
    p = rt.project
    checker = FactChecker(rt.llm, rt.store, p.quality, web=rt.providers.web_search, ingestor=rt.ingestor,
                          verify_domains=p.research.verify_domains, today=rt.today)
    report = checker.check(args.chapter, draft)
    rt.save(f"chapters/{args.chapter:02d}.check.json", report)
    for v in report.verdicts:
        print(f"{v.status:16s} {v.claim.text[:110]}")
        if v.note:
            print(f"{'':16s}   {v.note}")
    print("OK" if report.passed else "KO", report.counts)
    return 0 if report.passed else 2


def cmd_typeset(args: argparse.Namespace) -> int:
    from .models import ChapterDraft, FactCheckReport, Outline
    from .typeset import BookContent, typeset

    project = Project.load(args.project)
    workdir = project.workdir(args.root)
    outline = project.outline or Outline.model_validate_json((workdir / "04_outline.json").read_text("utf-8"))
    chapters, unverified = [], []
    for spec in outline.chapters:
        base = workdir / "chapters" / f"{spec.number:02d}"
        chapters.append(ChapterDraft.model_validate_json(base.with_suffix(".draft.json").read_text("utf-8")))
        check = base.with_suffix(".check.json")
        if not check.exists() or not FactCheckReport.model_validate_json(check.read_text("utf-8")).passed:
            unverified.append(spec.number)
    if unverified and not args.draft_proof:
        print(f"capitoli non verificati: {unverified}; usa --draft-proof per una bozza", file=sys.stderr)
        return 2
    book = BookContent(meta=project.book, outline=outline, chapters=chapters,
                       sources=json.loads((workdir / "sources.json").read_text("utf-8")),
                       facts_as_of=project.facts_as_of.isoformat(), index_terms=project.index_terms)
    from .graph import metadata_problems

    result = typeset(book, workdir / "05_interior", engine=args.engine)
    print(f"{result.pdf}  {result.preflight.pages} pagine, {result.passes} passaggi")
    problems = result.preflight.problems + metadata_problems(project.book)
    for problem in problems:
        print(f"  ! {problem}", file=sys.stderr)
    return 0 if not problems else 1


def cmd_autopilot(args: argparse.Namespace) -> int:
    from .autopilot import Autopilot, AutopilotProfile
    from .llm_free import make_llm
    from .providers.fixtures import Fixtures

    profile = AutopilotProfile.load(args.profile)
    providers = Providers.offline(Fixtures(args.offline)) if args.offline else None
    pilot = Autopilot(profile, Path(args.root), lambda folder: make_llm(args.llm, folder, args.model),
                      Credentials.from_env(), date.fromisoformat(args.today) if args.today else date.today(),
                      store=args.store, embedder=args.embedder, providers=providers)
    result = pilot.run()
    for line in result.get("log", []):
        print(line)
    for path in result.get("pending", []):
        print(f"  → rispondi a: {path}")
    for key in ("project", "pdf", "listing"):
        if result.get(key):
            print(f"{key}: {result[key]}")
    print(f"stato: {result.get('status')} (fase: {result.get('stage')})")
    return {"printed": 0, "proof": 0, "waiting": 3, "outline_ready": 4, "no_topic": 5}.get(result.get("status", ""), 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kdpi", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def project_cmd(name: str, fn, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("project", type=Path)
        sp.add_argument("--root", default=".", help="where the work directory lives")
        sp.add_argument("--store", default="chroma", choices=["chroma", "memory", "pinecone"])
        sp.add_argument("--embedder", default="hashing", help="hashing | multilingual | <model>")
        sp.add_argument("--offline", default="", help="fixture JSON replacing every provider")
        sp.add_argument("--llm", default="auto",
                        choices=["auto", "handoff", "gemini", "openrouter", "ollama", "claude"])
        sp.add_argument("--model", default="", help="model id for the chosen backend")
        sp.add_argument("--today", default="", help="pin the date (YYYY-MM-DD) for reproducible runs")
        sp.set_defaults(fn=fn)
        return sp

    sub.add_parser("providers", help="show available services").set_defaults(fn=cmd_providers)
    ap = sub.add_parser("autopilot", help="choose, validate, write and typeset a book unattended")
    ap.add_argument("profile", nargs="?", default="", help="autopilot profile YAML (seeds, author, gates)")
    ap.add_argument("--root", default=".")
    ap.add_argument("--store", default="chroma", choices=["chroma", "memory", "pinecone"])
    ap.add_argument("--embedder", default="hashing")
    ap.add_argument("--offline", default="")
    ap.add_argument("--llm", default="auto", choices=["auto", "handoff", "gemini", "openrouter", "ollama", "claude"])
    ap.add_argument("--model", default="")
    ap.add_argument("--today", default="")
    ap.set_defaults(fn=cmd_autopilot)
    ing = project_cmd("ingest", cmd_ingest, "fetch sources into the vector store")
    ing.add_argument("--refresh", action="store_true", help="fetch sources already in the store again")
    project_cmd("research", cmd_research, "score candidate niches")
    project_cmd("mine", cmd_mine, "mine competitor reviews")
    run_p = project_cmd("run", cmd_run, "run the whole pipeline")
    run_p.add_argument("--niche", default="")
    run_p.add_argument("--draft-proof", action="store_true", help="typeset even if a chapter failed")
    run_p.add_argument("--fresh", action="store_true", help="ignore saved artefacts")
    run_p.add_argument("--stop-after", default="", choices=["", "architect"],
                       help="architect: stop when the outline is ready for approval")
    project_cmd("pending", cmd_pending, "list hand-off requests waiting for an answer")
    ans = project_cmd("answer", cmd_answer, "validate and file a hand-off answer")
    ans.add_argument("task")
    ans.add_argument("file")
    cs = project_cmd("cover-spec", cmd_cover_spec, "cover size and Canva guide")
    cs.add_argument("--pdf", default="")
    cc = project_cmd("cover-check", cmd_cover_check, "check the cover PDF exported from Canva")
    cc.add_argument("cover")
    cc.add_argument("--pdf", default="")
    check_p = project_cmd("check", cmd_check, "fact-check a saved chapter")
    check_p.add_argument("--chapter", type=int, required=True)
    ts = project_cmd("typeset", cmd_typeset, "typeset saved chapters")
    ts.add_argument("--engine", default=None, choices=["typst", "weasyprint"])
    ts.add_argument("--draft-proof", action="store_true")

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
