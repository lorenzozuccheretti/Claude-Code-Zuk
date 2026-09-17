"""The cover's visual identity: palettes, motifs, and how a niche picks one.

A flat rectangle of brand colour with the title typed on it is what the engine
used to produce, and on a page of search results it loses to everything. What
makes these covers work at thumbnail size is a colour world, one drawn mark,
and type that was chosen rather than defaulted.

Both choices are deterministic. A palette and a motif are picked from the
niche's own words where they say something, and from the run seed where they do
not — so the same niche always gets the same cover, and two books in one
imprint do not collide by accident.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from reportlab.lib.colors import Color, HexColor

from ..content.rng import StageRandom
from ..content.text import token_set


def hex_color(value: str, alpha: float = 1.0) -> Color:
    base = HexColor(value)
    if alpha >= 1.0:
        return base
    return Color(base.red, base.green, base.blue, alpha=alpha)


def relative_luminance(value: str) -> float:
    """WCAG relative luminance of a hex colour."""
    colour = HexColor(value)

    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return (0.2126 * channel(colour.red) + 0.7152 * channel(colour.green)
            + 0.0722 * channel(colour.blue))


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colours, 1.0 to 21.0."""
    la, lb = relative_luminance(a), relative_luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def readable_on(background: str, light: str, dark: str) -> str:
    """Whichever of two inks is more readable on this background."""
    return light if contrast(light, background) >= contrast(dark, background) else dark


def mix(a: str, b: str, amount: float) -> str:
    """Blend two hex colours — used to derive a gradient's far end."""
    ca, cb = HexColor(a), HexColor(b)
    r = ca.red + (cb.red - ca.red) * amount
    g = ca.green + (cb.green - ca.green) * amount
    bl = ca.blue + (cb.blue - ca.blue) * amount
    return "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(bl * 255))


@dataclass(frozen=True)
class Palette:
    """One colour world: the cover ground, what sits on it, and the page inside."""

    key: str
    label: str
    ground: str           # back cover and spine
    ground_deep: str      # the far end of the ground's gradient
    panel: str            # front cover
    panel_deep: str
    title_ink: str        # title on the front panel
    panel_ink: str        # body text on the front panel
    ground_ink: str       # text on the back cover
    ground_ink_soft: str
    accent: str           # rules, marks, the one loud thing
    # A saturated field for artwork and colour blocking. The old accent was
    # pitched for hairlines: at 2.5:1 against the panel it vanished the moment
    # the cover was a thumbnail.
    bold: str = ""
    bold_ink: str = ""
    interior_ink: str = "#1C1B19"
    interior_soft: str = "#6E6A64"
    interior_rule: str = "#C9C4BB"
    interior_accent: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.bold:
            object.__setattr__(self, "bold", self.accent)
        if not self.bold_ink:
            object.__setattr__(
                self, "bold_ink", readable_on(self.bold, self.panel, self.ground))

    @property
    def accent_for_page(self) -> str:
        return self.interior_accent or self.accent

    def contrast_report(self) -> dict[str, float]:
        """Every pairing a cover relies on, measured."""
        return {
            "title_on_panel": round(contrast(self.title_ink, self.panel), 2),
            "bold_on_panel": round(contrast(self.bold, self.panel), 2),
            "ink_on_bold": round(contrast(self.bold_ink, self.bold), 2),
            "ink_on_ground": round(contrast(self.ground_ink, self.ground), 2),
            "accent_on_ground": round(contrast(self.accent, self.ground), 2),
        }


