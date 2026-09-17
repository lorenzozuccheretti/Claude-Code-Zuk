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
from . import artwork
from .design import (
    MotifSpec,
    Palette,
    choose_motif,
    choose_palette,
    contrast,
    draw_motif,
    gradient_ground,
    hex_color,
    small_caps,
)
from .layout import (
    balanced,
    draw_block,
    draw_tracked,
    fit_size,
    rule,
    tracked_width,
    wrap,
)
from .typography import font

# How the front panel is put together. A cover competes in a grid of twenty at
# about 200 pixels tall, so each of these commits to one loud idea.
COMPOSITIONS = ("banded", "reversed", "framed", "emblem")

COMPOSITION_BY_TAG = {
    "reversed": ("puzzle", "logic", "brain", "night", "bold", "challenge"),
    "banded": ("planner", "goals", "productivity", "habit", "energy", "morning"),
    "framed": ("gratitude", "garden", "nature", "keepsake", "memory", "family"),
    "emblem": ("calm", "mindfulness", "stress", "burnout", "healing", "quiet"),
}


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
        art_style: str | None = None,
        composition: str | None = None,
        badge: str = "",
        words: list[str] | None = None,
    ) -> None:
        self.geo = geometry
        self.title = title
        self.subtitle = subtitle
        self.back_copy = back_copy
        self.config = config
        self.brand = config.brand
        rng = StageRandom(seed, "cover")
        words = list(words or [title, subtitle])
        self.words = words
        self.palette = palette or choose_palette(words, rng)
        self.motif = motif or choose_motif(words, rng)
        self.art_style = art_style or artwork.choose_style(words, rng)
        self.composition = composition or self._choose_composition(words, rng)
        self.badge = (badge or "").strip()
        self.seed = seed
        # Every text extent drawn, so the renderer can be held to its own frames.
        self.text_extents: list[tuple[float, float]] = []
        # What the title ended up as, so "can you read this at thumbnail size"
        # is a number rather than an opinion.
        self.title_metrics: dict[str, Any] = {}

    @staticmethod
    def _choose_composition(words, rng: StageRandom) -> str:
        from ..content.text import token_set

        haystack = token_set(" ".join(w or "" for w in words))
        for composition, tags in COMPOSITION_BY_TAG.items():
            if haystack & set(tags):
                return composition
        return rng.stream("composition").choice(COMPOSITIONS)

    # ------------------------------------------------------------- helpers
    def _note_line(self, left: float, right: float, baseline: float) -> None:
        self._note_extent(left, right)

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

    # -------------------------------------------------------------- panels    # ---------------------------------------------------------- front panel
    def _front_panel(self, c: pdfcanvas.Canvas) -> None:
        {
            "banded": self._compose_banded,
            "reversed": self._compose_reversed,
            "framed": self._compose_framed,
            "emblem": self._compose_emblem,
        }.get(self.composition, self._compose_emblem)(c)

    def _panel_box(self) -> tuple[float, float, float, float]:
        """(x, y, w, h) of the whole front panel, bleed included."""
        geo = self.geo
        return (geo.front_panel_x * INCH, 0.0,
                (geo.trim.width + geo.bleed) * INCH, geo.height_pt)

    def _safe_frame(self) -> tuple[float, float, float, float]:
        """(x, y, w, h) of the area type may occupy on the front panel."""
        geo, safe = self.geo, self.geo.safe_margin
        return ((geo.front_panel_x + safe) * INCH, (geo.bleed + safe) * INCH,
                (geo.trim.width - 2 * safe) * INCH,
                (geo.height - 2 * (geo.bleed + safe)) * INCH)

    def _art(self, c, x: float, y: float, width: float, height: float,
             field: str, scale: float = 1.0, primary: str = "") -> None:
        artwork.draw(c, self.art_style, artwork.ArtSpec(
            x=x, y=y, width=width, height=height, palette=self.palette,
            field=field, seed=self.seed, scale=scale, primary=primary,
            extras={"columns": 6}))

    def _title_block(self, c, x: float, y: float, width: float, face: str,
                     start: float, minimum: float, colour: str,
                     centred: bool = True, background: str = "") -> float:
        size = fit_size(self.title, face, width, start, minimum, 3)
        self.title_metrics = {
            "size_pt": size,
            "face": face,
            "ink": colour,
            "background": background or self.palette.panel,
            "lines": len(balanced(self.title, face, size, width)),
        }
        return draw_block(c, self.title, x, y, width, face, size, size * 1.06,
                          colour, centred=centred, balance=True,
                          on_line=self._note_line)

    def _badge(self, c, x: float, y: float, fill: str, ink: str,
               outline: bool = False, centred: bool = True) -> float:
        """The flash that says what the book is: '109 PROMPTS', 'LARGE PRINT'."""
        if not self.badge:
            return y
        face = font("sans", "bold")
        size = 8.5
        tracking = 2.0
        text_width = tracked_width(self.badge, face, size, tracking)
        pad_x, pad_y = 13.0, 7.0
        box_w = text_width + pad_x * 2
        box_h = size + pad_y * 2
        left = x - box_w / 2 if centred else x
        if outline:
            c.setStrokeColor(hex_color(ink))
            c.setLineWidth(1.1)
            c.roundRect(left, y - pad_y, box_w, box_h, box_h / 2, stroke=1, fill=0)
        else:
            c.setFillColor(hex_color(fill))
            c.roundRect(left, y - pad_y, box_w, box_h, box_h / 2, stroke=0, fill=1)
        draw_tracked(c, self.badge, left + pad_x, y + 1.5, face, size, ink,
                     tracking, centred=False)
        self._note_extent(left, left + box_w)
        return y - pad_y

    def _author(self, c, cx: float, y: float, colour: str) -> None:
        author = self.brand.author.strip()
        if author:
            width = draw_tracked(c, author, cx, y, font("sans", "bold"), 9.5,
                                 colour, 2.4)
            self._note_extent(cx - width / 2, cx + width / 2)

    # ------------------------------------------------------- compositions
    def _compose_banded(self, c) -> None:
        """A field of colour on top, the type on a clean band below."""
        pal = self.palette
        px, py, pw, ph = self._panel_box()
        x, y, width, height = self._safe_frame()

        band_y = py + ph * 0.42
        c.setFillColor(hex_color(pal.panel))
        c.rect(px, py, pw, ph, stroke=0, fill=1)
        c.setFillColor(hex_color(pal.bold))
        c.rect(px, band_y, pw, ph - band_y, stroke=0, fill=1)
        self._art(c, px, band_y, pw, ph - band_y, pal.bold, 1.0,
                  primary=pal.bold_ink)

        cursor = band_y - 46
        cursor = self._title_block(c, x, cursor, width, font("poster", "bold"),
                                   40, 17, pal.title_ink, background=pal.panel)
        cursor -= 16
        if self.subtitle:
            face = font("text", "regular")
            size = fit_size(self.subtitle, face, width * 0.9, 12, 8.5, 3)
            cursor = draw_block(c, self.subtitle, x + width * 0.05, cursor,
                                width * 0.9, face, size, size * 1.5,
                                pal.panel_ink, centred=True, balance=True,
                                on_line=self._note_line)
        self._badge(c, x + width / 2, band_y + 16, pal.panel, pal.title_ink)
        self._author(c, x + width / 2, y + 2, pal.title_ink)

    def _compose_reversed(self, c) -> None:
        """Dark panel, art behind, title reversed out of it."""
        pal = self.palette
        px, py, pw, ph = self._panel_box()
        x, y, width, height = self._safe_frame()

        gradient_ground(c, px, py, pw, ph, pal.ground, pal.ground_deep)
        self._art(c, px, py + ph * 0.06, pw, ph * 0.62, pal.ground, 1.05)

        cursor = y + height * 0.94
        cursor = self._title_block(c, x, cursor, width, font("poster", "bold"),
                                   40, 17, pal.ground_ink, background=pal.ground)
        cursor -= 14
        rule(c, x + width * 0.38, cursor, width * 0.24, pal.accent, 1.4)
        cursor -= 22
        if self.subtitle:
            face = font("text", "regular")
            size = fit_size(self.subtitle, face, width * 0.88, 12, 8.5, 3)
            cursor = draw_block(c, self.subtitle, x + width * 0.06, cursor,
                                width * 0.88, face, size, size * 1.5,
                                pal.ground_ink_soft, centred=True, balance=True,
                                on_line=self._note_line)
        self._badge(c, x + width / 2, y + 34, pal.ground, pal.accent, outline=True)
        self._author(c, x + width / 2, y + 2, pal.ground_ink)

    def _compose_framed(self, c) -> None:
        """A thick border holds everything; the art is a block inside it."""
        pal = self.palette
        px, py, pw, ph = self._panel_box()
        x, y, width, height = self._safe_frame()

        c.setFillColor(hex_color(pal.bold))
        c.rect(px, py, pw, ph, stroke=0, fill=1)
        inset = 0.30 * INCH
        inner_x = (self.geo.front_panel_x + self.geo.bleed) * INCH + inset
        inner_y = self.geo.bleed * INCH + inset
        inner_w = self.geo.trim.width * INCH - inset * 2
        inner_h = (self.geo.height - 2 * self.geo.bleed) * INCH - inset * 2
        c.setFillColor(hex_color(pal.panel))
        c.rect(inner_x, inner_y, inner_w, inner_h, stroke=0, fill=1)

        art_h = inner_h * 0.40
        self._art(c, inner_x, inner_y, inner_w, art_h, pal.panel, 0.88)

        cursor = inner_y + inner_h - 46
        cursor = self._title_block(c, inner_x + 14, cursor, inner_w - 28,
                                   font("display", "bold"), 62, 24, pal.title_ink,
                                   background=pal.panel)
        cursor -= 14
        self._badge(c, inner_x + inner_w / 2, cursor - 6, pal.bold, pal.bold_ink)
        cursor -= 34
        if self.subtitle:
            face = font("text", "regular")
            size = fit_size(self.subtitle, face, inner_w * 0.82, 12, 8.5, 3)
            draw_block(c, self.subtitle, inner_x + inner_w * 0.09, cursor,
                       inner_w * 0.82, face, size, size * 1.5, pal.panel_ink,
                       centred=True, balance=True, on_line=self._note_line)
        self._author(c, x + width / 2, inner_y + 12, pal.title_ink)

    def _compose_emblem(self, c) -> None:
        """Light panel, one big mark low, the title above it."""
        pal = self.palette
        px, py, pw, ph = self._panel_box()
        x, y, width, height = self._safe_frame()

        gradient_ground(c, px, py, pw, ph, pal.panel, pal.panel_deep)
        self._art(c, px, py + ph * 0.05, pw, ph * 0.46, pal.panel, 1.0)

        cursor = y + height * 0.96
        cursor = self._title_block(c, x, cursor, width, font("display", "bold"),
                                   74, 26, pal.title_ink, background=pal.panel)
        cursor -= 18
        rule(c, x + width * 0.36, cursor, width * 0.28, pal.accent, 1.3)
        cursor -= 24
        if self.subtitle:
            face = font("text", "regular")
            size = fit_size(self.subtitle, face, width * 0.88, 13, 9, 3)
            cursor = draw_block(c, self.subtitle, x + width * 0.06, cursor,
                                width * 0.88, face, size, size * 1.52,
                                pal.panel_ink, centred=True, balance=True,
                                on_line=self._note_line)
        self._badge(c, x + width / 2, cursor - 18, pal.bold, pal.bold_ink)
        self._author(c, x + width / 2, y + 2, pal.title_ink)

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
                     f"({geo.paper}) · {self.composition} · {self.art_style} · "
                     f"palette {self.palette.key}. "
                     f"Do not upload this file.")

    # --------------------------------------------------------- legibility
    def legibility(self, thumbnail_height_px: int = 200) -> dict[str, Any]:
        """Can this cover be read in a search grid?

        Amazon shows a paperback at roughly 200 pixels tall. At that size a
        title has a cap height of a few pixels, and a colour pairing that looked
        fine at full size can vanish. Both are arithmetic, so both are checked
        rather than admired.
        """
        metrics = self.title_metrics
        if not metrics:
            return {"measured": False}
        scale = thumbnail_height_px / (self.geo.trim.height * INCH)
        cap_height_pt = metrics["size_pt"] * 0.72
        return {
            "measured": True,
            "thumbnail_height_px": thumbnail_height_px,
            "title_size_pt": round(metrics["size_pt"], 1),
            "title_lines": metrics["lines"],
            "title_cap_px": round(cap_height_pt * scale, 2),
            "title_contrast": round(contrast(metrics["ink"], metrics["background"]), 2),
            "composition": self.composition,
            "art_style": self.art_style,
            "palette": self.palette.key,
            "has_badge": bool(self.badge),
        }

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
    art_style: str | None = None,
    composition: str | None = None,
    badge: str = "",
    words: list[str] | None = None,
) -> Path:
    return CoverRenderer(
        geometry, title, subtitle, back_copy, config, palette, motif, seed,
        art_style, composition, badge, words,
    ).render(path, guides)


__all__ = ["CoverRenderer", "render_cover", "small_caps"]
