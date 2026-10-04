"""``kdpi``: the KDP-Intelligence-Engine command line.

    kdpi providers                         which services this environment can call
    kdpi ingest   PROJECT                  fetch the project's sources into the vector store
    kdpi research PROJECT                  Analyst only: score the candidate niches
    kdpi mine     PROJECT                  Review Miner only: what competitors' readers miss
    kdpi run      PROJECT [--niche N]      the whole graph, ingest to PDF
    kdpi check    PROJECT --chapter N      fact-check a saved chapter draft again
    kdpi typeset  PROJECT [--engine E]     set the saved, verified chapters into a PDF

``--offline FIXTURES.json`` swaps every provider for the fixture file, and
``--store memory`` keeps the vector store in RAM; together they run the
pipeline with no network and no credentials (the LLM still needs Claude
unless a test injects one).
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
    from .llm import ClaudeLLM
    from .providers.fixtures import Fixtures
    from .rag import open_store

    project = Project.load(args.project)
    workdir = project.workdir(args.root)
    creds = Credentials.from_env()
    providers = Providers.offline(Fixtures(args.offline)) if args.offline else build_providers(creds, project)
    store = open_store(workdir, args.store, args.embedder, creds)
    llm = ClaudeLLM(model=args.model) if need_llm else None
    return Runtime(project=project, llm=llm, providers=providers, store=store, workdir=workdir,
                   today=date.fromisoformat(args.today) if args.today else date.today(),
                   draft_proof=getattr(args, "draft_proof", False),
                   resume=not getattr(args, "fresh", False))


def cmd_providers(args: argparse.Namespace) -> int:
    for name, ok in Credentials.from_env().available().items():
        print(f"{'✔' if ok else '✘'} {name}")
    print("✔ claude (credentials resolved by the Anthropic SDK at first call)")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    rt = _runtime(args, need_llm=False)
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
    print(f"stato: {final.get('status')}  {final.get('pdf', '')}")
    return 0 if final.get("status") == "printed" else 2


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
    result = typeset(book, workdir / "05_interior", engine=args.engine)
    print(f"{result.pdf}  {result.preflight.pages} pagine, {result.passes} passaggi")
    for problem in result.preflight.problems:
        print(f"  ! {problem}", file=sys.stderr)
    return 0 if result.preflight.ok else 1


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
        sp.add_argument("--model", default="claude-opus-5-5")
        sp.add_argument("--today", default="", help="pin the date (YYYY-MM-DD) for reproducible runs")
        sp.set_defaults(fn=fn)
        return sp

    sub.add_parser("providers", help="show available services").set_defaults(fn=cmd_providers)
    project_cmd("ingest", cmd_ingest, "fetch sources into the vector store")
    project_cmd("research", cmd_research, "score candidate niches")
    project_cmd("mine", cmd_mine, "mine competitor reviews")
    run_p = project_cmd("run", cmd_run, "run the whole pipeline")
    run_p.add_argument("--niche", default="")
    run_p.add_argument("--draft-proof", action="store_true", help="typeset even if a chapter failed")
    run_p.add_argument("--fresh", action="store_true", help="ignore saved artefacts")
    check_p = project_cmd("check", cmd_check, "fact-check a saved chapter")
    check_p.add_argument("--chapter", type=int, required=True)
    ts = project_cmd("typeset", cmd_typeset, "typeset saved chapters")
    ts.add_argument("--engine", default=None, choices=["typst", "weasyprint"])
    ts.add_argument("--draft-proof", action="store_true")

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
