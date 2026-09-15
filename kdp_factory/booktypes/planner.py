"""Undated weekly planner: a plan page and a review page for every week.

A planner is repetitive by design — that is the product. So the thing worth
verifying is that it is not *identically* repetitive: every week carries its
own focus line and its own habit set, and the substance gate compares them. A
planner that stamped the same page fifty-two times would fail its own gate.
"""

from __future__ import annotations

from typing import Any

from ..config import EngineConfig
from ..content.packs import load_pack, select_diverse
from ..content.rng import StageRandom
from ..errors import ConfigError
from ..niche import Niche
from ..spec.kdp import KDP_SPEC, even_up
from .base import BookType, ContentUnit, InteriorPlan, PageSpec, TitleProposal, register

TITLE_PATTERNS = (
    "The {topic} Planner",
    "{topic}: The Undated Weekly Planner",
    "The Undated {topic} Planner",
)
SUBTITLE_PATTERNS = (
    "{weeks} Undated Weeks of Planning, Habit Tracking and Review for {audience}",
    "{weeks} Weeks, Undated — Weekly Focus, Habit Tracker and Review for {audience}",
    "An Undated {weeks}-Week Planner for {audience}",
)


@register
class PlannerBookType(BookType):
    key = "planner"
    label = "Undated weekly planner"
    description = "Weekly plan and review spreads with a habit tracker, undated."
    default_options: dict[str, Any] = {
        "target_pages": 120,
        "habits_per_week": 5,
        "monthly_overview": True,
        "months": 12,
        "weeks": None,
    }

    def plan(
        self,
        niche: Niche,
        config: EngineConfig,
        rng: StageRandom,
        options: dict[str, Any] | None = None,
    ) -> InteriorPlan:
        opts = {**self.options_for(niche), **(options or {})}
        trim = niche.constraints.get("trim_size", config.brand.default_trim)
        paper = niche.constraints.get("paper", config.brand.default_paper)
        target_pages = int(niche.constraints.get("target_pages") or opts["target_pages"])
        months = int(opts["months"]) if opts["monthly_overview"] else 0

        weeks = self._solve_layout(target_pages, opts.get("weeks"), months)
        pack = load_pack("planner")
        focus_pool = self._without_used(
            list(pack.get("focus_lines") or []), opts, "weekly focus lines"
        )
        habit_sets = list(pack.get("habit_sets") or [])
        if not focus_pool or not habit_sets:
            raise ConfigError("the planner pack needs focus_lines and habit_sets")
        if len(focus_pool) < weeks:
            raise ConfigError(
                f"{weeks} weeks need {weeks} distinct focus lines but the planner pack "
                f"has {len(focus_pool)}. Add focus lines to planner.yaml or lower target_pages."
            )
        focuses = select_diverse(
            rng.stream("focus").shuffled(focus_pool),
            weeks,
            config.quality.max_pairwise_similarity,
            "weekly focus lines",
        )
        habits_per_week = int(opts["habits_per_week"])
        day_names = list(pack.get("day_names") or [])
        review_questions = list(pack.get("review_questions") or [])

        titles = self.titles(niche, rng)
        pages: list[PageSpec] = self._front_matter(config, titles, pack, weeks)
        content_units: list[ContentUnit] = []

        if months:
            for index, month in enumerate(list(pack.get("month_names") or [])[:months]):
                pages.append(
                    PageSpec(
                        "month_page",
                        kind="content",
                        data={"month": month, "index": index + 1, "day_names": day_names},
                    )
                )

        for week in range(weeks):
            habits = habit_sets[week % len(habit_sets)][:habits_per_week]
            focus = focuses[week]
            pages.append(
                PageSpec(
                    "week_plan_page",
                    kind="content",
                    data={
                        "week": week + 1,
                        "of": weeks,
                        "focus": focus,
                        "day_names": day_names,
                    },
                )
            )
            pages.append(
                PageSpec(
                    "week_review_page",
                    kind="content",
                    data={
                        "week": week + 1,
                        "habits": habits,
                        "day_names": day_names,
                        "review_questions": review_questions,
                    },
                )
            )
            content_units.append(
                ContentUnit(
                    kind="week_focus",
                    text=f"Week {week + 1}: {focus}",
                    page_index=len(pages) - 2,
                    probe=focus,
                )
            )

        pages.extend(self._back_matter(config, pack))
        pages = self._pad_to(pages, max(target_pages, int(KDP_SPEC["page_count"]["min"])))
        pages = self._pad_to_even(pages)

        return InteriorPlan(
            book_type=self.key,
            title=titles.title,
            subtitle=titles.subtitle,
            trim_size=trim,
            paper=paper,
            pages=pages,
            content_units=content_units,
            metadata={
                "weeks": weeks,
                "months": months,
                "habits_per_week": habits_per_week,
                "target_pages": target_pages,
                "title_rationale": titles.rationale,
            },
        )

    def _solve_layout(self, target_pages: int, pinned: int | None, months: int) -> int:
        if pinned:
            return int(pinned)
        front = len(self.front_matter_templates)
        back = len(self.back_matter_templates)
        target = even_up(max(int(target_pages), int(KDP_SPEC["page_count"]["min"])))
        room = target - front - back - months
        weeks = room // 2
        if weeks < 4:
            raise ConfigError(
                f"target of {target_pages} pages leaves room for only {max(weeks, 0)} "
                f"week(s) after front matter, {months} monthly page(s) and back matter"
            )
        return weeks

    def _front_matter(
        self, config: EngineConfig, titles: TitleProposal, pack, weeks: int
    ) -> list[PageSpec]:
        brand = config.brand
        return [
            PageSpec(
                "title_page",
                kind="front_matter",
                data={
                    "title": titles.title,
                    "subtitle": titles.subtitle,
                    "imprint": brand.imprint,
                    "author": brand.author,
                },
                show_page_number=False,
            ),
            PageSpec(
                "copyright_page",
                kind="front_matter",
                data={
                    "lines": [
                        f"Copyright © {brand.copyright_year} {brand.copyright_holder}",
                        "All rights reserved.",
                        "Undated by design: start in any week of any year.",
                        f"Published by {brand.imprint}." + (f" {brand.website}" if brand.website else ""),
                        "First edition.",
                    ]
                },
                show_page_number=False,
            ),
            PageSpec(
                "belongs_to_page",
                kind="front_matter",
                data={
                    "heading": "This planner belongs to",
                    "fields": ["Name", "Started in", "Contact"],
                },
                show_page_number=False,
            ),
            PageSpec(
                "how_to_use_page",
                kind="front_matter",
                data={
                    "heading": "How to use this planner",
                    "intro": f"{weeks} undated weeks: a plan page and a review page for each.",
                    "bullets": list(pack.get("how_to_use") or []),
                },
                show_page_number=False,
            ),
        ]

    def _back_matter(self, config: EngineConfig, pack) -> list[PageSpec]:
        brand = config.brand
        return [
            PageSpec(
                "closing_page",
                kind="back_matter",
                data={
                    "lines": list(pack.get("closing_lines") or []),
                    "imprint": brand.imprint,
                    "also_by": list(brand.also_by),
                    "note": brand.back_matter_note,
                },
                show_page_number=False,
            )
        ]

    def titles(self, niche: Niche, rng: StageRandom) -> TitleProposal:
        stream = rng.stream("titles")
        topic = _topic_from(niche)
        audience = niche.audience_phrase or "anyone who plans a week at a time"
        opts = self.options_for(niche)
        target_pages = int(niche.constraints.get("target_pages") or opts["target_pages"])
        months = int(opts["months"]) if opts["monthly_overview"] else 0
        weeks = self._solve_layout(target_pages, opts.get("weeks"), months)
        return TitleProposal(
            title=stream.choice(TITLE_PATTERNS).format(topic=topic),
            subtitle=stream.stream("sub").choice(SUBTITLE_PATTERNS).format(
                weeks=weeks, audience=audience
            ),
            rationale=f"{weeks} weeks computed from the page plan; topic from the niche",
        )

    def verify(self, plan: InteriorPlan) -> list[tuple[str, bool, str]]:
        plan_pages = sum(1 for p in plan.pages if p.template == "week_plan_page")
        review_pages = sum(1 for p in plan.pages if p.template == "week_review_page")
        focuses = [u.normalized for u in plan.content_units if u.kind == "week_focus"]
        return [
            (
                "every_week_has_both_pages",
                plan_pages == review_pages == len(focuses),
                f"{plan_pages} plan page(s), {review_pages} review page(s), {len(focuses)} week(s)",
            ),
            (
                "week_focus_unique",
                len(set(focuses)) == len(focuses),
                f"{len(set(focuses))} distinct weekly focus lines out of {len(focuses)} weeks",
            ),
            (
                "habits_present",
                all(
                    p.data.get("habits")
                    for p in plan.pages
                    if p.template == "week_review_page"
                ),
                "every review page carries a habit tracker",
            ),
        ]


def _topic_from(niche: Niche) -> str:
    text = niche.niche.strip()
    for separator in (" for ", " per ", " — ", " - ", ":"):
        if separator in text:
            text = text.split(separator)[0].strip()
            break
    lowered = text.lower()
    for noise in ("undated", "weekly", "planner", "journal", "book", "logbook"):
        lowered = lowered.replace(noise, " ")
    cleaned = " ".join(w for w in lowered.split() if w not in {"a", "an", "the"})
    return cleaned.title() if cleaned.strip() else "Weekly"
