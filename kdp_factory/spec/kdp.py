"""Every countable thing about a KDP book, computed — never estimated.

Rule from the pack: "use code, not prose, for anything countable". Spine
width, gutter margins, page-count limits, printing cost and the price floor
all live here, driven by ``kdp_spec.yaml``. Renderers and gates import from
this module instead of carrying numbers of their own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..errors import SpecViolation

SPEC_PATH = Path(__file__).with_name("kdp_spec.yaml")

INCH = 72.0  # PostScript points per inch; PDF geometry is in points.


@lru_cache(maxsize=1)
def _load_spec() -> dict[str, Any]:
    with SPEC_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


KDP_SPEC = _load_spec()

PAPER_TYPES = tuple(KDP_SPEC["paper_thickness_in_per_page"])


@dataclass(frozen=True)
class TrimSize:
    """A KDP trim size in inches, plus the same numbers in PDF points."""

    name: str
    width: float
    height: float

    @property
    def width_pt(self) -> float:
        return self.width * INCH

    @property
    def height_pt(self) -> float:
        return self.height * INCH


@lru_cache(maxsize=1)
def _trim_index() -> dict[str, TrimSize]:
    return {
        t["name"]: TrimSize(t["name"], float(t["width"]), float(t["height"]))
        for t in KDP_SPEC["trim_sizes_in"]
    }


def list_trim_sizes() -> list[TrimSize]:
    return list(_trim_index().values())


def trim_size(name: str) -> TrimSize:
    try:
        return _trim_index()[name]
    except KeyError:
        raise SpecViolation(
            f"unknown trim size {name!r}; known sizes: {', '.join(_trim_index())}"
        ) from None


def validate_paper(paper: str) -> str:
    if paper not in PAPER_TYPES:
        raise SpecViolation(
            f"unknown paper type {paper!r}; known types: {', '.join(PAPER_TYPES)}"
        )
    return paper


def validate_page_count(page_count: int, paper: str = "bw_white") -> int:
    """Raise unless the page count is one KDP will actually print."""
    validate_paper(paper)
    pc = KDP_SPEC["page_count"]
    minimum = int(pc["min"])
    maximum = int(pc["max_by_paper"][paper])
    if page_count < minimum:
        raise SpecViolation(
            f"page count {page_count} is below the KDP minimum of {minimum}"
        )
    if page_count > maximum:
        raise SpecViolation(
            f"page count {page_count} exceeds the maximum of {maximum} for {paper}"
        )
    if pc["must_be_even"] and page_count % 2 != 0:
        raise SpecViolation(
            f"page count {page_count} is odd; a printed book has an even number of pages"
        )
    return page_count


def spine_width_in(page_count: int, paper: str = "bw_white") -> float:
    """Spine width in inches: page count times this paper's thickness.

    Always call this with the page count of the interior file that actually
    exists on disk, never with the page count you planned to produce.
    """
    validate_page_count(page_count, paper)
    return page_count * float(KDP_SPEC["paper_thickness_in_per_page"][paper])


def gutter_margin_in(page_count: int) -> float:
    """Inside margin for this page count — it grows with thickness."""
    for band in KDP_SPEC["gutter_margin_in"]:
        if page_count <= int(band["max_pages"]):
            return float(band["margin"])
    raise SpecViolation(f"no gutter margin band covers {page_count} pages")


def outside_margin_in(bleed: bool = False) -> float:
    key = "with_bleed" if bleed else "no_bleed"
    return float(KDP_SPEC["outside_margin_in"][key])


@dataclass(frozen=True)
class CoverGeometry:
    """The full-wrap cover, in inches and points, for one exact page count."""

    trim: TrimSize
    page_count: int
    paper: str
    spine_width: float
    bleed: float
    width: float
    height: float
    safe_margin: float
    spine_text_allowed: bool
    barcode_keepout: tuple[float, float]

    @property
    def width_pt(self) -> float:
        return self.width * INCH

    @property
    def height_pt(self) -> float:
        return self.height * INCH

    @property
    def spine_width_pt(self) -> float:
        return self.spine_width * INCH

    @property
    def back_panel_x(self) -> float:
        """Left edge of the back cover panel, in inches from the wrap origin."""
        return self.bleed

    @property
    def spine_x(self) -> float:
        return self.bleed + self.trim.width

    @property
    def front_panel_x(self) -> float:
        return self.bleed + self.trim.width + self.spine_width

    def pixel_size(self, dpi: int | None = None) -> tuple[int, int]:
        """Raster size at print resolution, for a flattened preview."""
        dpi = dpi or int(KDP_SPEC["cover"]["min_dpi"])
        return (round(self.width * dpi), round(self.height * dpi))

    def as_dict(self) -> dict[str, Any]:
        return {
            "trim_size": self.trim.name,
            "trim_width_in": self.trim.width,
            "trim_height_in": self.trim.height,
            "page_count": self.page_count,
            "paper": self.paper,
            "spine_width_in": round(self.spine_width, 5),
            "bleed_in": self.bleed,
            "wrap_width_in": round(self.width, 5),
            "wrap_height_in": round(self.height, 5),
            "safe_margin_in": self.safe_margin,
            "spine_text_allowed": self.spine_text_allowed,
            "barcode_keepout_in": list(self.barcode_keepout),
            "min_dpi": int(KDP_SPEC["cover"]["min_dpi"]),
            "pixel_size_at_min_dpi": list(self.pixel_size()),
        }


def cover_geometry(
    trim_name: str, page_count: int, paper: str = "bw_white"
) -> CoverGeometry:
    """Full cover wrap geometry for a real page count.

    wrap width  = bleed + back trim + spine + front trim + bleed
    wrap height = bleed + trim height + bleed
    """
    trim = trim_size(trim_name)
    spine = spine_width_in(page_count, paper)
    bleed = float(KDP_SPEC["bleed_in"])
    cover = KDP_SPEC["cover"]
    keepout = cover["barcode_keepout_in"]
    return CoverGeometry(
        trim=trim,
        page_count=page_count,
        paper=paper,
        spine_width=spine,
        bleed=bleed,
        width=2 * trim.width + spine + 2 * bleed,
        height=trim.height + 2 * bleed,
        safe_margin=float(cover["safe_margin_in"]),
        spine_text_allowed=page_count >= int(cover["spine_text_min_pages"]),
        barcode_keepout=(float(keepout["width"]), float(keepout["height"])),
    )


def _money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def printing_cost_usd(page_count: int, paper: str = "bw_white") -> Decimal:
    """What Amazon charges to print one copy, on the US marketplace."""
    validate_page_count(page_count, paper)
    cfg = KDP_SPEC["printing_cost"]
    plan = cfg["plans"][paper]
    threshold = int(cfg["low_page_threshold"])
    if page_count <= threshold and plan.get("low_fixed") is not None:
        return _money(plan["low_fixed"])
    cost = float(plan["fixed"]) + page_count * float(plan["per_page"])
    return _money(cost)


def min_list_price_usd(
    page_count: int, paper: str = "bw_white", expanded: bool = False
) -> Decimal:
    """Lowest list price whose royalty is not negative, rounded up to a cent."""
    rate = Decimal(
        str(
            KDP_SPEC["royalty"]["expanded_rate" if expanded else "standard_rate"]
        )
    )
    cost = printing_cost_usd(page_count, paper)
    step = Decimal(str(KDP_SPEC["royalty"]["price_rounding_step"]))
    raw = cost / rate
    return (raw / step).quantize(Decimal("1"), rounding=ROUND_CEILING) * step


def royalty_usd(
    list_price: float | Decimal,
    page_count: int,
    paper: str = "bw_white",
    expanded: bool = False,
) -> Decimal:
    """Royalty per copy = rate * list price - printing cost."""
    rate = Decimal(
        str(
            KDP_SPEC["royalty"]["expanded_rate" if expanded else "standard_rate"]
        )
    )
    price = Decimal(str(list_price))
    return _money(rate * price - printing_cost_usd(page_count, paper))


def even_up(page_count: int) -> int:
    """Round a page count up to the next even number."""
    return page_count if page_count % 2 == 0 else page_count + 1


def sheets_for(page_count: int) -> int:
    """How many physical leaves the page count implies."""
    return math.ceil(page_count / 2)


def listing_limits() -> dict[str, int]:
    return dict(KDP_SPEC["listing_limits"])


def spec_card_text() -> str:
    """The spec card as a human-readable block — `kdp spec` prints this."""
    limits = listing_limits()
    thickness = KDP_SPEC["paper_thickness_in_per_page"]
    lines = [
        f"KDP SPEC CARD  (spec_version {KDP_SPEC['spec_version']}, "
        f"last verified {KDP_SPEC['last_verified']}, {KDP_SPEC['marketplace']})",
        "",
        "SPINE       spine_in = page_count x thickness",
        *[f"              {name:<15} {value}" for name, value in thickness.items()],
        f"BLEED       {KDP_SPEC['bleed_in']} in on top, bottom and outer edges",
        "COVER WRAP  width = 2 x trim_w + spine + 2 x bleed;  "
        "height = trim_h + 2 x bleed",
        f"            safe margin {KDP_SPEC['cover']['safe_margin_in']} in; "
        f"barcode keep-out {KDP_SPEC['cover']['barcode_keepout_in']['width']}"
        f" x {KDP_SPEC['cover']['barcode_keepout_in']['height']} in; "
        f"min {KDP_SPEC['cover']['min_dpi']} DPI",
        f"            spine text needs >= "
        f"{KDP_SPEC['cover']['spine_text_min_pages']} pages",
        "GUTTER      "
        + ", ".join(
            f"<={b['max_pages']}p: {b['margin']}\"" for b in KDP_SPEC["gutter_margin_in"]
        ),
        f"OUTSIDE     {KDP_SPEC['outside_margin_in']['no_bleed']}\" no bleed / "
        f"{KDP_SPEC['outside_margin_in']['with_bleed']}\" with bleed",
        "PAGES       min "
        + str(KDP_SPEC["page_count"]["min"])
        + ", max "
        + ", ".join(
            f"{k}:{v}" for k, v in KDP_SPEC["page_count"]["max_by_paper"].items()
        )
        + ", must be even",
        f"LISTING     title+subtitle <= {limits['title_plus_subtitle_chars']} chars, "
        f"description <= {limits['description_chars']}, "
        f"{limits['keyword_slots']} keywords x {limits['keyword_chars']} chars, "
        f"{limits['categories']} categories",
        f"PRICE       min list = printing cost / "
        f"{KDP_SPEC['royalty']['standard_rate']} (standard royalty)",
        "",
        f"Source: {KDP_SPEC['source']}",
        "Retailer specs change. Verify against KDP's current documentation "
        "before a launch; edit kdp_factory/spec/kdp_spec.yaml when they do.",
    ]
    return "\n".join(lines)
