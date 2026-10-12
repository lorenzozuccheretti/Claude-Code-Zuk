"""Sales copy, written once and reused.

The back cover (station 3) and the listing description (station 4) say the same
things, because they are generated from the same niche evidence by the same
code. Improvising the description at the upload screen is exactly what the
checklist warns against.

The sentences themselves live in the locale table (``locale_<code>.yaml``), so
an Italian book gets Italian copy from the same code path. A niche file's own
``copy:`` block, when present, wins over the generated text field by field:
a press that has written its hook should not have to fight the generator for
it.
"""

from __future__ import annotations

from typing import Any

from ..booktypes.base import InteriorPlan
from ..config import Brand
from ..niche import Niche
from .locale import format_trim, lookup, strings, text

# A gap is research: it may be a pasted review, several sentences long, in a
# stranger's voice. Only a gap that maps to a claim this book can actually keep
# becomes a bullet — an unmatched one is dropped, never reprinted. Putting a
# competitor's one-star review on your own back cover is not marketing.
MAX_BENEFIT_CHARS = 90


def _table(brand: Brand | None) -> dict[str, Any]:
    return strings(brand.language if brand is not None else "english")


def _audience(niche: Niche, table: dict[str, Any]) -> str:
    return niche.audience_phrase or text(table, "copy.audience_default")


def benefits(plan: InteriorPlan, niche: Niche, brand: Brand | None = None) -> list[str]:
    """Benefit bullets: the format's own, plus anything the incumbent fails at.

    A niche that lists its own benefits gets exactly those, in its order.
    """
    if niche.copy.benefits:
        return list(niche.copy.benefits)
    table = _table(brand)
    out = list(lookup(table, "copy.benefits").get(plan.book_type, []))
    for gap in niche.competitor.gaps:
        answer = _answer_to_gap(gap, table)
        if answer and answer not in out and len(answer) <= MAX_BENEFIT_CHARS:
            out.append(answer)
        if len(out) >= 5:
            break
    return out[:5]


def _answer_to_gap(gap: str, table: dict[str, Any] | None = None) -> str:
    """Turn a complaint about the incumbent into a claim this book can keep.

    Returns "" when no rule matches, so the caller drops it. The rules are the
    locale's, because a complaint is matched in the language it was written in.
    """
    text_ = gap.strip().rstrip(".")
    if not text_:
        return ""
    lowered = text_.lower()
    for needle, claim in lookup(table or strings("english"), "copy.gap_rules"):
        if needle in lowered:
            return claim
    # No rule matched. The gap stays in the niche file as evidence; it just does
    # not get printed as a promise nobody wrote.
    return ""


def hook(plan: InteriorPlan, niche: Niche, brand: Brand | None = None) -> str:
    """The first line — the promise, in the buyer's words."""
    if niche.copy.hook:
        return niche.copy.hook
    if niche.promise.strip():
        return niche.promise.strip()
    hooks = lookup(_table(brand), "copy.hook")
    return hooks.get(plan.book_type, hooks["default"])


def back_cover_copy(
    plan: InteriorPlan, niche: Niche, brand: Brand
) -> dict[str, Any]:
    """The block of text printed on the back panel."""
    table = _table(brand)
    unit = _unit_label(plan, table)
    body = niche.copy.body or text(
        table, "copy.body",
        title=plan.title,
        audience=_audience(niche, table),
        # First letter only: str.capitalize() would also lower-case the rest,
        # which is harmless for "109 guided prompts" and wrong for a name.
        unit=unit[:1].upper() + unit[1:],
        pages=plan.page_count,
        trim=format_trim(table, plan.trim_size, "cover"),
    )
    return {
        "hook": hook(plan, niche, brand),
        "body": body,
        "benefits": benefits(plan, niche, brand),
        "closing": niche.copy.closing or brand.tagline
        or text(table, "copy.closing", imprint=brand.imprint),
    }


def _unit_label(plan: InteriorPlan, table: dict[str, Any]) -> str:
    meta = plan.metadata
    units = lookup(table, "copy.unit")
    fields = {
        "prompt_count": meta.get("prompt_count", 0),
        "weeks": meta.get("weeks", 0),
        "puzzle_count": meta.get("puzzle_count", 0),
        "days": meta.get("days", 0),
        "units": len(plan.content_units),
    }
    return units.get(plan.book_type, units["default"]).format(**fields)


def description(plan: InteriorPlan, niche: Niche, brand: Brand) -> str:
    """The listing description. Plain text with the light HTML KDP accepts.

    ``copy.description_paragraphs`` in the niche replaces the body paragraph;
    the inside list and the benefits still come from the book that exists.
    """
    table = _table(brand)
    copy = back_cover_copy(plan, niche, brand)
    bullets = "".join(f"<li>{b}</li>" for b in copy["benefits"])
    inside = _inside_lines(plan, table)
    paragraphs = niche.copy.description_paragraphs or [copy["body"]]
    return (
        f"<h2>{copy['hook']}</h2>"
        + "".join(f"<p>{p}</p>" for p in paragraphs)
        + f"<p><b>{text(table, 'listing.whats_inside')}</b></p>"
        f"<ul>{''.join(f'<li>{line}</li>' for line in inside)}</ul>"
        f"<p><b>{text(table, 'listing.why_this_one')}</b></p>"
        f"<ul>{bullets}</ul>"
        f"<p>{copy['closing']}</p>"
    )


def _inside_lines(plan: InteriorPlan, table: dict[str, Any] | None = None) -> list[str]:
    table = table or strings("english")
    meta = plan.metadata
    fields = {
        "pages": plan.page_count,
        "trim": format_trim(table, plan.trim_size, "listing"),
        "prompt_count": meta.get("prompt_count", 0),
        "lines_per_page": meta.get("lines_per_page", 0),
        "dividers": meta.get("dividers", 0),
        "weeks": meta.get("weeks", 0),
        "habits_per_week": meta.get("habits_per_week", 0),
        "months": meta.get("months", 0),
        "puzzle_count": meta.get("puzzle_count", 0),
        "grid_size": meta.get("grid_size", 0),
        "days": meta.get("days", 0),
        "reference": meta.get("reference_pages", 0),
        "records": meta.get("record_pages", 0),
        "headings": ", ".join(meta.get("record_headings") or []),
    }
    inside = lookup(table, "listing.inside")
    common = [line.format(**fields) for line in lookup(table, "listing.inside_common")]
    lines = [line.format(**fields) for line in inside.get(plan.book_type, [])]
    if plan.book_type == "planner":
        key = "planner_months" if meta.get("months") else "planner_no_months"
        lines.append(inside[key].format(**fields))
    elif plan.book_type == "logbook":
        if not meta.get("weeks"):
            lines = lines[:1]
        if meta.get("months"):
            lines.append(inside["logbook_months"].format(**fields))
        if meta.get("reference_pages"):
            lines.append(inside["logbook_reference"].format(**fields))
        if meta.get("record_pages"):
            lines.append(inside["logbook_records"].format(**fields))
    elif plan.book_type not in inside:
        return common
    return [*lines, *common]
