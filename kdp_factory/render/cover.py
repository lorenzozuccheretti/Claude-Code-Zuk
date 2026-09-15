"""Station 3's renderer: the full wrap — back, spine and front as one PDF.

Nothing here decides a size. The geometry comes from ``spec.kdp.cover_geometry``
for the page count of the interior file that actually exists, which is the
difference between a cover that fits and a rejected upload.

Two files come out of a run: the cover itself, and a proof with the trim lines,
safe area and barcode keep-out drawn on top — the proof is for you, never for
upload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab import rl_config
from reportlab.lib.colors import Color, HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdfcanvas

from ..config import EngineConfig
from ..spec.kdp import INCH, CoverGeometry


def _hex(value: str) -> Color:
    return HexColor(value)


class CoverRenderer:
    def __init__(
        self,
        geometry: CoverGeometry,
        title: str,
        subtitle: str,
        back_copy: dict[str, Any],
        config: EngineConfig,
    ) -> None:
        self.geo = geometry
        self.title = title
        self.subtitle = subtitle
        self.back_copy = back_copy
        self.config = config
        self.brand = config.brand
        self.palette = config.brand.palette

    def font(self, role: str, weight: str = "regular") -> str:
        return self.brand.fonts.resolve(role, weight)

    # ------------------------------------------------------------- helpers
    def _wrap(self, text: str, font: str, size: float, width: float) -> list[str]:
        words = text.split()
        if not words:
            return []
        lines, current = [], words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if pdfmetrics.stringWidth(candidate, font, size) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _fit_font_size(
        self, text: str, font: str, width: float, start: float, minimum: float, max_lines: int
    ) -> float:
        """Largest size at which the text still fits in ``max_lines``."""
        size = start
        while size > minimum and len(self._wrap(text, font, size, width)) > max_lines:
            size -= 0.5
        return size

    def _block(
        self,
        c: pdfcanvas.Canvas,
        text: str,
        x: float,
        y: float,
        width: float,
        font: str,
        size: float,
        leading: float,
        color: str,
        centred: bool = False,
    ) -> float:
        c.setFont(font, size)
        c.setFillColor(_hex(color))
        for line in self._wrap(text, font, size, width):
            if centred:
                c.drawCentredString(x + width / 2, y, line)
            else:
                c.drawString(x, y, line)
            y -= leading
        return y

    # -------------------------------------------------------------- panels
    def _background(self, c: pdfcanvas.Canvas) -> None:
        c.setFillColor(_hex(self.palette.primary))
        c.rect(0, 0, self.geo.width_pt, self.geo.height_pt, stroke=0, fill=1)
        # A lighter front panel, so the spine reads as a spine.
        c.setFillColor(_hex(self.palette.secondary))
        c.rect(
            self.geo.front_panel_x * INCH,
            0,
            (self.geo.trim.width + self.geo.bleed) * INCH,
            self.geo.height_pt,
            stroke=0,
            fill=1,
        )

    def _front_panel(self, c: pdfcanvas.Canvas) -> None:
        safe = self.geo.safe_margin
        x = (self.geo.front_panel_x + safe) * INCH
        width = (self.geo.trim.width - 2 * safe) * INCH
        top = (self.geo.height - self.geo.bleed - safe) * INCH

        title_font = self.font("display", "bold")
        size = self._fit_font_size(self.title, title_font, width, 44, 20, 3)
        y = top - self.geo.trim.height * INCH * 0.18
        y = self._block(
            c, self.title, x, y, width, title_font, size, size * 1.12,
            self.palette.primary, centred=True,
        )

        y -= size * 0.4
        c.setStrokeColor(_hex(self.palette.accent))
        c.setLineWidth(2)
        c.line(x + width * 0.25, y, x + width * 0.75, y)
        y -= 26

        if self.subtitle:
            sub_font = self.font("body")
            sub_size = self._fit_font_size(self.subtitle, sub_font, width, 15, 9, 4)
            self._block(
                c, self.subtitle, x, y, width, sub_font, sub_size, sub_size * 1.4,
                self.palette.ink, centred=True,
            )

        c.setFont(self.font("accent", "bold"), 13)
        c.setFillColor(_hex(self.palette.primary))
        c.drawCentredString(
            x + width / 2, (self.geo.bleed + safe + 0.25) * INCH, self.brand.author
        )

    def _spine(self, c: pdfcanvas.Canvas) -> None:
        if not self.geo.spine_text_allowed:
            return
        spine_centre = (self.geo.spine_x + self.geo.spine_width / 2) * INCH
        c.saveState()
        c.translate(spine_centre, self.geo.height_pt / 2)
        c.rotate(90)
        available = (self.geo.trim.height - 1.0) * INCH
        font = self.font("display", "bold")
        size = min(self.geo.spine_width_pt * 0.55, 14)
        text = self.title
        while pdfmetrics.stringWidth(text, font, size) > available and size > 6:
            size -= 0.5
        c.setFont(font, size)
        c.setFillColor(_hex(self.palette.secondary))
        c.drawCentredString(0, -size * 0.35, text)
        c.restoreState()

    def _back_panel(self, c: pdfcanvas.Canvas) -> None:
        safe = self.geo.safe_margin
        x = (self.geo.back_panel_x + safe) * INCH
        width = (self.geo.trim.width - 2 * safe) * INCH
        top = (self.geo.height - self.geo.bleed - safe) * INCH
        y = top - 24

        y = self._block(
            c, self.back_copy.get("hook", ""), x, y, width,
            self.font("display", "bold"), 16, 20, self.palette.secondary,
        )
        y -= 14
        y = self._block(
            c, self.back_copy.get("body", ""), x, y, width,
            self.font("body"), 10.5, 14.5, self.palette.secondary,
        )
        y -= 12
        for benefit in self.back_copy.get("benefits", []):
            c.setFont(self.font("accent", "bold"), 10)
            c.setFillColor(_hex(self.palette.accent))
            c.drawString(x, y, "›")
            y = self._block(
                c, benefit, x + 12, y, width - 12, self.font("body"), 10, 13.5,
                self.palette.secondary,
            )
            y -= 6

        closing = self.back_copy.get("closing", "")
        barcode_top = self._barcode_box_top()
        if closing and y > barcode_top + 16:
            self._block(
                c, closing, x, barcode_top + 18, width, self.font("body", "italic"),
                10, 13, self.palette.accent,
            )

        self._barcode_keepout(c)

    def _barcode_box(self) -> tuple[float, float, float, float]:
        """(x, y, w, h) in points of the area KDP prints the barcode over."""
        from ..spec.kdp import KDP_SPEC

        offsets = KDP_SPEC["cover"]["barcode_offset_in"]
        width, height = self.geo.barcode_keepout
        # Bottom-right of the back panel: right edge sits against the spine.
        x = (self.geo.spine_x - float(offsets["from_spine_edge"]) - width) * INCH
        y = (self.geo.bleed + float(offsets["from_bottom_trim"])) * INCH
        return x, y, width * INCH, height * INCH

    def _barcode_box_top(self) -> float:
        _, y, _, height = self._barcode_box()
        return y + height

    def _barcode_keepout(self, c: pdfcanvas.Canvas) -> None:
        x, y, width, height = self._barcode_box()
        c.setFillColor(_hex("#FFFFFF"))
        c.rect(x, y, width, height, stroke=0, fill=1)

    # -------------------------------------------------------------- guides
    def _guides(self, c: pdfcanvas.Canvas) -> None:
        bleed = self.geo.bleed * INCH
        safe = self.geo.safe_margin * INCH
        c.setLineWidth(0.6)

        c.setStrokeColor(_hex("#FF0055"))
        c.rect(
            bleed, bleed,
            self.geo.width_pt - 2 * bleed,
            self.geo.height_pt - 2 * bleed,
            stroke=1, fill=0,
        )
        for x in (self.geo.spine_x * INCH, self.geo.front_panel_x * INCH):
            c.line(x, 0, x, self.geo.height_pt)

        c.setStrokeColor(_hex("#00AAFF"))
        c.setDash(3, 3)
        for panel_x in (self.geo.back_panel_x, self.geo.front_panel_x):
            c.rect(
                (panel_x + self.geo.safe_margin) * INCH,
                bleed + safe,
                (self.geo.trim.width - 2 * self.geo.safe_margin) * INCH,
                self.geo.height_pt - 2 * (bleed + safe),
                stroke=1, fill=0,
            )
        c.setDash()

        x, y, width, height = self._barcode_box()
        c.setStrokeColor(_hex("#FF0055"))
        c.rect(x, y, width, height, stroke=1, fill=0)
        c.setFont("Helvetica", 7)
        c.setFillColor(_hex("#FF0055"))
        c.drawString(x + 3, y + 4, "barcode keep-out 2.0 x 1.2 in")
        c.drawString(
            bleed + 4,
            self.geo.height_pt - bleed - 10,
            f"PROOF — wrap {self.geo.width:.3f} x {self.geo.height:.3f} in, "
            f"spine {self.geo.spine_width:.3f} in for {self.geo.page_count} pages "
            f"({self.geo.paper}). Do not upload this file.",
        )

    # -------------------------------------------------------------- output
    def render(self, path: str | Path, guides: bool = False) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        previous = rl_config.invariant
        rl_config.invariant = 1
        try:
            c = pdfcanvas.Canvas(
                str(out),
                pagesize=(self.geo.width_pt, self.geo.height_pt),
                invariant=1,
                pageCompression=1,
            )
            c.setTitle(f"{self.title} — cover wrap")
            c.setAuthor(self.brand.author)
            c.setCreator("kdp-factory")
            self._background(c)
            self._back_panel(c)
            self._spine(c)
            self._front_panel(c)
            if guides:
                self._guides(c)
            c.showPage()
            c.save()
        finally:
            rl_config.invariant = previous
        return out


def render_cover(
    geometry: CoverGeometry,
    title: str,
    subtitle: str,
    back_copy: dict[str, Any],
    config: EngineConfig,
    path: str | Path,
    guides: bool = False,
) -> Path:
    return CoverRenderer(geometry, title, subtitle, back_copy, config).render(path, guides)