# Eight worlds, each built around a ground a book can actually be printed in.
PALETTES: tuple[Palette, ...] = (
    Palette(
        key="dusk", label="Dusk — evening plum and warm sand",
        ground="#2E2338", ground_deep="#1B1422",
        panel="#EFE6DC", panel_deep="#E2D4C6",
        title_ink="#2E2338", panel_ink="#5A4A52", ground_ink="#F1E7DC",
        ground_ink_soft="#C3B2BC", accent="#C97B6A",
        bold="#8E4A63",
        interior_ink="#221B28", interior_soft="#6F6472", interior_rule="#CFC6CF",
        tags=("sleep", "evening", "night", "insomnia", "dream", "bedtime"),
    ),
    Palette(
        key="sage", label="Sage — garden green and cream",
        ground="#3C4A3E", ground_deep="#26302A",
        panel="#EDEAE0", panel_deep="#DDD9CB",
        title_ink="#2C3830", panel_ink="#55604F", ground_ink="#EFEDE3",
        ground_ink_soft="#BFC6B8", accent="#B4854A",
        bold="#3F6B45",
        interior_ink="#23291F", interior_soft="#6B7063", interior_rule="#C8CCC0",
        tags=("gratitude", "garden", "nature", "growth", "mindfulness", "wellbeing", "thankful"),
    ),
    Palette(
        key="harbour", label="Harbour — deep teal and coral",
        ground="#1D3C44", ground_deep="#122A30",
        panel="#E8EDEB", panel_deep="#D6E0DD",
        title_ink="#173339", panel_ink="#4A5D60", ground_ink="#E9F1EF",
        ground_ink_soft="#A9C2C2", accent="#D97B5C",
        bold="#176577",
        interior_ink="#16262A", interior_soft="#5F7276", interior_rule="#BFCFD0",
        tags=("sea", "seaside", "travel", "coast", "holiday", "swim", "ocean"),
    ),
    Palette(
        key="ink", label="Ink — near black and brass",
        ground="#15161A", ground_deep="#0A0B0E",
        panel="#1C1E23", panel_deep="#101215",
        title_ink="#F0EBE1", panel_ink="#B9B3A7", ground_ink="#F0EBE1",
        ground_ink_soft="#9A958C", accent="#C9A227",
        bold="#C9A227",
        interior_ink="#16171B", interior_soft="#6A6B70", interior_rule="#C6C6C9",
        tags=("puzzle", "word", "search", "brain", "logic", "focus", "night", "chess"),
    ),
    Palette(
        key="linen", label="Linen — bone white and slate blue",
        ground="#44505E", ground_deep="#2E3843",
        panel="#F3F0EA", panel_deep="#E6E1D7",
        title_ink="#2F3A46", panel_ink="#5C6672", ground_ink="#F3F1EC",
        ground_ink_soft="#BAC2CB", accent="#7D93A6",
        bold="#4E6E8E",
        interior_ink="#1E242B", interior_soft="#67707A", interior_rule="#C7CBD1",
        tags=("planner", "work", "business", "productivity", "study", "school", "goals", "freelance"),
    ),
    Palette(
        key="plum", label="Plum — aubergine and blush",
        ground="#3B2432", ground_deep="#25151F",
        panel="#F2E6E6", panel_deep="#E4D2D3",
        title_ink="#3B2432", panel_ink="#6A4B57", ground_ink="#F4E9E8",
        ground_ink_soft="#C6ABB4", accent="#D08A76",
        bold="#9C3F62",
        interior_ink="#281922", interior_soft="#71606A", interior_rule="#D2C4C9",
        tags=("self", "love", "care", "woman", "women", "mother", "baby", "grief", "healing"),
    ),
    Palette(
        key="moss", label="Moss — olive and rust",
        ground="#3F4230", ground_deep="#282A1D",
        panel="#EEEADF", panel_deep="#DFD9C9",
        title_ink="#33361F", panel_ink="#5C5F4A", ground_ink="#F0EDE2",
        ground_ink_soft="#C2C2AC", accent="#A8562F",
        bold="#96441F",
        interior_ink="#24251A", interior_soft="#6B6D5C", interior_rule="#C9C8B8",
        tags=("autumn", "harvest", "farm", "countryside", "walking", "hiking", "forest"),
    ),
    Palette(
        key="frost", label="Frost — winter blue and silver",
        ground="#26384A", ground_deep="#15222F",
        panel="#EAEFF3", panel_deep="#D8E1E8",
        title_ink="#1F3040", panel_ink="#51606D", ground_ink="#EDF2F6",
        ground_ink_soft="#A9BCCB", accent="#8FB0C4",
        bold="#2E6C96",
        interior_ink="#1A2530", interior_soft="#5F6D78", interior_rule="#C3CDD5",
        tags=("christmas", "winter", "festive", "snow", "holiday", "advent"),
    ),
    Palette(
        key="citrus", label="Citrus — deep navy and amber",
        ground="#152A3F", ground_deep="#0C1927",
        panel="#F7F2E7", panel_deep="#EDE3D0",
        title_ink="#132639", panel_ink="#4C5A68", ground_ink="#F8F3E8",
        ground_ink_soft="#A9BACA", accent="#E08A1E",
        bold="#D4770E",
        interior_ink="#132639", interior_soft="#5F6E7C", interior_rule="#C6CFD8",
        tags=("energy", "morning", "goals", "focus", "start", "motivation", "fitness", "habit"),
    ),
    Palette(
        key="coral", label="Coral — bone and hot coral",
        ground="#B13A31", ground_deep="#8A2A23",
        panel="#FBF4EE", panel_deep="#F2E4D8",
        title_ink="#7A2019", panel_ink="#6B4A43", ground_ink="#FDF3EE",
        ground_ink_soft="#F0C3B9", accent="#D9472F",
        bold="#D9472F",
        interior_ink="#2A1713", interior_soft="#75584F", interior_rule="#DCC8BD",
        tags=("love", "self", "care", "confidence", "bold", "joy", "creative", "woman", "women"),
    ),
    Palette(
        key="noir", label="Noir — black and chartreuse",
        ground="#101112", ground_deep="#050506",
        panel="#16181A", panel_deep="#0C0D0E",
        title_ink="#E9F5C4", panel_ink="#A8B394", ground_ink="#E9F5C4",
        ground_ink_soft="#8E9A7B", accent="#C6F04A",
        bold="#C6F04A",
        interior_ink="#121314", interior_soft="#66696C", interior_rule="#C4C6C8",
        interior_accent="#6E8A16",
        tags=("puzzle", "sudoku", "logic", "brain", "crossword", "challenge", "games"),
    ),
    Palette(
        key="balm", label="Balm — blush and deep teal",
        ground="#1F5E5B", ground_deep="#123B39",
        panel="#FDF1EA", panel_deep="#F7E0D2",
        title_ink="#17403E", panel_ink="#6B5248", ground_ink="#FDF1EA",
        ground_ink_soft="#A8CBC6", accent="#E2664A",
        bold="#E2664A",
        interior_ink="#20302E", interior_soft="#6F7B78", interior_rule="#CFD9D6",
        tags=("stress", "burnout", "anxiety", "overwhelm", "calm", "healing",
              "selfcare", "self-care", "mental", "wellbeing", "rest", "breathe"),
    ),
    Palette(
        key="bloomy", label="Bloomy — cream and raspberry",
        ground="#7A2447", ground_deep="#551730",
        panel="#FFF4F2", panel_deep="#FBE0DB",
        title_ink="#6B1F3D", panel_ink="#7A5560", ground_ink="#FFF1F3",
        ground_ink_soft="#E0B0C0", accent="#4F8A6B",
        bold="#D94F70",
        interior_ink="#301B24", interior_soft="#7B5F69", interior_rule="#DCC6CE",
        tags=("woman", "women", "self", "love", "care", "confidence", "joy",
              "mother", "daughter", "friendship"),
    ),
    Palette(
        key="sunrise", label="Sunrise — peach and marigold",
        ground="#4A2540", ground_deep="#31182B",
        panel="#FFF3E4", panel_deep="#FCE2C4",
        title_ink="#4A2A12", panel_ink="#7A5B3C", ground_ink="#FFF3E4",
        ground_ink_soft="#D3B49C", accent="#D2552E",
        bold="#E09106",
        interior_ink="#2E1F12", interior_soft="#7A6852", interior_rule="#DDCDB6",
        tags=("morning", "energy", "hope", "gratitude", "thankful", "start",
              "fresh", "motivation", "goals"),
    ),
    Palette(
        key="mint", label="Mint — pale green and teal",
        ground="#14524A", ground_deep="#0C3630",
        panel="#EAF6F0", panel_deep="#D3EBE0",
        title_ink="#10403A", panel_ink="#4F6B63", ground_ink="#EAF6F0",
        ground_ink_soft="#9CC6BC", accent="#F08A5D",
        bold="#12857A",
        interior_ink="#16302B", interior_soft="#5F7169", interior_rule="#C4D8D1",
        tags=("mindfulness", "breathe", "meditation", "clarity", "focus",
              "garden", "growth", "nature", "plant"),
    ),
)

