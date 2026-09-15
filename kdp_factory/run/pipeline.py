"""The engine: six stations, three gates, one niche in, a book package out.

    niche ─► [1 niche] ─(G1)─► [2 interior] ─(G2)─► [3 cover] ─(G3)─►
            [4 listing] ─► [5 upload plan] ─► [6 your review]

Two properties are worth stating plainly, because they are the whole point:

* **A gate can stop the build.** ``GateFailure`` propagates; the run ends with
  status ``gate_failed`` and the report on disk says which check failed and why.
* **A gate never sees the station that produced the artifact.** It is handed
  file paths and reads them itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..booktypes import get_book_type, is_registered
from ..config import EngineConfig
from ..errors import ConfigError, GateFailure
from ..gates.base import Gate, GateInput, GateReport, record_gate_result
from ..gates.g1_niche import NicheGate
from ..gates.g2_substance import SubstanceGate
from ..gates.g3_print import PrintReadyGate
from ..niche import Niche
from ..stations.s1_niche import NicheStation
from ..stations.s2_interior import InteriorStation
from ..stations.s3_cover import CoverStation
from ..stations.s4_listing import ListingStation
from ..stations.s5_upload import UploadStation
from ..stations.s6_review import ReviewStation
from .context import BuildContext

log = logging.getLogger("kdp_factory")

PRIMITIVES = (str, int, float, bool, type(None))


@dataclass
class BuildResult:
    """What a run produced, including the gate that stopped it."""

    ok: bool
    context: BuildContext
    failed_gate: GateReport | None = None
    gate_reports: list[GateReport] = field(default_factory=list)
    error: str = ""

    @property
    def slug(self) -> str:
        return self.context.slug

    @property
    def root(self) -> Path:
        return self.context.root

    def summary(self) -> str:
        if self.ok:
            facts = self.context.manifest.facts
            return (
                f"{self.slug}: {facts.get('page_count', '?')} pages, "
                f"spine {facts.get('spine_width_in', '?')} in, "
                f"${facts.get('price_usd', '?')} → ${facts.get('royalty_usd', '?')}/copy"
            )
        if self.failed_gate is not None:
            failing = ", ".join(c.name for c in self.failed_gate.blocking_failures)
            return f"{self.slug}: stopped by {self.failed_gate.gate_id} ({failing})"
        return f"{self.slug}: failed — {self.error}"


class Pipeline:
    """Runs the stations in order, enforcing the gate between each pair."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        self.stations = {
            1: NicheStation(self.config),
            2: InteriorStation(self.config),
            3: CoverStation(self.config),
            4: ListingStation(self.config),
            5: UploadStation(self.config),
            6: ReviewStation(self.config),
        }
        self.gates: dict[int, Gate] = {
            1: NicheGate(self.config),
            2: SubstanceGate(self.config),
            3: PrintReadyGate(self.config),
        }

    # ----------------------------------------------------------------- run
    def run(
        self,
        niche: Niche,
        seed: int = 0,
        output_root: str | Path | None = None,
        stop_after: int = 6,
        options: dict[str, Any] | None = None,
        avoid: set[str] | None = None,
    ) -> BuildResult:
        if not is_registered(niche.book_type):
            raise ConfigError(
                f"niche {niche.niche!r} asks for book type {niche.book_type!r}, which is "
                f"not registered"
            )
        get_book_type(niche.book_type)  # fail early if the type is broken

        ctx = BuildContext(
            niche_slug=niche.slug,
            book_type=niche.book_type,
            seed=seed,
            config=self.config,
            output_root=output_root,
        )
        if avoid:
            options = {**(options or {}), "avoid": avoid}
            ctx.fact("avoided_content_units", len(avoid))

        result = BuildResult(ok=False, context=ctx)
        plan = None
        log.info("build %s → %s", ctx.slug, ctx.root)

        try:
            self._station(ctx, 1, niche=niche)
            if stop_after < 1:
                return self._finish(ctx, result)
            self._gate(ctx, 1, result)

            if stop_after >= 2:
                plan = self._station(ctx, 2, niche=niche, options=options)["plan"]
                self._gate(ctx, 2, result)
            if stop_after >= 3 and plan is not None:
                self._station(ctx, 3, niche=niche, plan=plan)
                self._gate(ctx, 3, result)
            if stop_after >= 4 and plan is not None:
                self._station(ctx, 4, niche=niche, plan=plan)
            if stop_after >= 5:
                self._station(ctx, 5)
            if stop_after >= 6:
                self._station(ctx, 6)
        except GateFailure as exc:
            result.failed_gate = exc.report
            ctx.manifest.status = "gate_failed"
            ctx.save()
            self._write_stop_note(ctx, exc.report)
            return result
        except Exception as exc:  # noqa: BLE001 - recorded on the manifest, then surfaced
            result.error = f"{type(exc).__name__}: {exc}"
            ctx.manifest.status = "error"
            ctx.save()
            raise

        return self._finish(ctx, result)

    def _finish(self, ctx: BuildContext, result: BuildResult) -> BuildResult:
        ctx.manifest.status = "complete"
        ctx.save()
        result.ok = True
        return result

    # ------------------------------------------------------------ internals
    def _station(self, ctx: BuildContext, number: int, **kwargs: Any) -> dict[str, Any]:
        station = self.stations[number]
        with ctx.stage(number, station.name) as record:
            output = station.run(ctx, **kwargs) or {}
            record.detail = {
                k: v for k, v in output.items() if isinstance(v, PRIMITIVES)
            }
        return output

    def _gate(self, ctx: BuildContext, number: int, result: BuildResult) -> GateReport:
        gate = self.gates[number]
        data = GateInput(
            subject=ctx.slug,
            paths={
                artifact.role: ctx.root / artifact.path
                for artifact in ctx.manifest.artifacts
            },
            facts=dict(ctx.manifest.facts),
        )
        try:
            report = gate.run(data)
        except GateFailure as exc:
            report = exc.report  # a missing artifact is itself a gate failure

        ctx.write_gate_report(gate.gate_id, report.as_dict())
        ctx.manifest.gates = [g for g in ctx.manifest.gates if g["gate_id"] != gate.gate_id]
        ctx.manifest.gates.append(report.as_dict())
        record_gate_result(self.config, report, ctx.slug)
        ctx.save()

        result.gate_reports.append(report)
        log.info("%s", report.to_text())
        report.enforce()
        return report

    def _write_stop_note(self, ctx: BuildContext, report: GateReport) -> None:
        note = "\n".join(
            [
                f"# Build stopped — {ctx.slug}",
                "",
                f"`{report.gate_id}` failed, so the build stopped here. This is the gate",
                "working, not the engine breaking.",
                "",
                "```",
                report.to_text(),
                "```",
                "",
                "## What to do",
                "",
                "1. Read the failing lines above — each names its own reason.",
                "2. Fix the *input*, not the gate: a niche with no evidence, a target page",
                "   count the content cannot fill, a book type whose pack is too small.",
                "3. Re-run. A gate you loosen to get a green build has stopped being a gate.",
                "",
            ]
        )
        (ctx.root / "STOPPED.md").write_text(note, encoding="utf-8")


def build(
    niche: Niche,
    config: EngineConfig | None = None,
    seed: int = 0,
    output_root: str | Path | None = None,
    stop_after: int = 6,
    options: dict[str, Any] | None = None,
    avoid: set[str] | None = None,
) -> BuildResult:
    """Convenience wrapper: one niche in, one build out."""
    return Pipeline(config).run(
        niche,
        seed=seed,
        output_root=output_root,
        stop_after=stop_after,
        options=options,
        avoid=avoid,
    )
