"""Station 2 — interior build.

Plan first, render second, and write both down. The plan is a real artifact:
gate 3 compares it against the PDF, and a mismatch between what was planned and
what exists is a defect worth stopping for.
"""

from __future__ import annotations

from typing import Any

from ..booktypes import get_book_type
from ..booktypes.base import InteriorPlan
from ..naming import artifact_name
from ..niche import Niche
from ..render.interior import render_interior
from ..render.pdfutil import pdf_page_count
from ..run.context import BuildContext
from ..spec.kdp import gutter_margin_in, spine_width_in
from .base import Station


class InteriorStation(Station):
    number = 2
    name = "interior build"

    def run(
        self,
        ctx: BuildContext,
        niche: Niche | None = None,
        options: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if niche is None:
            raise ValueError("station 2 needs a niche")
        book_type = get_book_type(niche.book_type)
        plan: InteriorPlan = book_type.plan(
            niche, self.config, ctx.rng("interior"), options=options
        )

        ctx.write_json(2, "interior_plan.json", plan.as_dict(), role="interior_plan")
        pdf_path = ctx.station_dir(2) / artifact_name(ctx.slug, "interior", "pdf")
        render_interior(plan, self.config, pdf_path)
        ctx.register("interior_pdf", pdf_path, station=2)

        # The page count that matters from here on is the file's, not the plan's.
        real_pages = pdf_page_count(pdf_path)
        ctx.fact("title", plan.title)
        ctx.fact("subtitle", plan.subtitle)
        ctx.fact("planned_page_count", plan.page_count)
        ctx.fact("page_count", real_pages)
        ctx.fact("trim_size", plan.trim_size)
        ctx.fact("paper", plan.paper)
        ctx.fact("gutter_margin_in", gutter_margin_in(real_pages))
        ctx.fact("spine_width_in", round(spine_width_in(real_pages, plan.paper), 5))
        ctx.fact("content_units", len(plan.content_units))
        ctx.fact("book_type_metadata", {
            k: v for k, v in plan.metadata.items() if k != "puzzles"
        })

        # Format-specific verification the book type alone can do (e.g. every
        # puzzle word really is in its grid). Recorded here, enforced by gate 2.
        checks = book_type.verify(plan)
        ctx.write_json(
            2,
            "book_type_checks.json",
            [{"name": n, "passed": p, "reason": r} for n, p, r in checks],
            role="book_type_checks",
        )
        return {
            # The plan object travels to stations 3 and 4 in memory; the gates
            # deliberately do not get it, and read interior_plan.json instead.
            "plan": plan,
            "page_count": real_pages,
            "content_units": len(plan.content_units),
            "title": plan.title,
        }
