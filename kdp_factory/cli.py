"""``kdp`` — the command line around the engine.

    kdp types                       what this factory can build
    kdp spec                        the KDP spec card, in one screen
    kdp score   <niche.yaml>        station 1 + gate 1 only
    kdp scout   <niches.csv>        rank many niches before building any
    kdp build   <niche.yaml>        the whole run, gates enforced
    kdp upload  <run-dir>           dry run, or drive the browser and stop
    kdp gates                       how often each gate has actually failed
    kdp show    <run-dir>           what a finished run produced
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .booktypes import available
from .config import EngineConfig, load_config
from .errors import KdpFactoryError
from .gates.base import summarize_telemetry
from .niche import load_niche, load_niche_csv, rank
from .run.history import describe, load_used_content
from .run.pipeline import Pipeline
from .spec.kdp import spec_card_text


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
        stream=sys.stderr,
    )


def _config(args: argparse.Namespace) -> EngineConfig:
    config = load_config(getattr(args, "config", None))
    if getattr(args, "output", None):
        config = config.with_output_root(args.output)
    return config


# ------------------------------------------------------------------ commands
def cmd_types(args: argparse.Namespace) -> int:
    for book_type in available():
        print(f"{book_type.key:<10} {book_type.label}")
        print(f"{'':<10} {book_type.description}")
        options = ", ".join(f"{k}={v}" for k, v in book_type.default_options.items())
        print(f"{'':<10} options: {options}\n")
    return 0


def cmd_spec(args: argparse.Namespace) -> int:
    print(spec_card_text())
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    from .gates.base import GateInput
    from .gates.g1_niche import NicheGate

    config = _config(args)
    niche = load_niche(args.niche)
    score = niche.score()
    print(f"{niche.niche}  [{niche.book_type}]")
    print(score.table())

    pipeline = Pipeline(config)
    result = pipeline.run(niche, seed=args.seed, stop_after=1)
    print()
    for report in result.gate_reports:
        print(report.to_text())
    if result.failed_gate is not None:
        print(f"\nartifacts: {result.root}")
        return 2
    print(f"\nartifacts: {result.root}")
    return 0


def cmd_scout(args: argparse.Namespace) -> int:
    niches = load_niche_csv(args.csv)
    config = _config(args)
    bar = config.niche_gate.min_score
    print(f"{'score':>6}  {'bar':>4}  niche")
    for niche, score in rank(niches):
        flag = "PASS" if score.total >= bar else "    "
        missing = (
            f"  (missing: {', '.join(score.missing_signals)})" if score.missing_signals else ""
        )
        print(f"{score.total:>6.2f}  {flag:>4}  {niche.niche}{missing}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    config = _config(args)
    niche = load_niche(args.niche)
    options = json.loads(args.options) if args.options else None
    avoid = load_used_content(args.avoid) if args.avoid else None
    if avoid:
        print(describe(avoid, args.avoid))
    pipeline = Pipeline(config)
    result = pipeline.run(
        niche, seed=args.seed, stop_after=args.stop_after, options=options, avoid=avoid
    )

    for report in result.gate_reports:
        print(report.to_text())
        print()

    if not result.ok:
        print(result.summary())
        print(f"see {result.root / 'STOPPED.md'}")
        return 2

    print(result.summary())
    print(f"\nartifacts in {result.root}:")
    for artifact in result.context.manifest.artifacts:
        print(f"  {artifact.role:<22} {artifact.path}")
    print(f"\nnext: read {result.root / '06_review' / 'review_checklist.md'}")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    from .upload.playwright_driver import run_from_file

    plan_path = Path(args.run_dir)
    if plan_path.is_dir():
        plan_path = plan_path / "05_upload" / "upload_plan.json"
    if not plan_path.is_file():
        print(f"no upload plan at {plan_path}", file=sys.stderr)
        return 1
    run_from_file(plan_path, dry_run=not args.execute, user_data_dir=args.profile)
    return 0


def cmd_gates(args: argparse.Namespace) -> int:
    config = _config(args)
    stats = summarize_telemetry(config.telemetry_path)
    if not stats:
        print(f"no gate telemetry yet at {config.telemetry_path}")
        return 0
    print(f"{'gate':<16}{'runs':>6}{'failed':>8}{'rate':>8}  verdict")
    for gate_id, entry in sorted(stats.items()):
        print(
            f"{gate_id:<16}{entry['runs']:>6}{entry['failures']:>8}"
            f"{entry['fail_rate']:>8.0%}  {entry['verdict']}"
        )
        for name, count in sorted(
            entry["failed_checks"].items(), key=lambda kv: -kv[1]
        )[:3]:
            print(f"{'':<16}  caught {count}x: {name}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    root = Path(args.run_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        print(f"no manifest at {manifest_path}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"{manifest['slug']}  [{manifest['status']}]")
    print(f"book type: {manifest['book_type']}   seed: {manifest['seed']}")
    print("\nfacts:")
    for key, value in manifest["facts"].items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            print(f"  {key:<22} {value}")
    print("\ngates:")
    for gate in manifest["gates"]:
        verdict = "PASS" if gate["passed"] else "FAIL"
        print(f"  {gate['gate_id']:<14} {verdict}  {', '.join(gate['failed_checks']) or ''}")
    print("\nartifacts:")
    for artifact in manifest["artifacts"]:
        print(f"  {artifact['role']:<22} {artifact['path']}  ({artifact['bytes']:,} bytes)")
    return 0


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    # Shared options, accepted either before or after the subcommand — argparse
    # otherwise forces `kdp --config x build y`, which nobody types.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="log every station",
    )
    common.add_argument(
        "--config", default=argparse.SUPPRESS,
        help="engine config YAML (brand, quality bar, price policy)",
    )
    common.add_argument(
        "--output", default=argparse.SUPPRESS, help="output root (default: output/)"
    )

    parser = argparse.ArgumentParser(
        prog="kdp",
        parents=[common],
        description="A repeatable engine that builds print-ready KDP books.",
    )
    parser.add_argument("--version", action="version", version=f"kdp-factory {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("types", parents=[common], help="list the book types this factory can build")
    p.set_defaults(func=cmd_types)

    p = subparsers.add_parser("spec", parents=[common], help="print the KDP spec card")
    p.set_defaults(func=cmd_spec)

    p = subparsers.add_parser("score", parents=[common], help="score one niche and run the niche gate")
    p.add_argument("niche")
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=cmd_score)

    p = subparsers.add_parser("scout", parents=[common], help="rank many niches from a CSV")
    p.add_argument("csv")
    p.set_defaults(func=cmd_scout)

    p = subparsers.add_parser("build", parents=[common], help="run the whole engine on one niche")
    p.add_argument("niche")
    p.add_argument("--seed", type=int, default=0, help="same niche + seed = same book")
    p.add_argument(
        "--stop-after", type=int, default=6, metavar="STATION",
        help="stop after this station (1-6)",
    )
    p.add_argument("--options", help="JSON of book-type options, e.g. '{\"target_pages\": 90}'")
    p.add_argument(
        "--avoid", action="append", metavar="RUN_DIR", default=[],
        help="do not reuse content from this previous run (or folder of runs); "
             "repeatable — use it when publishing a second book in one niche",
    )
    p.set_defaults(func=cmd_build)

    p = subparsers.add_parser("upload", parents=[common], help="dry-run or drive the KDP form, stopping at Publish")
    p.add_argument("run_dir", help="a run directory, or a path to upload_plan.json")
    p.add_argument(
        "--execute", action="store_true",
        help="actually open a browser (you log in; it still stops before Publish)",
    )
    p.add_argument("--profile", help="persistent browser profile dir, to keep your login")
    p.set_defaults(func=cmd_upload)

    p = subparsers.add_parser("gates", parents=[common], help="how often each gate has actually failed something")
    p.set_defaults(func=cmd_gates)

    p = subparsers.add_parser("show", parents=[common], help="summarise a finished run")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    try:
        return args.func(args)
    except KdpFactoryError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
