"""Station 3 — cover build.

"Back, spine and front as one wrap, at the exact size the page count demands."

The page count used here is read from the interior PDF on disk, not from the
plan that produced it, because that is the file the printer will bind.
"""

from __future__ import annotations

from typing import Any

from ..booktypes.base import InteriorPlan
from ..content.copy import back_cover_copy
from ..naming import artifact_name
from ..niche import Niche
from ..render.cover import render_cover
from ..render.pdfutil import pdf_page_count
from ..run.context import BuildContext
from ..spec.kdp import cover_geometry
from .base import Station


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

        cover_path = ctx.station_dir(3) / artifact_name(ctx.slug, "cover_wrap", "pdf")
        render_cover(
            geometry, plan.title, plan.subtitle, copy, self.config, cover_path, guides=False
        )
        ctx.register("cover_pdf", cover_path, station=3)

        proof_path = ctx.station_dir(3) / artifact_name(ctx.slug, "cover_proof", "pdf")
        render_cover(
            geometry, plan.title, plan.subtitle, copy, self.config, proof_path, guides=True
        )
        ctx.register("cover_proof_pdf", proof_path, station=3)

        spec = geometry.as_dict()
        spec["source_page_count_from"] = str(interior_pdf.name)
        spec["back_cover_copy"] = copy
        ctx.write_json(3, "cover_spec.json", spec, role="cover_spec")
        ctx.write_json(3, "back_cover_copy.json", copy, role="back_cover_copy")

        ctx.fact("cover_wrap_in", [round(geometry.width, 3), round(geometry.height, 3)])
        ctx.fact("spine_width_in", round(geometry.spine_width, 5))
        ctx.fact("cover_pixels_at_300dpi", list(geometry.pixel_size()))
        return {
            "wrap": [round(geometry.width, 3), round(geometry.height, 3)],
            "spine_in": round(geometry.spine_width, 4),
            "page_count": page_count,
        }
