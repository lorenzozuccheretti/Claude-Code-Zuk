"""Cover artwork: filled shapes, at full strength.

The engine's first covers drew hairline motifs at 16% opacity. On a search
results page a cover is about 200 pixels tall, and at that size a 16% hairline
is not there at all. Everything here is a filled shape in a saturated colour,
sized as a fraction of the panel, so it still reads when the cover is a
thumbnail.

Each style fills a rectangle and leaves the composition to decide where the
type goes. All of it is vector and deterministic: no bitmaps, small files, same
seed same art.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from ..content.rng import StageRandom
from .design import Palette, contrast, hex_color, mix


def _close(a: str, b: str) -> bool:
    """Two colours too alike to draw one on the other."""
    return contrast(a, b) < 1.6

STYLES = ("arch", "bloom", "strata", "sunburst", "rings", "tiles", "wave")

# Which style suits which niche. The first match wins; anything unmatched is
# chosen from the seed, so two books never collide by accident.
STYLE_BY_TAG: dict[str, tuple[str, ...]] = {
    "arch": ("calm", "breathe", "mindfulness", "stress", "burnout", "anxiety",
             "healing", "sleep", "rest", "quiet"),
    "bloom": ("garden", "nature", "growth", "gratitude", "flower", "plant",
              "bloom", "woman", "women", "love", "care", "mother"),
    "strata": ("journey", "progress", "habit", "step", "hike", "walking",
               "mountain", "landscape", "travel"),
    "sunburst": ("morning", "energy", "goals", "focus", "start", "motivation",
                 "confidence", "joy"),
    "rings": ("cycle", "week", "year", "routine", "practice", "daily"),
    "tiles": ("puzzle", "word", "search", "logic", "brain", "grid", "crossword",
              "planner", "study", "school"),
    "wave": ("sea", "ocean", "swim", "coast", "flow", "music", "song"),
}


@dataclass
class ArtSpec:
    """Where the art goes and what it is drawn in."""

    x: float
    y: float
    width: float
    height: float
    palette: Palette
    field: str                       # the colour it is drawn on top of
    seed: int = 0
    scale: float = 1.0               # overall prominence, 0.5 quiet, 1.2 loud
    # What the shapes are drawn in. Defaults to the palette's bold colour, but
    # art sitting ON the bold colour has to be drawn in something else or it
    # disappears — a tint of bold over bold is not a drawing.
    primary: str = ""
    extras: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.primary:
            bold = self.palette.bold
            same_field = _close(bold, self.field)
            self.primary = self.palette.panel if same_field else bold

    @property
    def cx(self) -> float:
        return self.x + self.width / 2

    @property
    def top(self) -> float:
        return self.y + self.height

    def tint(self, amount: float) -> str:
        """The drawing colour, pulled toward the field it sits on."""
        return mix(self.primary, self.field, amount)


def choose_style(words, rng: StageRandom, override: str | None = None) -> str:
    from ..content.text import token_set

    if override:
        if override not in STYLES:
            raise KeyError(f"unknown art style {override!r}; known: {', '.join(STYLES)}")
        return override
    haystack = token_set(" ".join(w or "" for w in words))
    for style, tags in STYLE_BY_TAG.items():
        if haystack & set(tags):
            return style
    return rng.stream("artwork").choice(STYLES)


def draw(c, style: str, spec: ArtSpec) -> None:
    drawer: Callable[..., None] = {
        "arch": _arch,
        "bloom": _bloom,
        "strata": _strata,
        "sunburst": _sunburst,
        "rings": _rings,
        "tiles": _tiles,
        "wave": _wave,
    }.get(style, _arch)
    c.saveState()
    clip = c.beginPath()
    clip.rect(spec.x, spec.y, spec.width, spec.height)
    c.clipPath(clip, stroke=0, fill=0)
    drawer(c, spec)
    c.restoreState()


def _fill(c, colour: str) -> None:
    c.setFillColor(hex_color(colour))
    c.setStrokeColor(hex_color(colour))


# --------------------------------------------------------------------- styles
def _arch(c, s: ArtSpec) -> None:
    """A portal. Two nested arches, the outer one a tint of the inner."""
    width = s.width * 0.62 * s.scale
    height = min(s.height * 0.86, width * 1.5)
    x0 = s.cx - width / 2
    for index, (inset, colour) in enumerate((
        (0.0, s.tint(0.55)),
        (width * 0.11, s.primary),
    )):
        w = width - inset * 2
        h = height - inset * 1.6
        x = x0 + inset
        y = s.y + (s.height - height) * 0.5 + inset * 0.2
        body = h - w / 2
        _fill(c, colour)
        path = c.beginPath()
        path.moveTo(x, y)
        path.lineTo(x, y + body)
        # From due west, sweeping clockwise over the top to due east.
        path.arcTo(x, y + body - w / 2, x + w, y + body + w / 2, 180, -180)
        path.lineTo(x + w, y)
        path.close()
        c.drawPath(path, stroke=0, fill=1)
        _ = index


def _bloom(c, s: ArtSpec) -> None:
    """A flower head: petals around a centre, two tones."""
    radius = min(s.width, s.height) * 0.42 * s.scale
    cx, cy = s.cx, s.y + s.height * 0.5
    petals = 8
    for layer, (scale, colour, turn) in enumerate((
        (1.0, s.tint(0.45), 0.0),
        (0.68, s.primary, math.pi / petals),
    )):
        _fill(c, colour)
        for index in range(petals):
            angle = turn + 2 * math.pi * index / petals
            length = radius * scale
            half = length * 0.42
            tip_x, tip_y = cx + math.cos(angle) * length, cy + math.sin(angle) * length
            left = angle + math.pi / 2
            path = c.beginPath()
            path.moveTo(cx, cy)
            path.curveTo(cx + math.cos(left) * half, cy + math.sin(left) * half,
                         tip_x + math.cos(left) * half * 0.6,
                         tip_y + math.sin(left) * half * 0.6, tip_x, tip_y)
            path.curveTo(tip_x - math.cos(left) * half * 0.6,
                         tip_y - math.sin(left) * half * 0.6,
                         cx - math.cos(left) * half, cy - math.sin(left) * half, cx, cy)
            path.close()
            c.drawPath(path, stroke=0, fill=1)
        _ = layer
    _fill(c, s.palette.panel if s.field != s.palette.panel else s.palette.ground)
    c.circle(cx, cy, radius * 0.17, stroke=0, fill=1)


def _strata(c, s: ArtSpec) -> None:
    """Layered hills, darkest at the back."""
    rng = StageRandom(s.seed, "art/strata")
    layers = 5
    for index in range(layers):
        t = index / (layers - 1)
        colour = mix(s.tint(0.70), s.primary, t)
        _fill(c, colour)
        # Back to front: each ridge starts lower and rises less, so the layers
        # stay distinguishable instead of collapsing into one silhouette.
        base = s.y + s.height * (0.46 - 0.10 * index)
        crest = base + s.height * (0.34 - 0.03 * index) * s.scale
        offset = (rng.stream(str(index)).randint(-30, 30) / 100.0) * s.width
        path = c.beginPath()
        path.moveTo(s.x, base)
        path.curveTo(s.x + s.width * 0.28 + offset, crest,
                     s.x + s.width * 0.72 + offset, crest * 0.96, s.x + s.width, base)
        path.lineTo(s.x + s.width, s.y)
        path.lineTo(s.x, s.y)
        path.close()
        c.drawPath(path, stroke=0, fill=1)


def _sunburst(c, s: ArtSpec) -> None:
    """Thick wedges from a low centre — the loudest of the set."""
    cx, cy = s.cx, s.y + s.height * 0.08
    length = max(s.width, s.height) * 1.3
    wedges = 11
    for index in range(wedges):
        if index % 2:
            continue
        start = math.pi * (0.02 + 0.96 * index / wedges)
        end = math.pi * (0.02 + 0.96 * (index + 1) / wedges)
        _fill(c, s.primary if index % 4 == 0 else s.tint(0.45))
        path = c.beginPath()
        path.moveTo(cx, cy)
        path.lineTo(cx + math.cos(start) * length, cy + math.sin(start) * length)
        path.lineTo(cx + math.cos(end) * length, cy + math.sin(end) * length)
        path.close()
        c.drawPath(path, stroke=0, fill=1)
    _fill(c, s.primary)
    c.circle(cx, cy, min(s.width, s.height) * 0.09 * s.scale, stroke=0, fill=1)


def _rings(c, s: ArtSpec) -> None:
    """Thick concentric rings, drawn as stroked circles."""
    cx, cy = s.cx, s.y + s.height * 0.5
    outer = min(s.width, s.height) * 0.46 * s.scale
    rings = 4
    for index in range(rings):
        radius = outer * (1 - index * 0.22)
        weight = outer * (0.12 - index * 0.012)
        c.setStrokeColor(hex_color(s.primary if index % 2 == 0 else s.tint(0.45)))
        c.setLineWidth(max(weight, 2.0))
        c.circle(cx, cy, radius, stroke=1, fill=0)


def _tiles(c, s: ArtSpec) -> None:
    """A field of squares and dots — reads as a grid, suits puzzles."""
    rng = StageRandom(s.seed, "art/tiles")
    columns = int(s.extras.get("columns", 6))
    cell = s.width / columns
    rows = max(1, int(s.height / cell))
    for row in range(rows):
        for col in range(columns):
            pick = rng.stream(f"{row}/{col}").randint(0, 9)
            if pick < 3:
                continue
            x = s.x + col * cell
            y = s.y + row * cell
            inset = cell * 0.16
            _fill(c, s.primary if pick > 6 else s.tint(0.5))
            if pick % 2:
                c.circle(x + cell / 2, y + cell / 2, (cell - inset * 2) / 2,
                         stroke=0, fill=1)
            else:
                c.rect(x + inset, y + inset, cell - inset * 2, cell - inset * 2,
                       stroke=0, fill=1)


def _wave(c, s: ArtSpec) -> None:
    """Thick flowing bands."""
    bands = 4
    band_height = s.height / (bands + 1.2)
    for index in range(bands):
        base = s.y + index * band_height * 1.15
        colour = mix(s.tint(0.6), s.primary, index / max(bands - 1, 1))
        _fill(c, colour)
        path = c.beginPath()
        path.moveTo(s.x, base)
        path.curveTo(s.x + s.width * 0.3, base + band_height * 1.5,
                     s.x + s.width * 0.7, base - band_height * 0.6,
                     s.x + s.width, base + band_height * 0.5)
        path.lineTo(s.x + s.width, base + band_height * 0.5 + band_height * 0.62)
        path.curveTo(s.x + s.width * 0.7, base - band_height * 0.6 + band_height * 0.62,
                     s.x + s.width * 0.3, base + band_height * 1.5 + band_height * 0.62,
                     s.x, base + band_height * 0.62)
        path.close()
        c.drawPath(path, stroke=0, fill=1)
