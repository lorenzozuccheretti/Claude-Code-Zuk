"""Station 1 — niche selection.

Choose what to publish, scored against real demand, before a page exists. The
station does not decide whether the niche is good enough: it writes the score
down, and gate 1 decides.
"""

from __future__ import annotations

from typing import Any

from ..niche import Niche
from ..run.context import BuildContext
from .base import Station


class NicheStation(Station):
    number = 1
    name = "niche selection"

    def run(self, ctx: BuildContext, niche: Niche | None = None, **_: Any) -> dict[str, Any]:
        if niche is None:
            raise ValueError("station 1 needs a niche")
        score = niche.score()

        ctx.write_json(1, "niche.json", niche.as_dict(), role="niche")
        ctx.write_json(1, "niche_score.json", score.as_dict(), role="niche_score")
        ctx.write_text(1, "niche_report.md", self._report(niche, score), role="niche_report")

        ctx.fact("niche", niche.niche)
        ctx.fact("audience", niche.audience)
        ctx.fact("promise", niche.promise)
        ctx.fact("niche_score", round(score.total, 3))
        ctx.fact("trim_size", niche.constraints.get("trim_size", self.config.brand.default_trim))
        ctx.fact("paper", niche.constraints.get("paper", self.config.brand.default_paper))
        return {"score": score.total, "signals": len(niche.signals)}

    def _report(self, niche: Niche, score) -> str:
        lines = [
            f"# Niche — {niche.niche}",
            "",
            f"- **Book type**: {niche.book_type}",
            f"- **Audience**: {niche.audience or '_not named_'}",
            f"- **Promise**: {niche.promise or '_not named_'}",
            f"- **Weighted score**: {score.total:.2f} / 10",
            "",
            "## Scoring sheet",
            "",
            "```",
            score.table(),
            "```",
            "",
            "## Evidence",
            "",
        ]
        for key, signal in niche.signals.items():
            evidence = signal.evidence.strip() or "_no evidence recorded — this is a hunch_"
            source = f" ({signal.source})" if signal.source else ""
            lines.append(f"- **{key}** — {signal.score:.1f}: {evidence}{source}")
        lines += [
            "",
            "## Incumbent",
            "",
            f"- Title: {niche.competitor.title or '_none recorded_'}",
            f"- ASIN: {niche.competitor.asin or '—'}",
            f"- Price: {niche.competitor.price if niche.competitor.price is not None else '—'}",
            f"- Reviews: {niche.competitor.reviews if niche.competitor.reviews is not None else '—'}"
            f" (rating {niche.competitor.rating if niche.competitor.rating is not None else '—'})",
            "",
            "### Named weaknesses",
            "",
        ]
        lines += [f"- {gap}" for gap in niche.competitor.gaps] or ["- _none recorded_"]
        lines += [
            "",
            "## Trademark",
            "",
            f"- Checked: {'yes' if niche.trademark.checked else 'NO'}"
            + (f" via {niche.trademark.source}" if niche.trademark.source else ""),
            f"- Hits: {', '.join(niche.trademark.hits) if niche.trademark.hits else 'none'}",
            "",
        ]
        return "\n".join(lines)
