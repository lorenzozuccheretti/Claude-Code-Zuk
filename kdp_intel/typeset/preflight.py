"""Check the PDF that exists, not the one we meant to make.

KDP rejects interiors for a wrong page size, an odd or out-of-range page
count, and fonts that are not embedded. Margins are checked against the
spec for the page count the file really has.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from kdp_factory.spec import kdp as spec

from .common import Geometry

PT = 72.0
TOLERANCE_PT = 0.5


@dataclass
class Preflight:
    pages: int
    size_in: tuple[float, float]
    problems: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {"ok": self.ok, "pages": self.pages, "size_in": list(self.size_in),
                "fonts": self.fonts, "problems": self.problems}


def pad_to_even(pdf: Path) -> int:
    """Append one blank page if the count is odd. Returns the final count."""
    reader = PdfReader(str(pdf))
    count = len(reader.pages)
    if count % 2 == 0:
        return count
    writer = PdfWriter(clone_from=reader)
    box = reader.pages[0].mediabox
    writer.add_blank_page(width=float(box.width), height=float(box.height))
    with pdf.open("wb") as fh:
        writer.write(fh)
    return count + 1


def _fonts(reader: PdfReader) -> tuple[list[str], list[str]]:
    names, unembedded = set(), set()
    for page in reader.pages:
        fonts = (page.get("/Resources") or {}).get("/Font") or {}
        for ref in fonts.values():
            font = ref.get_object()
            name = str(font.get("/BaseFont", "?"))
            names.add(name)
            descriptors = [font.get("/FontDescriptor")]
            for d in font.get("/DescendantFonts") or []:
                descriptors.append(d.get_object().get("/FontDescriptor"))
            embedded = any(
                d is not None and any(k in d.get_object() for k in ("/FontFile", "/FontFile2", "/FontFile3"))
                for d in descriptors
            )
            if not embedded and font.get("/Subtype") != "/Type3":
                unembedded.add(name)
    return sorted(names), sorted(unembedded)


def preflight(pdf: Path, trim: str, paper: str, geo: Geometry) -> Preflight:
    reader = PdfReader(str(pdf))
    pages = len(reader.pages)
    box = reader.pages[0].mediabox
    size = (round(float(box.width) / PT, 3), round(float(box.height) / PT, 3))
    report = Preflight(pages=pages, size_in=size)
    t = spec.trim_size(trim)

    for i, page in enumerate(reader.pages, 1):
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        if abs(w - t.width_pt) > TOLERANCE_PT or abs(h - t.height_pt) > TOLERANCE_PT:
            report.problems.append(f"page {i} is {w / PT:.3f}x{h / PT:.3f} in, trim is {t.name}")
            break
    try:
        spec.validate_page_count(pages, paper)
    except Exception as exc:  # noqa: BLE001 - SpecViolation carries the reason
        report.problems.append(str(exc))
    else:
        needed = spec.gutter_margin_in(pages)
        if geo.inside + 1e-9 < needed:
            report.problems.append(f"inside margin {geo.inside} in < KDP gutter {needed} in for {pages} pages")
    if geo.outside + 1e-9 < spec.outside_margin_in(bleed=False):
        report.problems.append(f"outside margin {geo.outside} in below the KDP minimum")
    report.fonts, unembedded = _fonts(reader)
    if unembedded:
        report.problems.append(f"fonts not embedded: {', '.join(unembedded)}")
    return report
