"""Gate 3 — print readiness.

Runs on the files, after the cover exists. It re-measures instead of trusting:
the page count comes from the PDF, the spine width is recomputed from that page
count, and the cover's own dimensions are measured and compared.

This is the gate that catches the classic KDP rejection: a cover built for the
page count you meant to produce rather than the one you did.
"""

from __future__ import annotations

from ..errors import SpecViolation
from ..render.pdfutil import pdf_page_count, pdf_page_sizes_in
from ..spec.kdp import (
    KDP_SPEC,
    cover_geometry,
    gutter_margin_in,
    spine_width_in,
    trim_size,
    validate_page_count,
)
from .base import Gate, GateInput, GateReport

TOLERANCE_IN = 0.01


class PrintReadyGate(Gate):
    gate_id = "g3_print"
    title = "Will KDP actually print this?"
    requires = ("interior_pdf", "interior_plan")

    def evaluate(self, data: GateInput, report: GateReport) -> None:
        interior = data.path("interior_pdf")
        plan = data.json("interior_plan")
        paper = plan["paper"]
        trim_name = plan["trim_size"]

        real_pages = pdf_page_count(interior)
        report.metrics["page_count"] = real_pages

        report.add(
            "page_count_matches_plan",
            real_pages == plan["page_count"],
            f"the PDF has {real_pages} pages; the plan said {plan['page_count']}",
            {"pdf": real_pages, "plan": plan["page_count"]},
        )

        try:
            validate_page_count(real_pages, paper)
            printable, reason = True, f"{real_pages} pages on {paper} is inside KDP's limits"
        except SpecViolation as exc:
            printable, reason = False, str(exc)
        report.add("page_count_printable", printable, reason)

        target = plan.get("metadata", {}).get("target_pages")
        if target:
            report.add(
                "landed_on_target",
                real_pages == int(target),
                f"page count landed on {real_pages} against a target of {target}"
                + ("" if real_pages == int(target) else " — on purpose, or by accident?"),
                {"target": target, "actual": real_pages},
            )

        trim = trim_size(trim_name)
        sizes = set(pdf_page_sizes_in(interior))
        expected = (round(trim.width, 3), round(trim.height, 3))
        report.add(
            "interior_trim_size",
            sizes == {expected},
            f"every interior page measures {expected[0]} x {expected[1]} in"
            if sizes == {expected}
            else f"expected every page at {expected}, found {sorted(sizes)}",
            {"expected": list(expected), "found": [list(s) for s in sorted(sizes)]},
        )

        gutter = gutter_margin_in(real_pages)
        report.add(
            "gutter_band",
            abs(plan.get("gutter_margin_in", gutter) - gutter) < 1e-9
            if "gutter_margin_in" in plan
            else True,
            f"a {real_pages}-page book needs a {gutter}\" inside margin",
            {"gutter_margin_in": gutter},
            advisory=True,
        )

        self._cover_checks(data, report, real_pages, paper, trim_name)

    def _cover_checks(
        self, data: GateInput, report: GateReport, real_pages: int, paper: str, trim_name: str
    ) -> None:
        if "cover_pdf" not in data.paths:
            report.add(
                "cover_present",
                False,
                "no cover was produced, so this book cannot be uploaded",
                advisory=True,
            )
            return

        cover = data.path("cover_pdf")
        geometry = cover_geometry(trim_name, real_pages, paper)
        spine = spine_width_in(real_pages, paper)
        report.metrics["spine_width_in"] = round(spine, 5)
        report.metrics["wrap_in"] = [round(geometry.width, 3), round(geometry.height, 3)]

        sizes = pdf_page_sizes_in(cover)
        report.add(
            "cover_is_one_page",
            len(sizes) == 1,
            f"the cover wrap is {len(sizes)} page(s); KDP takes exactly one",
        )
        if not sizes:
            return

        width, height = sizes[0]
        width_ok = abs(width - geometry.width) <= TOLERANCE_IN
        height_ok = abs(height - geometry.height) <= TOLERANCE_IN
        report.add(
            "cover_size_matches_page_count",
            width_ok and height_ok,
            f"cover measures {width} x {height} in; {real_pages} pages on {paper} "
            f"require {geometry.width:.3f} x {geometry.height:.3f} in "
            f"(spine {spine:.4f} in)",
            {
                "measured": [width, height],
                "required": [round(geometry.width, 3), round(geometry.height, 3)],
                "spine_width_in": round(spine, 5),
                "tolerance_in": TOLERANCE_IN,
            },
        )

        recorded = data.facts.get("spine_width_in")
        if recorded is not None:
            report.add(
                "spine_recomputed_from_real_file",
                abs(float(recorded) - spine) <= 0.0005,
                f"the spine on record ({float(recorded):.4f} in) matches the one "
                f"recomputed from the {real_pages}-page PDF ({spine:.4f} in)",
            )

        spine_minimum = int(KDP_SPEC["cover"]["spine_text_min_pages"])
        spine_text_note = (
            "allowed"
            if geometry.spine_text_allowed
            else f"not allowed by KDP below {spine_minimum} pages"
        )
        report.add(
            "spine_text_allowed",
            True,
            f"{real_pages} pages: spine text is {spine_text_note}",
            {"spine_text_allowed": geometry.spine_text_allowed, "minimum_pages": spine_minimum},
            advisory=True,
        )