PALETTES_BY_KEY = {p.key: p for p in PALETTES}


def choose_palette(
    words: Iterable[str], rng: StageRandom, override: str | None = None
) -> Palette:
    """The niche's own words pick the colour world; the seed breaks a tie."""
    if override:
        try:
            return PALETTES_BY_KEY[override]
        except KeyError:
            raise KeyError(
                f"unknown palette {override!r}; known: {', '.join(PALETTES_BY_KEY)}"
            ) from None
    haystack = token_set(" ".join(w or "" for w in words))
    scored = [(len(haystack & set(p.tags)), p) for p in PALETTES]
    best = max(score for score, _ in scored)
    if best == 0:
        return rng.stream("palette").choice(PALETTES)
    shortlist = [p for score, p in scored if score == best]
    return shortlist[0] if len(shortlist) == 1 else rng.stream("palette").choice(shortlist)


# ----------------------------------------------------------------- motifs
MOTIFS = ("arc", "waves", "stems", "rays", "dots", "rule")

MOTIF_BY_TAG = {
    "arc": ("calm", "breathe", "mindfulness", "stress", "burnout", "anxiety", "healing"),
    "waves": ("sea", "sleep", "rest", "ocean", "swim", "flow"),
    "stems": ("garden", "nature", "growth", "gratitude", "flower", "plant", "bloom"),
    "rays": ("morning", "energy", "goals", "focus", "start", "fitness"),
    "dots": ("planner", "week", "habit", "track", "puzzle", "grid", "study"),
    "rule": (),  # the quiet one: two hairlines, nothing else
}


