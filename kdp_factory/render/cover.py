"""Station 3's renderer: the full wrap — back, spine and front as one PDF.

Nothing here decides a size. The geometry comes from ``spec.kdp.cover_geometry``
for the page count of the interior file that actually exists, which is the
difference between a cover that fits and a rejected upload.

Everything else here is design, and it is the part that sells the book. A cover
is judged at the size of a thumbnail in a list of twenty, so: one colour world
(``design.Palette``), one drawn mark (``design.draw_motif``), a title set in a
face chosen for size, and nothing else competing with it.

Two files come out of a run: the cover itself, and a proof with the trim lines,
safe area and barcode keep-out drawn on top — the proof is for you, never for
upload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab import rl_config
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdfcanvas

from ..config import EngineConfig
from ..content.rng import StageRandom
from ..spec.kdp import INCH, CoverGeometry
from .design import (
    MotifSpec,
    Palette,
    choose_motif,
    choose_palette,
    draw_motif,
    gradient_ground,
    hex_color,
    small_caps,
)
from .typography import font


class CoverRenderer:
    def __init__(
        self,
        geometry: CoverGeometry,
        title: str,
        subtitle: str,
        back_copy: dict[str, Any],
        config: EngineConfig,
        palette: Palette | None = None,
        motif: str | None = None,
        seed: int = 0,
    ) -> None:
        self.geo = geometry
        self.title = title
        self.subtitle = subtitle
        self.back_copy = back_copy
        self.config = config
        self.brand = config.brand
        rng = StageRandom(seed, "cover")
        self.palette = palette or choose_palette([title, subtitle], rng)
        self.motif = motif or choose_motif([title, subtitle], rng)
        self.seed = seed
        # Every text extent drawn, so the renderer can be held to its own frames.
        self.text_extents: list[tuple[float, float]] = []

    # ------------------------------------------------------------- helpers
    def _note_extent(self, left: float, right: float) -> None:
        self.text_extents.append((left, right))

    def safe_box(self) -> tuple[float, float]:
        """Left and right bounds no text may cross, in points across the wrap."""
        geo = self.geo
        return ((geo.bleed + geo.safe_margin) * INCH,
                (geo.width - geo.bleed - geo.safe_margin) * INCH)

    def _wrap(self, text: str, face: str, size: float, width: float) -> list[str]:
        words = text.split()
        if not words:
            return []
        lines, current = [], words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if pdfmetrics.stringWidth(candidate, face, size) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _fit(self, text: str, face: str, width: float, start: float,
             minimum: float, max_lines: int) -> float:
        """Largest size at which the text still fits in ``max_lines``."""
        size = start
        while size > minimum and len(self._wrap(text, face, size, width)) > max_lines:
            size -= 0.5
        return size

    def _balanced(self, text: str, face: str, size: float, width: float) -> list[str]:
        """Wrap into even lines rather than filling each one to the edge.

        Greedy wrapping leaves a long first line and a stub last one. Narrowing
        the measure until the line count would grow finds the tightest width
        that keeps the same number of lines, which is what balances them.
        """
        lines = self._wrap(text, face, size, width)
        if len(lines) < 2:
            return lines
        target = len(lines)
        low, high = max(w for w in (pdfmetrics.stringWidth(word, face, size)
                                    for word in text.split())), width
        while high - low > 1.0:
            middle = (low + high) / 2
            if len(self._wrap(text, face, size, middle)) <= target:
                high = middle
            else:
                low = middle
        return self._wrap(text, face, size, high)

    def _block(self, c, text: str, x: float, y: float, width: float, face: str,
               size: float, leading: float, color: str, centred: bool = True,
               balanced: bool = False) -> float:
        c.setFont(face, size)
        c.setFillColor(hex_color(color))
        lines = self._balanced(text, face, size, width) if balanced else \
            self._wrap(text, face, size, width)
        for line in lines:
            line_width = pdfmetrics.stringWidth(line, face, size)
            left = x + (width - line_width) / 2 if centred else x
            self._note_extent(left, left + line_width)
            if centred:
                c.drawCentredString(x + width / 2, y, line)
            else:
                c.drawString(x, y, line)
            y -= leading
        return y

    def _tracked(self, c, text: str, x: float, y: float, face: str, size: float,
                 color: str, tracking: float = 1.6, centred: bool = True) -> None:
        """Letterspaced caps — the label voice on this cover.

        Tracking lives on a text object, not the canvas, so this measures the
        tracked width itself to centre it.
        """
        if not text:
            return
        caps = text.upper()
        start = x - self._tracked_width(caps, face, size, tracking) / 2 if centred else x
        # Character spacing is part of the PDF text state and survives BT/ET, so
        # without this save/restore every later string is drawn wider than
        # ReportLab measures it — which is how a title ends up over the trim.
        c.saveState()
        obj = c.beginText()
        obj.setFont(face, size)
        obj.setFillColor(hex_color(color))
        obj.setCharSpace(tracking)
        obj.setTextOrigin(start, y)
        obj.textOut(caps)
        c.drawText(obj)
        c.restoreState()
        self._note_extent(start, start + self._tracked_width(caps, face, size, tracking))

    def _tracked_width(self, text: str, face: str, size: float, tracking: float) -> float:
        caps = text.upper()
        return pdfmetrics.stringWidth(caps, face, size) + tracking * max(len(caps) - 1, 0)

    # -------------------------------------------------------------- panels
    def _front_panel(self, c: pdfcanvas.Canvas) -> None:
        geo, pal = self.geo, self.palette
        panel_x = geo.front_panel_x * INCH
        panel_w = (geo.trim.width + geo.bleed) * INCH

        gradient_ground(c, panel_x, 0, panel_w, geo.height_pt, pal.panel, pal.panel_deep)

        safe = geo.safe_margin
        x = (geo.front_panel_x + safe) * INCH
        width = (geo.trim.width - 2 * safe) * INCH
        top = (geo.height - geo.bleed - safe) * INCH
        bottom = (geo.bleed + safe) * INCH

        self._draw_front_motif(c, x, bottom, width, top - bottom)

        # The title sits in the upper third: that is the part of a cover that
        # survives being shrunk to a thumbnail.
        display = font("display", "bold")
        size = self._fit(self.title, display, width, 56, 24, 3)
        cursor = top - geo.trim.height * INCH * 0.16
        cursor = self._block(c, self.title, x, cursor, width, display, size,
                             size * 1.04, pal.title_ink, balanced=True)

        cursor -= size * 0.32
        c.setStrokeColor(hex_color(pal.accent))
        c.setLineWidth(1.1)
        c.line(x + width * 0.36, cursor, x + width * 0.64, cursor)
        cursor -= 26

        if self.subtitle:
            text_face = font("text", "regular")
            sub_size = self._fit(self.subtitle, text_face, width * 0.92, 13.5, 9, 4)
            self._block(c, self.subtitle, x + width * 0.04, cursor, width * 0.92,
                        text_face, sub_size, sub_size * 1.55, pal.panel_ink, balanced=True)

        author = self.brand.author.strip()
        if author:
            self._tracked(c, author, x + width / 2, bottom + 4,
                          font("sans", "bold"), 9.5, pal.title_ink, 2.2)

    def _draw_front_motif(self, c, x: float, y: float, width: float, height: float) -> None:
        """Place the mark where it supports the type instead of fighting it.

        Clipped to the front panel: a motif is allowed to run off the trim, but
        never onto the spine or the back cover.
        """
        pal = self.palette
        panel_x = self.geo.front_panel_x * INCH
        c.saveState()
        clip = c.beginPath()
        clip.rect(panel_x, 0, self.geo.width_pt - panel_x, self.geo.height_pt)
        c.clipPath(clip, stroke=0, fill=0)
        spec = MotifSpec(x=x, y=y, width=width, height=height,
                         color=pal.accent, alpha=0.20, seed=self.seed, weight=1.2)
        if self.motif == "arc":
            spec.y = y + height * 0.30
            spec.alpha = 0.16
        elif self.motif in ("stems", "rays"):
            spec.height = height * 0.42
        elif self.motif == "waves":
            spec.y = y + height * 0.04
            spec.height = height * 0.26
            spec.alpha = 0.18
        elif self.motif == "dots":
            spec.alpha = 0.22
            spec.extras = {"step": 15.0, "radius": 1.0}
        elif self.motif == "rule":
            spec.y = y + height * 0.46
            spec.alpha = 0.5
        draw_motif(c, self.motif, spec)
        c.restoreState()

    def _spine(self, c: pdfcanvas.Canvas) -> None:
        geo, pal = self.geo, self.palette
        gradient_ground(c, geo.spine_x * INCH, 0, geo.spine_width_pt, geo.height_pt,
                        pal.ground, pal.ground_deep)
        if not geo.spine_text_allowed:
            return
        centre = (geo.spine_x + geo.spine_width / 2) * INCH
        c.saveState()
        c.translate(centre, geo.height_pt / 2)
        c.rotate(90)
        available = (geo.trim.height - 1.4) * INCH
        face = font("display", "bold")
        size = min(geo.spine_width_pt * 0.52, 15)
        text = self.title
        while pdfmetrics.stringWidth(text, face, size) > available and size > 6:
            size -= 0.5
        c.setFont(face, size)
        c.setFillColor(hex_color(pal.ground_ink))
        c.drawCentredString(-available * 0.06, -size * 0.34, text)
        imprint = self.brand.imprint.strip()
        if imprint and geo.spine_width_pt >= 36:
            self._tracked(c, imprint, available * 0.40, -size * 0.34,
                          font("sans", "regular"), min(size * 0.5, 7.5),
                          pal.ground_ink_soft, 1.2)
        c.restoreState()

    def _back_panel(self, c: pdfcanvas.Canvas) -> None:
        geo, pal = self.geo, self.palette
        gradient_ground(c, 0, 0, (geo.trim.width + geo.bleed) * INCH, geo.height_pt,
                        pal.ground, pal.ground_deep)

        back_w = (geo.trim.width + geo.bleed) * INCH
        c.saveState()
        clip = c.beginPath()
        clip.rect(0, 0, back_w, geo.height_pt)
        c.clipPath(clip, stroke=0, fill=0)
        draw_motif(c, self.motif, MotifSpec(
            x=-back_w * 0.15, y=geo.height_pt * 0.10, width=back_w * 1.3,
            height=geo.height_pt * 0.34, color=pal.accent, alpha=0.10,
            seed=self.seed, weight=1.0,
            extras={"step": 18.0, "radius": 1.0}))
        c.restoreState()

        safe = geo.safe_margin
        x = (geo.back_panel_x + safe + 0.12) * INCH
        width = (geo.trim.width - 2 * safe - 0.24) * INCH
        top = (geo.height - geo.bleed - safe) * INCH
        y = top - 34

        self._tracked(c, self.brand.imprint or "", x, y + 16,
                      font("sans", "regular"), 7.5, pal.ground_ink_soft, 2.0, centred=False)

        hook = self.back_copy.get("hook", "")
        if hook:
            face = font("display", "bold")
            size = self._fit(hook, face, width, 21, 13, 3)
            y = self._block(c, hook, x, y, width, face, size, size * 1.18,
                            pal.ground_ink, centred=False, balanced=True)
            y -= 12

        c.setStrokeColor(hex_color(pal.accent, 0.8))
        c.setLineWidth(0.9)
        c.line(x, y + 4, x + width * 0.22, y + 4)
        y -= 18

        body = self.back_copy.get("body", "")
        if body:
            y = self._block(c, body, x, y, width, font("text", "regular"), 10.5, 15.5,
                            pal.ground_ink_soft, centred=False)
            y -= 14

        text_face = font("text", "regular")
        for benefit in self.back_copy.get("benefits", []):
            c.setFillColor(hex_color(pal.accent))
            c.circle(x + 2.4, y + 3.4, 1.9, stroke=0, fill=1)
            y = self._block(c, benefit, x + 14, y, width - 14, text_face, 10, 14.5,
                            pal.ground_ink, centred=False)
            y -= 7

        barcode_top = self._barcode_box()[1] + self._barcode_box()[3]
        closing = self.back_copy.get("closing", "")
        if closing and y > barcode_top + 26:
            self._block(c, closing, x, barcode_top + 26, width * 0.62,
                        font("text", "italic"), 10, 14, pal.accent, centred=False)

        website = self.brand.website.strip()
        if website:
            self._tracked(c, website, x, (geo.bleed + safe) * INCH + 2,
                          font("sans", "regular"), 7, pal.ground_ink_soft, 1.4, centred=False)

        self._barcode_keepout(c)

    # ------------------------------------------------------------- barcode
    def _barcode_box(self) -> tuple[float, float, float, float]:
        """(x, y, w, h) in points of the area KDP prints the barcode over."""
        from ..spec.kdp import KDP_SPEC

        offsets = KDP_SPEC["cover"]["barcode_offset_in"]
        width, height = self.geo.barcode_keepout
        x = (self.geo.spine_x - float(offsets["from_spine_edge"]) - width) * INCH
        y = (self.geo.bleed + float(offsets["from_bottom_trim"])) * INCH
        return x, y, width * INCH, height * INCH

    def _barcode_keepout(self, c: pdfcanvas.Canvas) -> None:
        x, y, width, height = self._barcode_box()
        c.setFillColor(hex_color("#FFFFFF"))
        c.rect(x, y, width, height, stroke=0, fill=1)

    # -------------------------------------------------------------- guides
    def _guides(self, c: pdfcanvas.Canvas) -> None:
        geo = self.geo
        bleed = geo.bleed * INCH
        safe = geo.safe_margin * INCH
        c.setLineWidth(0.6)

        c.setStrokeColor(hex_color("#FF0055"))
        c.rect(bleed, bleed, geo.width_pt - 2 * bleed, geo.height_pt - 2 * bleed,
               stroke=1, fill=0)
        for x in (geo.spine_x * INCH, geo.front_panel_x * INCH):
            c.line(x, 0, x, geo.height_pt)

        c.setStrokeColor(hex_color("#00AAFF"))
        c.setDash(3, 3)
        for panel_x in (geo.back_panel_x, geo.front_panel_x):
            c.rect((panel_x + geo.safe_margin) * INCH, bleed + safe,
                   (geo.trim.width - 2 * geo.safe_margin) * INCH,
                   geo.height_pt - 2 * (bleed + safe), stroke=1, fill=0)
        c.setDash()

        x, y, width, height = self._barcode_box()
        c.setStrokeColor(hex_color("#FF0055"))
        c.rect(x, y, width, height, stroke=1, fill=0)
        c.setFont("Helvetica", 7)
        c.setFillColor(hex_color("#FF0055"))
        c.drawString(x + 3, y + 4, "barcode keep-out 2.0 x 1.2 in")
        c.drawString(bleed + 4, geo.height_pt - bleed - 10,
                     f"PROOF — wrap {geo.width:.3f} x {geo.height:.3f} in, "
                     f"spine {geo.spine_width:.3f} in for {geo.page_count} pages "
                     f"({geo.paper}) · palette {self.palette.key} · motif {self.motif}. "
                     f"Do not upload this file.")

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
    palette: Palette | None = None,
    motif: str | None = None,
    seed: int = 0,
) -> Path:
    return CoverRenderer(
        geometry, title, subtitle, back_copy, config, palette, motif, seed
    ).render(path, guides)


__all__ = ["CoverRenderer", "render_cover", "small_caps"]
