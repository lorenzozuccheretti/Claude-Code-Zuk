"""Sales copy, written once and reused.

The back cover (station 3) and the listing description (station 4) say the same
things, because they are generated from the same niche evidence by the same
code. Improvising the description at the upload screen is exactly what the
checklist warns against.
"""

from __future__ import annotations

from typing import Any

from ..booktypes.base import InteriorPlan
from ..config import Brand
from ..niche import Niche

BENEFIT_BY_TYPE: dict[str, list[str]] = {
    "journal": [
        "One prompt per page — no blank-page paralysis",
        "Room to write, not just room to tick",
        "Undated, so a missed day costs you nothing",
        "Matte cover and a spine that lies open on a table",
    ],
    "planner": [
        "A focus line before the to-do list, so the week has a point",
        "A habit tracker on every review page",
        "Undated: start in any week of any year",
        "Two pages per week — plan on one, review on the other",
    ],
    "puzzle": [
        "Large print grids that do not need a magnifier",
        "Every answer in the back, in puzzle order",
        "Machine-verified: every listed word really is in its grid",
        "One puzzle per page, with room to rest your hand",
    ],
}


def _audience(niche: Niche) -> str:
    return niche.audience_phrase or "anyone who has been looking for this book"


def benefits(plan: InteriorPlan, niche: Niche) -> list[str]:
    """Benefit bullets: the format's own, plus anything the incumbent fails at."""
    out = list(BENEFIT_BY_TYPE.get(plan.book_type, []))
    for gap in niche.competitor.gaps[:2]:
        answer = _answer_to_gap(gap)
        if answer and answer not in out:
            out.append(answer)
    return out[:5]


def _answer_to_gap(gap: str) -> str:
    """Turn a complaint about the incumbent into a claim this book can keep."""
    text = gap.strip().rstrip(".")
    if not text:
        return ""
    lowered = text.lower()
    rules = (
        ("bleed", "Single-sided layout and generous margins"),
        ("thin", "Printed on heavier stock than the usual budget copy"),
        ("small print", "Large, readable type throughout"),
        ("too small", "Large, readable type throughout"),
        ("repetit", "No repeated prompts — every page is different"),
        ("repeat", "No repeated prompts — every page is different"),
        ("blank", "Every page earns its place: no filler"),
        ("filler", "Every page earns its place: no filler"),
        ("binding", "Bound to open flat and stay open"),
        ("no room", "More writing space per page"),
        ("cramped", "More writing space per page"),
        ("date", "Undated, so you can start whenever you like"),
    )
    for needle, claim in rules:
        if needle in lowered:
            return claim
    return f"Addresses a common complaint: {text.lower()}"


def hook(plan: InteriorPlan, niche: Niche) -> str:
    """The first line — the promise, in the buyer's words."""
    if niche.promise.strip():
        return niche.promise.strip()
    return {
        "journal": "A prompt a day, and nothing else to decide.",
        "planner": "A week that knows what it is for.",
        "puzzle": "Large-print puzzles, and every answer in the back.",
    }.get(plan.book_type, "A book that does one thing well.")


def back_cover_copy(
    plan: InteriorPlan, niche: Niche, brand: Brand
) -> dict[str, Any]:
    """The block of text printed on the back panel."""
    unit = _unit_label(plan)
    return {
        "hook": hook(plan, niche),
        "body": (
            f"{plan.title} is built for {_audience(niche)}. "
            f"{unit.capitalize()}, {plan.page_count} pages, "
            f"{plan.trim_size.replace('x', chr(8221) + ' x ')}”."
        ),
        "benefits": benefits(plan, niche),
        "closing": brand.tagline or f"From {brand.imprint}.",
    }


def _unit_label(plan: InteriorPlan) -> str:
    meta = plan.metadata
    if plan.book_type == "journal":
        return f"{meta.get('prompt_count', 0)} guided prompts"
    if plan.book_type == "planner":
        return f"{meta.get('weeks', 0)} undated weeks"
    if plan.book_type == "puzzle":
        return f"{meta.get('puzzle_count', 0)} puzzles with full solutions"
    return f"{len(plan.content_units)} entries"


def description(plan: InteriorPlan, niche: Niche, brand: Brand) -> str:
    """The listing description. Plain text with the light HTML KDP accepts."""
    copy = back_cover_copy(plan, niche, brand)
    bullets = "".join(f"<li>{b}</li>" for b in copy["benefits"])
    inside = _inside_lines(plan)
    return (
        f"<h2>{copy['hook']}</h2>"
        f"<p>{copy['body']}</p>"
        f"<p><b>What's inside</b></p>"
        f"<ul>{''.join(f'<li>{line}</li>' for line in inside)}</ul>"
        f"<p><b>Why this one</b></p>"
        f"<ul>{bullets}</ul>"
        f"<p>{copy['closing']}</p>"
    )


def _inside_lines(plan: InteriorPlan) -> list[str]:
    meta = plan.metadata
    common = [
        f"{plan.page_count} pages, {plan.trim_size.replace('x', ' x ')} inches",
        "Front matter, page numbers and a closing page — nothing missing",
    ]
    if plan.book_type == "journal":
        return [
            f"{meta.get('prompt_count', 0)} guided prompts, one per page",
            f"{meta.get('lines_per_page', 0)} ruled lines on every prompt page",
            f"{meta.get('dividers', 0)} sections to break the book into stages",
            *common,
        ]
    if plan.book_type == "planner":
        return [
            f"{meta.get('weeks', 0)} undated weekly spreads",
            f"A {meta.get('habits_per_week', 0)}-habit tracker on every review page",
            f"{meta.get('months', 0)} monthly overview pages" if meta.get("months") else
            "Weekly focus line on every plan page",
            *common,
        ]
    if plan.book_type == "puzzle":
        return [
            f"{meta.get('puzzle_count', 0)} word search puzzles",
            f"{meta.get('grid_size', 0)} x {meta.get('grid_size', 0)} large-print grids",
            "Complete solutions section, in puzzle order",
            *common,
        ]
    return common