def choose_motif(words: Iterable[str], rng: StageRandom, override: str | None = None) -> str:
    if override:
        if override not in MOTIFS:
            raise KeyError(f"unknown motif {override!r}; known: {', '.join(MOTIFS)}")
        return override
    haystack = token_set(" ".join(w or "" for w in words))
    for motif, tags in MOTIF_BY_TAG.items():
        if tags and haystack & set(tags):
            return motif
    return rng.stream("motif").choice(("arc", "stems", "rays", "rule"))


@dataclass
class MotifSpec:
    """Where a motif is drawn and how loud it is, in points."""

    x: float
    y: float
    width: float
    height: float
    color: str
    alpha: float = 0.16
    seed: int = 0
    weight: float = 1.0
    extras: dict = field(default_factory=dict)


def draw_motif(c, motif: str, spec: MotifSpec) -> None:
    """Draw one mark. Vector only — a cover should stay a small file."""
    drawer = {
        "arc": _draw_arc,
        "waves": _draw_waves,
        "stems": _draw_stems,
        "rays": _draw_rays,
        "dots": _draw_dots,
        "rule": _draw_rule,
    }.get(motif, _draw_rule)
    c.saveState()
    c.setStrokeColor(hex_color(spec.color, spec.alpha))
    c.setFillColor(hex_color(spec.color, spec.alpha))
    c.setLineCap(1)
    drawer(c, spec)
    c.restoreState()


