"""Station 3 — cover build.

"Back, spine and front as one wrap, at the exact size the page count demands."

The page count used here is read from the interior PDF on disk, not from the
plan that produced it, because that is the file the printer will bind.
"""

from __future__ import annotations

from typing import Any

from ..booktypes.base import InteriorPlan
from ..content.copy import back_cover_copy
from ..content.locale import lookup, strings
from ..naming import artifact_name
from ..niche import Niche
from ..render.cover import CoverRenderer
from ..render.identity import choose_identity, identity_words
from ..render.pdfutil import pdf_page_count
from ..run.context import BuildContext
from ..spec.kdp import cover_geometry
from .base import Station


def badge_for(plan, language: str = "english") -> str:
    """The flash a shopper reads before the subtitle: what this book gives them.

    Worded by the locale table, so an Italian logbook says "90 GIORNI".
    """
    badge = lookup(strings(language), "cover.badge")
    meta = plan.metadata
    if plan.book_type == "journal" and meta.get("prompt_count"):
        return badge["journal"].format(count=meta["prompt_count"])
    if plan.book_type == "planner" and meta.get("weeks"):
        return badge["planner"].format(weeks=meta["weeks"])
    if plan.book_type == "puzzle" and meta.get("puzzle_count"):
        solutions = badge["puzzle_answers"] if meta.get("solutions") else ""
        return badge["puzzle"].format(count=meta["puzzle_count"], answers=solutions)
    if plan.book_type == "logbook" and meta.get("days"):
        return badge["logbook"].format(days=meta["days"])
    return badge["pages"].format(pages=plan.page_count)


class CoverStation(Station):
    number = 3
    name = "cover build"

    def run(
        self,
        ctx: BuildContext,
        niche: Niche | None = None,
        plan: InteriorPlan | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if niche is None or plan is None:
            raise ValueError("station 3 needs the niche and the interior plan")

        interior_pdf = ctx.path_of("interior_pdf")
        page_count = pdf_page_count(interior_pdf)
        geometry = cover_geometry(plan.trim_size, page_count, plan.paper)
        copy = back_cover_copy(plan, niche, self.config.brand)

        palette, motif = choose_identity(niche, plan, self.config, ctx.seed)
        renderer = CoverRenderer(
            geometry, plan.title, plan.subtitle, copy, self.config,
            palette=palette, motif=motif, seed=ctx.seed,
            art_style=(niche.options or {}).get("art_style"),
            composition=(niche.options or {}).get("composition"),
            badge=badge_for(plan, self.config.brand.language),
            words=identity_words(niche, plan),
        )
        cover_path = ctx.station_dir(3) / artifact_name(ctx.slug, "cover_wrap", "pdf")
        renderer.render(cover_path, guides=False)
        ctx.register("cover_pdf", cover_path, station=3)

        proof_path = ctx.station_dir(3) / artifact_name(ctx.slug, "cover_proof", "pdf")
        renderer.render(proof_path, guides=True)
        ctx.register("cover_proof_pdf", proof_path, station=3)

        spec = geometry.as_dict()
        spec["palette"] = palette.key
        spec["palette_label"] = palette.label
        spec["motif"] = motif
        spec["composition"] = renderer.composition
        spec["art_style"] = renderer.art_style
        spec["badge"] = renderer.badge
        spec["contrast"] = palette.contrast_report()
        # What a shopper's eye gets: the cover at search-results size.
        spec["legibility"] = renderer.legibility()
        spec["source_page_count_from"] = str(interior_pdf.name)
        spec["back_cover_copy"] = copy
        ctx.write_json(3, "cover_spec.json", spec, role="cover_spec")
        ctx.write_json(3, "back_cover_copy.json", copy, role="back_cover_copy")

        ctx.fact("cover_wrap_in", [round(geometry.width, 3), round(geometry.height, 3)])
        ctx.fact("spine_width_in", round(geometry.spine_width, 5))
        ctx.fact("cover_pixels_at_300dpi", list(geometry.pixel_size()))
        ctx.fact("composition", renderer.composition)
        ctx.fact("art_style", renderer.art_style)
        ctx.fact("title_cap_px_at_thumbnail", spec["legibility"].get("title_cap_px"))
        return {
            "wrap": [round(geometry.width, 3), round(geometry.height, 3)],
            "spine_in": round(geometry.spine_width, 4),
            "page_count": page_count,
        }
