"""Gate 1 — the niche gate.

Runs between station 1 and station 2, on the niche file as written to disk.
The checklist item it enforces: "You can name the actual demand signal — not a
hunch. And you've checked it for trademark or brand exposure."
"""

from __future__ import annotations

from ..niche import SIGNALS_BY_KEY, niche_from_dict
from ..spec.kdp import KDP_SPEC, PAPER_TYPES, validate_page_count
from ..spec.kdp import trim_size as lookup_trim
from ..errors import SpecViolation
from .base import Gate, GateInput, GateReport


class NicheGate(Gate):
    gate_id = "g1_niche"
    title = "Is this niche worth a book at all?"
    requires = ("niche", "niche_score")

    def evaluate(self, data: GateInput, report: GateReport) -> None:
        niche = niche_from_dict(data.json("niche"))
        score = data.json("niche_score")
        cfg = self.config.niche_gate
        report.metrics = {
            "total_score": score["total"],
            "min_score": cfg.min_score,
            "missing_signals": score["missing_signals"],
            "unevidenced_signals": score["unevidenced_signals"],
        }

        total = float(score["total"])
        report.add(
            "score_threshold",
            total >= cfg.min_score,
            f"weighted niche score {total:.2f}/10 against a bar of {cfg.min_score}",
            {"total": total, "min_score": cfg.min_score},
        )

        missing = score["missing_signals"]
        report.add(
            "signals_complete",
            not missing,
            "all ten signals scored"
            if not missing
            else f"{len(missing)} signal(s) never scored: {', '.join(missing)}",
            {"missing": missing},
        )

        required = list(cfg.require_evidence_for)
        hunches = [
            key
            for key in required
            if key in SIGNALS_BY_KEY
            and not (niche.signal(key) and niche.signal(key).has_evidence)
        ]
        report.add(
            "demand_evidence_named",
            not hunches,
            "every load-bearing signal names its source"
            if not hunches
            else f"scored on a hunch, with no evidence: {', '.join(hunches)}",
            {"required": required, "without_evidence": hunches},
        )

        demand = niche.signal("demand_volume")
        demand_score = demand.score if demand else 0.0
        report.add(
            "demand_floor",
            demand_score >= cfg.min_demand_signal,
            f"demand signal {demand_score:.1f}/10 against a floor of {cfg.min_demand_signal}",
            {"demand_volume": demand_score},
        )

        report.add(
            "trademark_checked",
            niche.trademark.checked and bool(niche.trademark.source.strip()),
            "trademark exposure checked against "
            f"{niche.trademark.source or 'nothing'}"
            + (f" on {niche.trademark.checked_on}" if niche.trademark.checked_on else ""),
            niche.trademark.as_dict(),
        )

        tm_signal = niche.signal("trademark_risk")
        tm_risk = tm_signal.score if tm_signal else 10.0
        report.add(
            "trademark_clear",
            not niche.trademark.hits and tm_risk <= cfg.max_trademark_risk,
            f"trademark risk {tm_risk:.1f} (max {cfg.max_trademark_risk})"
            + (f", hits: {'; '.join(niche.trademark.hits)}" if niche.trademark.hits else ", no hits"),
            {"risk": tm_risk, "hits": niche.trademark.hits},
        )

        gaps = niche.competitor.gaps
        differentiation = niche.signal("differentiation")
        report.add(
            "incumbent_gap_named",
            bool(gaps) or bool(differentiation and differentiation.has_evidence),
            f"{len(gaps)} named weakness(es) in the incumbent"
            if gaps
            else "no incumbent weakness and no evidenced angle — "
            "this book would compete on nothing",
            {"gaps": gaps},
        )

        report.add(
            "audience_named",
            bool(niche.audience.strip()),
            f"audience: {niche.audience}" if niche.audience.strip()
            else "no audience named; every listing decision downstream would be improvised",
        )

        seeds = niche.keywords_seed
        report.add(
            "keyword_seeds",
            len(seeds) >= 3,
            f"{len(seeds)} keyword seed(s) carried from the niche evidence "
            f"(the listing is written from these, not improvised at the upload screen)",
            {"keywords_seed": seeds},
        )

        self._check_constraints(niche, report)

    def _check_constraints(self, niche, report: GateReport) -> None:
        constraints = niche.constraints
        trim = constraints.get("trim_size", self.config.brand.default_trim)
        paper = constraints.get("paper", self.config.brand.default_paper)
        target_pages = constraints.get("target_pages")

        try:
            lookup_trim(trim)
            trim_ok, trim_reason = True, f"trim {trim} is a KDP size"
        except SpecViolation as exc:
            trim_ok, trim_reason = False, str(exc)
        report.add("trim_size_valid", trim_ok, trim_reason, {"trim_size": trim})

        report.add(
            "paper_valid",
            paper in PAPER_TYPES,
            f"paper {paper!r} is one of {', '.join(PAPER_TYPES)}",
            {"paper": paper},
        )

        if target_pages is None:
            report.add(
                "target_pages_set",
                True,
                "no target page count pinned; the book type will compute one",
                advisory=True,
            )
            return
        try:
            validate_page_count(int(target_pages), paper if paper in PAPER_TYPES else "bw_white")
            pages_ok, pages_reason = True, f"target of {target_pages} pages is printable"
        except (SpecViolation, ValueError, TypeError) as exc:
            pages_ok, pages_reason = False, str(exc)
        report.add(
            "target_pages_printable",
            pages_ok,
            pages_reason,
            {"target_pages": target_pages, "spec_min": KDP_SPEC["page_count"]["min"]},
        )
