"""Station 6 — your review.

The engine's last act is to hand the work over. It writes a checklist with this
run's own numbers in it and a build report, and stops. Nothing here publishes
anything: "You read it, fix it, publish it. This one stays yours."
"""

from __future__ import annotations

from typing import Any

from ..run.context import BuildContext
from .base import Station


class ReviewStation(Station):
    number = 6
    name = "your review"

    def run(self, ctx: BuildContext, **_: Any) -> dict[str, Any]:
        facts = ctx.manifest.facts
        checklist = self._checklist(ctx, facts)
        ctx.write_text(6, "review_checklist.md", checklist, role="review_checklist")
        report = self._report(ctx, facts)
        path = ctx.root / "report.md"
        path.write_text(report, encoding="utf-8")
        ctx.register("run_report", path, station=6)
        return {"checklist": "written", "handed_over": True}

    # ----------------------------------------------------------- checklist
    def _checklist(self, ctx: BuildContext, facts: dict[str, Any]) -> str:
        pages = facts.get("page_count", "?")
        spine = facts.get("spine_width_in", "?")
        wrap = facts.get("cover_wrap_in", ["?", "?"])
        price = facts.get("price_usd", "?")
        royalty = facts.get("royalty_usd", "?")
        return "\n".join(
            [
                f"# Your review — {facts.get('title', ctx.slug)}",
                "",
                "The engine stops here. Everything below is yours to check with your own",
                "eyes; nothing in this list can be ticked by the machine that built the book.",
                "",
                "## Open the real files",
                "",
                f"- [ ] Open `{ctx.manifest.artifact('interior_pdf').path}` and page through it.",
                f"      {pages} pages. Does page 1 look like a book, or like a first draft?",
                f"- [ ] Open `{ctx.manifest.artifact('cover_pdf').path}` **at 100%**.",
                f"      Wrap {wrap[0]} x {wrap[1]} in, spine {spine} in for {pages} pages.",
                "- [ ] Open the `cover_proof` file and confirm nothing important sits outside",
                "      the safe area or under the barcode box. Never upload the proof file.",
                "",
                "## Read the listing as a stranger",
                "",
                f"- [ ] Read `{ctx.manifest.artifact('listing_readable').path}` cold.",
                "      Would you click this title in a list of twenty?",
                "- [ ] Check the 7 keywords: does any slot repeat what the title already says?",
                "- [ ] Check the 3 categories against the ones the top sellers actually sit in.",
                f"- [ ] Price ${price} → royalty ${royalty} per copy. Still the right number?",
                "",
                "## The honest questions",
                "",
                "- [ ] Is there anything in this book that exists only to reach the page count?",
                "- [ ] Would you give this to someone you know, at this price?",
                "- [ ] Did a gate fail on this run, and do you agree with why it passed?",
                "",
                "## Then, and only then",
                "",
                "- [ ] Run the upload station, fill the form, read every screen back.",
                "- [ ] Press Publish yourself.",
                "",
            ]
        )

    # -------------------------------------------------------------- report
    def _report(self, ctx: BuildContext, facts: dict[str, Any]) -> str:
        manifest = ctx.manifest
        lines = [
            f"# Build report — {manifest.slug}",
            "",
            f"- Book type: **{manifest.book_type}**",
            f"- Niche: {facts.get('niche', '—')}",
            f"- Seed: `{manifest.seed}` (same niche + seed = same book, byte for byte)",
            f"- Engine {manifest.engine_version}, KDP spec card {manifest.spec_version}",
            f"- Status: **{manifest.status}**",
            "",
            "## Stations",
            "",
            "| # | Station | Status | Seconds |",
            "| --- | --- | --- | --- |",
        ]
        for stage in manifest.stages:
            lines.append(
                f"| {stage.station} | {stage.name} | {stage.status} | {stage.duration_s or '—'} |"
            )
        lines += ["", "## Gates", "", "| Gate | Verdict | Failing checks |", "| --- | --- | --- |"]
        for gate in manifest.gates:
            verdict = "PASS" if gate["passed"] else "FAIL"
            failing = ", ".join(gate.get("failed_checks", [])) or "—"
            lines.append(f"| {gate['gate_id']} | {verdict} | {failing} |")
        lines += ["", "## The book that exists", ""]
        for key in (
            "title", "subtitle", "page_count", "planned_page_count", "trim_size",
            "paper", "spine_width_in", "cover_wrap_in", "content_units",
            "price_usd", "royalty_usd", "niche_score",
        ):
            if key in facts:
                lines.append(f"- **{key}**: {facts[key]}")
        lines += ["", "## Artifacts", "", "| Role | File | Bytes |", "| --- | --- | --- |"]
        for artifact in manifest.artifacts:
            lines.append(f"| {artifact.role} | `{artifact.path}` | {artifact.bytes:,} |")
        lines += [
            "",
            "## What is still yours",
            "",
            "Reading the PDF, looking at the cover at 100%, reading the listing as a",
            "stranger, and pressing Publish. The engine does not do these, by design.",
            "",
        ]
        return "\n".join(lines)
