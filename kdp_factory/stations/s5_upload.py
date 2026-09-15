"""Station 5 — the upload run.

"It opens the browser, fills the KDP form — and stops."

The station itself only writes the plan; the browser work is in
``kdp_factory.upload.playwright_driver`` and is never triggered by a build. A
build that silently opened a browser would be a build you could not run fifty
times unattended.
"""

from __future__ import annotations

from typing import Any

from ..run.context import BuildContext
from ..upload.plan import build_upload_plan
from .base import Station


class UploadStation(Station):
    number = 5
    name = "upload run (prepared, not executed)"

    def run(self, ctx: BuildContext, **_: Any) -> dict[str, Any]:
        listing = ctx.read_json("listing")
        manuscript = ctx.path_of("interior_pdf")
        cover = ctx.path_of("cover_pdf")

        plan = build_upload_plan(
            slug=ctx.slug,
            listing=listing,
            manuscript=manuscript.resolve(),
            cover=cover.resolve(),
        )
        ctx.write_json(5, "upload_plan.json", plan.as_dict(), role="upload_plan")
        ctx.write_text(5, "upload_run.md", plan.to_markdown(), role="upload_readable")
        ctx.fact("upload_prepared", True)
        return {
            "fields": len(plan.fields),
            "files": len(plan.files),
            "stops_before": plan.stops_before,
        }
