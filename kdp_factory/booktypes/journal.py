"""Guided journal: one prompt per page, ruled writing space, section dividers.

The page count is solved for, not stumbled into: given a target, the type
computes how many prompt pages fit exactly and reports what it could not fit
rather than quietly padding with blanks.
"""

from __future__ import annotations

import math
from typing import Any

from ..config import EngineConfig
from ..content.packs import load_pack, pool_from_banks, select_banks, select_diverse
from ..content.rng import StageRandom
from ..errors import ConfigError
from ..niche import Niche
from ..spec.kdp import KDP_SPEC, even_up
from .base import (
    BookType,
    ContentUnit,
    InteriorPlan,
    PageSpec,
    TitleProposal,
    pick_title,
    register,
)

TITLE_PATTERNS = (
    "The {topic} Journal",
    "{topic}, One Page at a Time",
    "The Daily {topic} Journal",
    "{topic}: A Guided Journal",
    "Pages for {topic}",
)

SUBTITLE_PATTERNS = (
    "{count} Guided Prompts for {audience}",
    "A {count}-Prompt Guided Journal for {audience}",
    "{count} Prompts, One Page a Day — for {audience}",
    "A Guided Journal for {audience}: {count} Prompts with Room to Write",
)


@register
class JournalBookType(BookType):
    key = "journal"
    label = "Guided journal"
    description = "One prompt per page with ruled writing space, grouped into sections."
    default_options: dict[str, Any] = {
        "target_pages": 120,
        "lines_per_page": 13,
        "section_dividers": True,
        "divider_every": 20,
        "date_line": True,
        "prompt_pages": None,  # set this to pin the count instead of the page count
    }

    # ------------------------------------------------------------- planning
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
        divider_every = int(opts["divider_every"]) if opts["section_dividers"] else 0

        prompt_count, dividers = self._solve_layout(
            target_pages=target_pages,
            pinned=opts.get("prompt_pages"),
            divider_every=divider_every,
        )

        pack = load_pack("journal")
        banks = select_banks(
            pack, [niche.niche, niche.audience, niche.promise, *niche.keywords_seed]
        )
        pool = self._without_used(pool_from_banks(pack, banks), opts, "prompts")
        if len(pool) < prompt_count:
            raise ConfigError(
                f"the journal needs {prompt_count} distinct prompts but the selected "
                f"banks ({', '.join(banks)}) only offer {len(pool)}. Add prompts to "
                f"kdp_factory/content/templates/journal.yaml or lower target_pages."
            )
        # The generator holds itself to the same near-duplicate bar the substance
        # gate will apply. Frames multiply cheaply, and "…about your morning…"
        # and "…about your evening…" are one idea, not two.
        prompts = select_diverse(
            rng.stream("prompts").shuffled(pool),
            prompt_count,
            config.quality.max_pairwise_similarity,
            "prompts",
        )
        sections = pack.get("sections") or ["Section"]

        titles = self.named(niche, rng)
        pages: list[PageSpec] = self._front_matter(niche, config, titles, pack, prompt_count)
        content_units: list[ContentUnit] = []

        section_index = 0
        for index, prompt in enumerate(prompts):
            if divider_every and index % divider_every == 0:
                name = sections[section_index % len(sections)]
                section_index += 1
                pages.append(
                    PageSpec(
                        "section_divider",
                        kind="divider",
                        data={
                            "section": name,
                            "index": section_index,
                            "from_prompt": index + 1,
                            "to_prompt": min(index + divider_every, prompt_count),
                        },
                        show_page_number=False,
                    )
                )
            pages.append(
                PageSpec(
                    "prompt_page",
                    kind="content",
                    data={
                        "prompt": prompt,
                        "number": index + 1,
                        "of": prompt_count,
                        "lines": int(opts["lines_per_page"]),
                        "date_line": bool(opts["date_line"]),
                    },
                )
            )
            content_units.append(
                ContentUnit(
                    kind="prompt", text=prompt, page_index=len(pages) - 1, probe=prompt
                )
            )

        pages.extend(self._back_matter(niche, config, pack))
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
                "prompt_count": prompt_count,
                "dividers": dividers,
                "banks": banks,
                "pool_size": len(pool),
                "lines_per_page": int(opts["lines_per_page"]),
                "target_pages": target_pages,
                "title_rationale": titles.rationale,
            },
        )

    def _solve_layout(
        self, target_pages: int, pinned: int | None, divider_every: int
    ) -> tuple[int, int]:
        """How many prompt pages land exactly on the target page count.

        total = front(4) + prompts + dividers + back(1), padded to even.
        Solved by search rather than algebra because the divider count is a
        ceiling function of the prompt count.
        """
        front = len(self.front_matter_templates)
        back = len(self.back_matter_templates)
        if pinned:
            prompts = int(pinned)
            dividers = math.ceil(prompts / divider_every) if divider_every else 0
            return prompts, dividers

        target = even_up(max(int(target_pages), int(KDP_SPEC["page_count"]["min"])))
        best: tuple[int, int] | None = None
        for prompts in range(1, target + 1):
            dividers = math.ceil(prompts / divider_every) if divider_every else 0
            total = front + prompts + dividers + back
            if total > target:
                break
            best = (prompts, dividers)
        if best is None:
            raise ConfigError(
                f"target of {target_pages} pages leaves no room for content after "
                f"{front} front-matter and {back} back-matter pages"
            )
        return best

    # --------------------------------------------------------------- matter
    def _front_matter(
        self, niche: Niche, config: EngineConfig, titles: TitleProposal, pack, prompt_count: int
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
                        "No part of this book may be reproduced in any form without "
                        "written permission from the publisher, except brief quotations "
                        "in a review.",
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
                    "heading": "This journal belongs to",
                    "fields": ["Name", "Started on", "Finished on"],
                },
                show_page_number=False,
            ),
            PageSpec(
                "how_to_use_page",
                kind="front_matter",
                data={
                    "heading": "How to use this journal",
                    "intro": f"{prompt_count} prompts, one per page, in whatever order suits you.",
                    "bullets": list(pack.get("how_to_use") or []),
                },
                show_page_number=False,
            ),
        ]

    def _back_matter(self, niche: Niche, config: EngineConfig, pack) -> list[PageSpec]:
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

    # --------------------------------------------------------------- titles
    def titles(self, niche: Niche, rng: StageRandom) -> TitleProposal:
        stream = rng.stream("titles")
        topic = _topic_from(niche)
        audience = niche.audience_phrase or "anyone who wants to keep the habit"
        opts = self.options_for(niche)
        target_pages = int(niche.constraints.get("target_pages") or opts["target_pages"])
        divider_every = int(opts["divider_every"]) if opts["section_dividers"] else 0
        count, _ = self._solve_layout(target_pages, opts.get("prompt_pages"), divider_every)

        title = pick_title(TITLE_PATTERNS, stream, topic=topic)
        subtitle = stream.stream("sub").choice(SUBTITLE_PATTERNS).format(
            count=count, audience=audience
        )
        return TitleProposal(
            title=title,
            subtitle=subtitle,
            rationale=(
                f"topic '{topic}' taken from the niche, audience from the niche's "
                f"audience field, count computed from the page plan ({count} prompts)"
            ),
        )

    # --------------------------------------------------------------- verify
    def verify(self, plan: InteriorPlan) -> list[tuple[str, bool, str]]:
        prompts = [u for u in plan.content_units if u.kind == "prompt"]
        unique = {u.normalized for u in prompts}
        prompt_pages = sum(1 for p in plan.pages if p.template == "prompt_page")
        return [
            (
                "prompt_pages_match_units",
                prompt_pages == len(prompts),
                f"{prompt_pages} prompt page(s) for {len(prompts)} prompt unit(s)",
            ),
            (
                "prompts_unique",
                len(unique) == len(prompts),
                f"{len(unique)} distinct prompts out of {len(prompts)}",
            ),
            (
                "writing_space",
                all(
                    p.data.get("lines", 0) >= 8
                    for p in plan.pages
                    if p.template == "prompt_page"
                ),
                "every prompt page leaves at least 8 ruled lines to write on",
            ),
        ]


def _topic_from(niche: Niche) -> str:
    """The noun phrase a title is built around, taken from the niche text."""
    text = niche.niche.strip()
    for separator in (" for ", " per ", " — ", " - ", ":"):
        if separator in text:
            text = text.split(separator)[0].strip()
            break
    words = [w for w in text.split() if w.lower() not in {"a", "an", "the"}]
    cleaned = " ".join(words)
    for suffix in (" journal", " notebook", " diary", " book"):
        if cleaned.lower().endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned.title() if cleaned else "Daily Reflection"