def _draw_arc(c, s: MotifSpec) -> None:
    """Concentric half-circles — a horizon, or a held breath."""
    cx = s.x + s.width / 2
    cy = s.y
    rings = 7
    for i in range(rings):
        r = s.width * (0.18 + 0.075 * i)
        c.setLineWidth(max(0.6, s.weight * (1.6 - i * 0.12)))
        c.arc(cx - r, cy - r, cx + r, cy + r, startAng=0, extent=180)


def _draw_waves(c, s: MotifSpec) -> None:
    lines = 9
    for i in range(lines):
        y = s.y + (s.height / lines) * i
        c.setLineWidth(max(0.5, s.weight * 1.1))
        path = c.beginPath()
        steps = 48
        for step in range(steps + 1):
            x = s.x + s.width * step / steps
            offset = math.sin((step / steps) * math.pi * 3 + i * 0.5) * (s.height / lines) * 0.35
            if step == 0:
                path.moveTo(x, y + offset)
            else:
                path.lineTo(x, y + offset)
        c.drawPath(path)


def _draw_stems(c, s: MotifSpec) -> None:
    """Line-art stems: a stalk, and leaves stepping up it."""
    rng = StageRandom(s.seed, "motif/stems")
    stems = 3
    for i in range(stems):
        x = s.x + s.width * (0.2 + 0.3 * i)
        height = s.height * (0.55 + 0.15 * (i % 3))
        c.setLineWidth(max(0.7, s.weight * 1.2))
        c.line(x, s.y, x, s.y + height)
        leaves = 4 + (i % 2)
        for leaf in range(leaves):
            t = (leaf + 1) / (leaves + 1)
            ly = s.y + height * t
            span = s.width * 0.085 * (1 - t * 0.45)
            direction = -1 if (leaf + i) % 2 else 1
            path = c.beginPath()
            path.moveTo(x, ly)
            path.curveTo(x + direction * span * 0.5, ly + span * 0.55,
                         x + direction * span, ly + span * 0.35, x + direction * span, ly)
            path.curveTo(x + direction * span, ly - span * 0.3,
                         x + direction * span * 0.5, ly - span * 0.25, x, ly)
            c.drawPath(path)
        _ = rng


def _draw_rays(c, s: MotifSpec) -> None:
    cx = s.x + s.width / 2
    cy = s.y
    count = 13
    for i in range(count):
        angle = math.pi * (0.08 + 0.84 * i / (count - 1))
        length = s.width * (0.34 if i % 2 else 0.46)
        c.setLineWidth(max(0.5, s.weight))
        c.line(cx, cy, cx + math.cos(angle) * length, cy + math.sin(angle) * length)


def _draw_dots(c, s: MotifSpec) -> None:
    step = s.extras.get("step", 14.0)
    radius = s.extras.get("radius", 1.0)
    rows = int(s.height // step)
    cols = int(s.width // step)
    for row in range(rows + 1):
        for col in range(cols + 1):
            c.circle(s.x + col * step, s.y + row * step, radius, stroke=0, fill=1)


def _draw_rule(c, s: MotifSpec) -> None:
    c.setLineWidth(max(0.7, s.weight * 1.1))
    c.line(s.x, s.y, s.x + s.width, s.y)
    c.setLineWidth(max(0.4, s.weight * 0.5))
    c.line(s.x, s.y - 4, s.x + s.width, s.y - 4)


def gradient_ground(c, x: float, y: float, width: float, height: float,
                    top: str, bottom: str) -> None:
    """A ground with depth, instead of one flat fill."""
    c.saveState()
    path = c.beginPath()
    path.rect(x, y, width, height)
    c.clipPath(path, stroke=0, fill=0)
    c.linearGradient(x, y + height, x, y, (hex_color(top), hex_color(bottom)))
    c.restoreState()


def small_caps(text: str) -> str:
    return " ".join(text.upper())
