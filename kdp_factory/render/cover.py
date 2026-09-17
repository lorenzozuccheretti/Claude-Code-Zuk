"""Station 3's renderer: the full wrap — back, spine and front as one PDF.

Nothing here decides a size. The geometry comes from ``spec.kdp.cover_geometry``
for the page count of the interior file that actually exists, which is the
difference between a cover that fits and a rejected upload.

Everything else here is design, and it is the part that sells the book. A cover
is judged at the size of a thumbnail in a list of twenty, so: one colour world
(``design.Palette``), one drawn mark (``artwork.draw``), a title set in a
face chosen for size, and nothing else competing with it.

Two files come out of a run: the cover itself, and a proof with the trim lines,
safe area and barcode keep-out drawn on top — the proof is for you, never for
upload.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from reportlab import rl_config
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdfcanvas

from ..config import EngineConfig
from ..content.rng import StageRandom
from ..spec.kdp import INCH, CoverGeometry
from . import artwork
from .design import (
    Palette,
    choose_motif,
    choose_palette,
    contrast,
    gradient_ground,
    hex_color,
    mix,
    readable_on,
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


def short_subtitle(subtitle: str, max_words: int = 7) -> str:
    """The cover's version of the subtitle.

    "A 109-Prompt Guided Journal for women running on empty" is written for a
    product page. At 160 pixels tall it is a smudge, so the cover takes the
    first clause and the listing keeps the whole thing.
    """
    text = " ".join(subtitle.split())
    for separator in (" — ", " – ", ": ", " for ", ", "):
        if separator in text:
            head = text.split(separator)[0].strip()
            if len(head.split()) >= 3:
                text = head
                break
    words = text.split()
    return " ".join(words[:max_words]) if len(words) > max_words else text


@dataclass
class StackItem:
    """One block in a centred group: how tall it is, and how to draw it."""

    height: float
    render: Callable[[float], None]
    gap: float = 0.0


def _stack_height(items: list) -> float:
    """How tall a group of blocks is, gaps included."""
    present = [item for item in items if item is not None]
    if not present:
        return 0.0
    return sum(item.height + item.gap for item in present) - present[0].gap


def _gap(item: "StackItem | None", gap: float) -> "StackItem | None":
    """Space above a block, applied only when the block exists."""
    if item is not None:
        item.gap = gap
    return item

# How the front panel is put together. A cover competes in a grid of twenty at
# about 200 pixels tall, so each of these commits to one loud idea.
# How much of the front panel's foot is kept for the press name. The art used
# to stop 0.40in up, close enough that an arch's cream interior swallowed the
# line set under it.
FRONT_FOOT_IN = 0.64

COMPOSITIONS = ("banded", "reversed", "framed", "emblem")

COMPOSITION_BY_TAG = {
    # Stress and burnout buyers were getting the quietest layout on the sheet.
    # They are not shopping for a funeral: put them on a loud one.
    "reversed": ("puzzle", "logic", "brain", "night", "bold", "challenge",
                 "burnout", "stress", "overwhelm", "anxiety"),
    "banded": ("planner", "goals", "productivity", "habit", "energy", "morning",
               "confidence", "women", "woman"),
    "framed": ("gratitude", "garden", "nature", "keepsake", "memory", "family"),
    "emblem": ("calm", "mindfulness", "healing", "quiet", "sleep", "meditation"),
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
        self.cover_subtitle = short_subtitle(subtitle)
        self.seed = seed
        # Every text extent drawn, so the renderer can be held to its own frames.
        self.text_extents: list[tuple[float, float]] = []
        # (left, right, baseline) for every line, so the barcode keep-out can be
        # verified now that no white box hides what is under it.
        self.text_boxes: list[tuple[float, float, float]] = []
        # What the title ended up as, so "can you read this at thumbnail size"
        # is a number rather than an opinion.
        self.title_metrics: dict[str, Any] = {}
        # Where the press name landed, so the strip kept for it can be checked.
        self.author_metrics: dict[str, Any] = {}

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
        self.text_boxes.append((left, right, baseline))

    def _note_extent(self, left: float, right: float) -> None:
        self.text_extents.append((left, right))

    def text_in_barcode_area(self, pad: float = 4.0) -> list[tuple[float, float, float]]:
        """Any line of text sitting where KDP will print its barcode.

        The background runs under the barcode, as on every trade paperback, but
        nothing that has to be read may be there.
        """
        bx, by, bw, bh = self._barcode_box()
        return [
            (left, right, baseline) for left, right, baseline in self.text_boxes
            if right > bx - pad and left < bx + bw + pad
            and by - pad - 10 < baseline < by + bh + pad
        ]

    def safe_box(self) -> tuple[float, float]:
        """Left and right bounds no text may cross, in points across the wrap."""
        geo = self.geo
        return ((geo.bleed + geo.safe_margin) * INCH,
                (geo.width - geo.bleed - geo.safe_margin) * INCH)

    # ---------------------------------------------------------- front panel
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

    # ------------------------------------------------- the type, as a stack
    def _stack_title(self, c, x: float, width: float, face: str, start: float,
                     minimum: float, colour: str, background: str) -> StackItem:
        size = fit_size(self.title, face, width, start, minimum, 4)
        lines = balanced(self.title, face, size, width)
        leading = size * 1.06
        self.title_metrics = {
            "size_pt": size, "face": face, "ink": colour,
            "background": background, "lines": len(lines),
        }
        height = (len(lines) - 1) * leading + size * 0.72

        def render(top: float) -> None:
            draw_block(c, self.title, x, top - size * 0.72, width, face, size,
                       leading, colour, centred=True, balance=True,
                       on_line=self._note_line)

        return StackItem(height, render)

    def _stack_subtitle(self, c, x: float, width: float, colour: str,
                        start: float = 18.0) -> StackItem | None:
        text = self.cover_subtitle
        if not text:
            return None
        face = font("sans", "regular")
        size = fit_size(text, face, width, start, 10, 2)
        lines = balanced(text, face, size, width)
        leading = size * 1.5
        height = (len(lines) - 1) * leading + size

        def render(top: float) -> None:
            draw_block(c, text, x, top - size, width, face, size, leading,
                       colour, centred=True, balance=True, on_line=self._note_line)

        return StackItem(height, render)

    def _stack_rule(self, c, cx: float, width: float, colour: str) -> StackItem:
        def render(top: float) -> None:
            rule(c, cx - width / 2, top - 1, width, colour, 1.6)

        return StackItem(2.0, render)

    def _stack_badge(self, c, cx: float, fill: str, ink: str,
                     outline: bool = False) -> StackItem | None:
        """The flash that says what the book gives you: '109 PROMPTS'."""
        if not self.badge:
            return None
        # `fill` is what the badge sits on — the pill's own colour, or the
        # ground when it is only outlined. Either way the lettering is held to
        # the same floor as the rest of the cover: the coral accent on the
        # coral ground was a badge nobody could read.
        ink = self._ink_on(fill, ink)
        face = font("sans", "bold")
        size = 10.0
        tracking = 2.2
        text_width = tracked_width(self.badge, face, size, tracking)
        pad_x, pad_y = 15.0, 8.0
        box_w, box_h = text_width + pad_x * 2, size + pad_y * 2

        def render(top: float) -> None:
            left = cx - box_w / 2
            bottom = top - box_h
            if outline:
                c.setStrokeColor(hex_color(ink))
                c.setLineWidth(1.3)
                c.roundRect(left, bottom, box_w, box_h, box_h / 2, stroke=1, fill=0)
            else:
                c.setFillColor(hex_color(fill))
                c.roundRect(left, bottom, box_w, box_h, box_h / 2, stroke=0, fill=1)
            draw_tracked(c, self.badge, left + pad_x, bottom + pad_y + 1.5, face,
                         size, ink, tracking, centred=False)
            self._note_extent(left, left + box_w)

        return StackItem(box_h, render)

    def _assemble(self, build, zone_h: float, start: float, minimum: float,
                  step: float = 3.0) -> list:
        """Size the title so the whole group fits its zone.

        Centring a stack that is taller than its zone just moves the overflow
        to both ends: the title ran over the artwork below it. So the title
        starts at the size the composition wants and comes down until the
        assembled group fits.
        """
        size = start
        items = build(size)
        while size > minimum and _stack_height(items) > zone_h:
            size -= step
            items = build(size)
        return items

    @staticmethod
    def _stack_bounds(items: list, zone_y: float, zone_h: float,
                      lift: float = 0.06) -> tuple[float, float]:
        """Where a group of blocks will land: (top, bottom) in points.

        Text centred by arithmetic sits low to the eye, so the stack is raised
        by a fraction of the zone. Knowing the bounds before drawing lets the
        artwork be placed against the copy rather than against the trim.
        """
        present = [item for item in items if item is not None]
        if not present:
            return zone_y + zone_h, zone_y + zone_h
        total = _stack_height(present)
        top = min(zone_y + (zone_h + total) / 2 + zone_h * lift, zone_y + zone_h)
        return top, top - total

    def _draw_stack(self, items: list, zone_y: float, zone_h: float,
                    lift: float = 0.06) -> None:
        """Centre a group of blocks in its zone, lifted a little.

        This is the whole reason the covers stopped looking top-heavy: the type
        is placed as a group, not flowed from the top edge until it runs out.
        """
        items = [item for item in items if item is not None]
        if not items:
            return
        top, _ = self._stack_bounds(items, zone_y, zone_h, lift)
        for index, item in enumerate(items):
            if index:
                top -= item.gap
            item.render(top)
            top -= item.height

    def _author(self, c, cx: float, y: float, colour: str, field: str,
                clear_to: float) -> None:
        """The imprint on the front foot, in an ink that survives what is under it.

        Two things used to go wrong here. A composition would put this line on
        the bold band and ask for the dark title ink, so the press name was in
        the file and invisible in print. And the artwork ran down to meet it,
        so an arch's cream interior swallowed what was left. `field` fixes the
        first; `clear_to` — the height the art stops at — records the second.
        """
        author = self.brand.author.strip()
        if author:
            size = 9.5
            width = draw_tracked(c, author, cx, y, font("sans", "bold"), size,
                                 self._ink_on(field, colour), 2.6)
            self._note_line(cx - width / 2, cx + width / 2, y)
            self.author_metrics = {"baseline": y, "cap_top": y + size * 0.72,
                                   "left": cx - width / 2, "right": cx + width / 2,
                                   "clear_to": clear_to}

    # ------------------------------------------------------- compositions
    def _compose_banded(self, c) -> None:
        """A field of colour above, the type centred on a clean band below."""
        pal = self.palette
        px, py, pw, ph = self._panel_box()
        x, y, width, height = self._safe_frame()

        band_y = py + ph * 0.54
        c.setFillColor(hex_color(pal.panel))
        c.rect(px, py, pw, ph, stroke=0, fill=1)
        c.setFillColor(hex_color(pal.bold))
        c.rect(px, band_y, pw, ph - band_y, stroke=0, fill=1)
        self._art(c, px, band_y, pw, ph - band_y, pal.bold, 1.0, primary=pal.bold_ink)

        zone_y, zone_h = y + 26, band_y - y - 36
        items = self._assemble(lambda size: [
            self._stack_badge(c, x + width / 2, pal.bold, pal.bold_ink),
            _gap(self._stack_title(c, x, width, font("poster", "bold"), size, 20,
                                   pal.title_ink, pal.panel), 20),
            _gap(self._stack_subtitle(c, x + width * 0.05, width * 0.9,
                                      pal.panel_ink), 18),
        ], zone_h, 58, 20)
        self._draw_stack(items, zone_y, zone_h)
        self._author(c, x + width / 2, y + 2, pal.title_ink, pal.panel, band_y)

    def _compose_reversed(self, c) -> None:
        """Dark panel, art behind, the title reversed out of the middle."""
        pal = self.palette
        px, py, pw, ph = self._panel_box()
        x, y, width, height = self._safe_frame()

        gradient_ground(c, px, py, pw, ph, pal.ground, pal.ground_deep)
        # The art sits below the type rather than behind it. A translucent scrim
        # over the artwork reads as exactly what it is — a grey rectangle — and
        # the cover is stronger when the title has clean ground under it.
        foot = FRONT_FOOT_IN * INCH   # a clear strip for the imprint
        art_h = ph * 0.52
        self._art(c, px, py + foot, pw, art_h - foot, pal.ground, 1.1)

        items = self._assemble(lambda size: [
            self._stack_badge(c, x + width / 2, pal.ground, pal.accent, outline=True),
            _gap(self._stack_title(c, x, width, font("poster", "bold"), size, 20,
                                   pal.ground_ink, pal.ground), 22),
            _gap(self._stack_rule(c, x + width / 2, width * 0.22, pal.accent), 20),
            _gap(self._stack_subtitle(c, x + width * 0.06, width * 0.88,
                                      pal.ground_ink_soft), 18),
        ], zone_h := (y + height) - (py + art_h) - 16, 58, 20)
        self._draw_stack(items, py + art_h + 8, zone_h, lift=0.0)
        self._author(c, x + width / 2, y + 4, pal.ground_ink, pal.ground,
                     py + foot)

    def _compose_framed(self, c) -> None:
        """A thick border holds it; art below, type centred above."""
        pal = self.palette
        px, py, pw, ph = self._panel_box()
        x, y, width, height = self._safe_frame()

        c.setFillColor(hex_color(pal.bold))
        c.rect(px, py, pw, ph, stroke=0, fill=1)
        inset = 0.26 * INCH
        inner_x = (self.geo.front_panel_x + self.geo.bleed) * INCH + inset
        inner_y = self.geo.bleed * INCH + inset
        inner_w = self.geo.trim.width * INCH - inset * 2
        inner_h = (self.geo.height - 2 * self.geo.bleed) * INCH - inset * 2
        c.setFillColor(hex_color(pal.panel))
        c.rect(inner_x, inner_y, inner_w, inner_h, stroke=0, fill=1)

        art_h = inner_h * 0.38
        foot = FRONT_FOOT_IN * INCH   # a clear strip for the imprint
        c.setFillColor(hex_color(pal.bold))
        c.rect(inner_x, inner_y, inner_w, art_h, stroke=0, fill=1)
        self._art(c, inner_x, inner_y + foot, inner_w, art_h - foot,
                  pal.bold, 0.95, primary=pal.bold_ink)

        zone_y = inner_y + art_h + 16
        zone_h = inner_h - art_h - 46
        items = self._assemble(lambda size: [
            self._stack_badge(c, inner_x + inner_w / 2, pal.bold, pal.bold_ink),
            _gap(self._stack_title(c, inner_x + 12, inner_w - 24,
                                   font("display", "bold"), size, 26,
                                   pal.title_ink, pal.panel), 20),
            _gap(self._stack_subtitle(c, inner_x + inner_w * 0.08, inner_w * 0.84,
                                      pal.panel_ink), 18),
        ], zone_h, 88, 26)
        self._draw_stack(items, zone_y, zone_h, lift=0.02)
        self._author(c, x + width / 2, inner_y + 14, pal.bold_ink, pal.bold,
                     inner_y + foot)

    def _compose_emblem(self, c) -> None:
        """Light panel, one big mark low, the title centred above it."""
        pal = self.palette
        px, py, pw, ph = self._panel_box()
        x, y, width, height = self._safe_frame()

        gradient_ground(c, px, py, pw, ph, pal.panel, pal.panel_deep)
        art_h = ph * 0.42
        c.setFillColor(hex_color(pal.bold))
        c.rect(px, py, pw, art_h, stroke=0, fill=1)
        foot = FRONT_FOOT_IN * INCH   # a clear strip for the imprint
        self._art(c, px, py + foot, pw, art_h - foot, pal.bold, 1.0,
                  primary=pal.bold_ink)

        zone_y = py + art_h + 18
        zone_h = (y + height) - zone_y
        items = self._assemble(lambda size: [
            self._stack_badge(c, x + width / 2, pal.bold, pal.bold_ink),
            _gap(self._stack_title(c, x, width, font("display", "bold"), size, 28,
                                   pal.title_ink, pal.panel), 22),
            _gap(self._stack_rule(c, x + width / 2, width * 0.24, pal.accent), 20),
            _gap(self._stack_subtitle(c, x + width * 0.06, width * 0.88,
                                      pal.panel_ink), 18),
        ], zone_h, 92, 28)
        self._draw_stack(items, zone_y, zone_h, lift=0.0)
        self._author(c, x + width / 2, y + 4, pal.bold_ink, pal.bold,
                     py + foot)

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
            draw_tracked(c, imprint, available * 0.40, -size * 0.34,
                         font("sans", "regular"), min(size * 0.5, 7.5),
                         pal.ground_ink_soft, 1.2)
        c.restoreState()

    def _back_panel(self, c: pdfcanvas.Canvas) -> None:
        """Back cover: one selling block on the panel's centre axis.

        The front cover is centred, so the back is too — an off-axis column of
        copy beside a centred title reads as a mistake rather than a choice.
        The whole group (hook, rule, body, benefits, closing, site) is placed
        as one stack between the imprint line and the top of the barcode
        keep-out, and the artwork is a band underneath it, so nothing a buyer
        has to read ever sits on top of a shape.
        """
        geo, pal = self.geo, self.palette
        back_w = (geo.trim.width + geo.bleed) * INCH
        gradient_ground(c, 0, 0, back_w, geo.height_pt, pal.ground, pal.ground_deep)

        safe = geo.safe_margin
        x = (geo.back_panel_x + safe + 0.14) * INCH
        width = (geo.trim.width - 2 * safe - 0.28) * INCH
        cx = x + width / 2
        top = (geo.height - geo.bleed - safe) * INCH

        _, barcode_y, _, barcode_h = self._barcode_box()
        zone_bottom = barcode_y + barcode_h + 16
        zone_h = (top - 30) - zone_bottom

        draw_tracked(c, self.brand.imprint or "", cx, top - 12,
                     font("sans", "regular"), 8, pal.ground_ink_soft, 2.4)

        benefits = [b for b in self.back_copy.get("benefits", []) if b]
        items = self._back_stack(c, x, cx, width, benefits)
        # The keep-out is not negotiable, so the copy gives way to it: the
        # bullet list is what a back cover has too much of, and it is what
        # comes off until the group clears the barcode.
        while _stack_height(items) > zone_h and len(benefits) > 3:
            benefits = benefits[:-1]
            items = self._back_stack(c, x, cx, width, benefits)

        # The art fills the band the copy has vacated, measured from where the
        # copy actually lands rather than from the trim — otherwise a short
        # back cover leaves a hole between the last line and the shape. Tone on
        # tone: it is a texture, not a subject, and nothing readable sits on it.
        _, stack_bottom = self._stack_bounds(items, zone_bottom, zone_h, 0.0)
        band = max(stack_bottom - 24, geo.height_pt * 0.14)
        c.saveState()
        clip = c.beginPath()
        clip.rect(0, 0, back_w, band)
        c.clipPath(clip, stroke=0, fill=0)
        # Dropped below the trim so the shape sits on the bottom edge and
        # bleeds off it, rather than floating with a sliver of ground beneath.
        self._art(c, -back_w * 0.14, -band * 0.09, back_w * 1.28, band * 1.09,
                  pal.ground, 0.82, primary=self._band_ink(pal.ground))
        c.restoreState()

        self._draw_stack(items, zone_bottom, zone_h, lift=0.0)

    def _back_stack(self, c, x: float, cx: float, width: float,
                    benefits: list[str]) -> list:
        """Every block on the back cover, in order, each centred on the axis."""
        pal = self.palette
        # Point sizes tuned on a 6-inch panel are a smudge on an 8.5-inch one:
        # the copy grows with the trim so it reads the same at arm's length.
        scale = min(max(self.geo.trim.width / 6.0, 1.0), 1.3)
        items: list[StackItem | None] = []

        hook = self.back_copy.get("hook", "")
        if hook:
            face = font("display", "bold")
            size = fit_size(hook, face, width, 24 * scale, 14, 3)
            lines = balanced(hook, face, size, width)
            leading = size * 1.2
            items.append(StackItem(
                (len(lines) - 1) * leading + size * 0.74,
                lambda top_y, f=face, sz=size, ld=leading: draw_block(
                    c, hook, x, top_y - sz * 0.74, width, f, sz, ld,
                    pal.ground_ink, centred=True, balance=True,
                    on_line=self._note_line)))
            items.append(_gap(StackItem(
                2.0,
                lambda top_y: rule(c, cx - width * 0.10, top_y - 1,
                                   width * 0.20, pal.ground_ink_soft, 1.6)),
                15))

        body = self.back_copy.get("body", "")
        if body:
            face = font("text", "regular")
            # Full ink, not the soft tone. This paragraph is the one a buyer
            # actually reads on a phone, and soft ink on a coloured ground was
            # the reason it could not be read.
            size, leading = 11 * scale, 16 * scale
            lines = wrap(body, face, size, width)
            items.append(_gap(StackItem(
                (len(lines) - 1) * leading + size,
                lambda top_y, f=face, sz=size, ld=leading: draw_block(
                    c, body, x, top_y - sz, width, f, sz, ld,
                    pal.ground_ink, centred=True, on_line=self._note_line)),
                17 * scale))

        if benefits:
            items.append(_gap(self._stack_benefits(c, cx, width, benefits, scale),
                              19 * scale))

        closing = self.back_copy.get("closing", "")
        if closing:
            ink = self._ink_on(pal.ground, pal.accent)
            face = font("text", "italic")
            measure = width * 0.82
            size = fit_size(closing, face, measure, 11.5 * scale, 9.5, 1)
            lines = balanced(closing, face, size, measure)
            items.append(_gap(StackItem(
                (len(lines) - 1) * (size * 1.35) + size,
                lambda top_y, f=face, sz=size, m=measure: draw_block(
                    c, closing, cx - m / 2, top_y - sz, m, f, sz, sz * 1.35,
                    ink, centred=True, balance=True, on_line=self._note_line)),
                19 * scale))

        website = self.brand.website.strip()
        if website:
            face = font("sans", "regular")
            size = 7.5 * scale
            site_w = tracked_width(website, face, size, 1.6 * scale)

            def site(top_y: float, f=face, sz=size, w=site_w) -> None:
                draw_tracked(c, website, cx, top_y - sz, f, sz,
                             pal.ground_ink_soft, 1.6 * scale)
                self._note_line(cx - w / 2, cx + w / 2, top_y - sz)

            items.append(_gap(StackItem(size, site), 20 * scale))

        return [item for item in items if item is not None]

    def _stack_benefits(self, c, cx: float, width: float,
                        benefits: list[str], scale: float = 1.0) -> StackItem:
        """The bullet list, as one block centred on the panel axis.

        Centring each bullet separately makes a ragged diamond of text. The
        items stay left-aligned to each other; what is centred is the block,
        measured from the longest line it actually sets.
        """
        face = font("text", "regular")
        size, leading, indent = 10.5 * scale, 15.0 * scale, 15.0 * scale
        measure = width - indent
        widest = 0.0
        laid: list[list[str]] = []
        for benefit in benefits:
            lines = wrap(benefit, face, size, measure)
            laid.append(lines)
            for line in lines:
                widest = max(widest, pdfmetrics.stringWidth(line, face, size))
        block_w = widest + indent
        left = cx - block_w / 2
        step = 7.0 * scale
        height = sum((len(lines) - 1) * leading + size for lines in laid)
        height += step * (len(laid) - 1)
        pal = self.palette
        # A bullet that cannot be seen is not a bullet: the accent is held to
        # the same contrast floor as anything else drawn on the ground.
        dot = self._ink_on(pal.ground, pal.accent, floor=2.2)

        def render(top_y: float) -> None:
            y = top_y
            for lines in laid:
                c.setFillColor(hex_color(dot))
                c.circle(left + 2.6 * scale, y - size * 0.44, 2.2 * scale,
                         stroke=0, fill=1)
                draw_block(c, " ".join(lines), left + indent, y - size, widest,
                           face, size, leading, pal.ground_ink,
                           on_line=self._note_line)
                y -= (len(lines) - 1) * leading + size + step

        return StackItem(height, render)

    def _band_ink(self, field: str, target: float = 2.0) -> str:
        """A tone that reads as texture on this ground: present, never loud.

        A fixed blend does not travel: 62% toward the ground is a soft rose on
        coral and an invisible near-black on midnight. So the blend is chosen
        by contrast instead — whichever of the palette's two strong colours has
        room on this ground, pulled back until it lands at roughly `target`.
        """
        pal = self.palette
        base = max((pal.bold, pal.panel), key=lambda ink: contrast(ink, field))
        if contrast(base, field) <= target:
            return base
        steps = 24
        return min(
            (mix(base, field, step / steps) for step in range(steps + 1)),
            key=lambda ink: abs(contrast(ink, field) - target))

    def _ink_on(self, field: str, preferred: str, floor: float = 3.0) -> str:
        """The preferred ink if it can be read on this field, else one that can.

        The coral palette's accent lands at 1.4:1 on its own ground — printed,
        that is a line nobody sees. Anything that carries words gets checked
        against a floor before it is used.
        """
        if contrast(preferred, field) >= floor:
            return preferred
        pal = self.palette
        return readable_on(field, pal.ground_ink, pal.title_ink)

    def _barcode_box(self) -> tuple[float, float, float, float]:
        """(x, y, w, h) in points of the area KDP prints the barcode over.

        The design keeps text and focal artwork out of it; the background runs
        underneath, as it does on every trade paperback. Only the proof draws
        the box, so you can check the area before uploading.
        """
        from ..spec.kdp import KDP_SPEC

        offsets = KDP_SPEC["cover"]["barcode_offset_in"]
        width, height = self.geo.barcode_keepout
        x = (self.geo.spine_x - float(offsets["from_spine_edge"]) - width) * INCH
        y = (self.geo.bleed + float(offsets["from_bottom_trim"])) * INCH
        return x, y, width * INCH, height * INCH

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
    def legibility(self, thumbnail_height_px: int = 160) -> dict[str, Any]:
        """Can this cover be read in a search grid?

        The phone is the hard case: Amazon's app shows a paperback at roughly
        160 pixels tall. At that size a title has a cap height of a few pixels,
        and a colour pairing that looked fine at full size can vanish. Both are
        arithmetic, so both are checked rather than admired.
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
            "barcode_area_clear": not self.text_in_barcode_area(),
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
