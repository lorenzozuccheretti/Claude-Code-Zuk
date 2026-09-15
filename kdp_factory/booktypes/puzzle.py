"""Word search book: puzzle pages, then a solutions section.

The interesting property of this type is that correctness is decidable. The
generator refuses to drop a word, and ``verify`` re-derives the answer from the
grid instead of trusting the solution key it was handed.
"""

from __future__ import annotations

import math
from typing import Any

from ..config import EngineConfig
from ..content.packs import load_pack, tokens
from ..content.text import normalize
from ..content.rng import StageRandom
from ..content.wordsearch import Puzzle, generate_puzzle, verify_puzzle
from ..errors import ConfigError
from ..niche import Niche
from ..spec.kdp import KDP_SPEC, even_up
from .base import BookType, ContentUnit, InteriorPlan, PageSpec, TitleProposal, register

TITLE_PATTERNS = (
    "{topic} Word Search",
    "The {topic} Word Search Book",
    "Word Search: {topic}",
)
SUBTITLE_PATTERNS = (
    "{count} Large-Print Puzzles for {audience}",
    "{count} Puzzles with Full Solutions — for {audience}",
    "{count} Large-Print Word Searches for {audience}, with Answers",
)


@register
class WordSearchBookType(BookType):
    key = "puzzle"
    label = "Word search puzzle book"
    description = "Large-print word search puzzles with a verified solutions section."
    default_options: dict[str, Any] = {
        "target_pages": 120,
        "grid_size": 15,
        "words_per_puzzle": 14,
        "difficulty": "medium",
        "solutions": True,
        "solutions_per_page": 2,
        "puzzle_count": None,
    }
    MIN_WORDS_PER_PUZZLE = 8
    back_matter_templates = ("closing_page",)

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
        per_solution_page = max(1, int(opts["solutions_per_page"]))
        with_solutions = bool(opts["solutions"])

        puzzle_count = self._solve_layout(
            target_pages=target_pages,
            pinned=opts.get("puzzle_count"),
            per_solution_page=per_solution_page,
            with_solutions=with_solutions,
        )

        schedule = self._theme_schedule(niche, rng, puzzle_count, opts)
        size = int(opts["grid_size"])
        per_puzzle = int(opts["words_per_puzzle"])

        titles = self.titles(niche, rng)
        pages: list[PageSpec] = self._front_matter(config, titles, puzzle_count, size)
        content_units: list[ContentUnit] = []
        puzzles: list[Puzzle] = []

        for index, (theme, occurrence, uses) in enumerate(schedule):
            words = self._words_for(theme, occurrence, uses, per_puzzle, size, rng)
            puzzle = generate_puzzle(
                number=index + 1,
                theme=self._theme_label(theme, occurrence, uses),
                words=words,
                size=size,
                rng=rng,
                difficulty=str(opts["difficulty"]),
            )
            puzzles.append(puzzle)
            pages.append(
                PageSpec(
                    "puzzle_page",
                    kind="content",
                    data={
                        "number": puzzle.number,
                        "theme": puzzle.theme,
                        "size": puzzle.size,
                        "grid": ["".join(row) for row in puzzle.grid],
                        "words": puzzle.words,
                    },
                )
            )
            content_units.append(
                ContentUnit(
                    kind="puzzle",
                    text=f"{puzzle.theme}: {', '.join(puzzle.words)}",
                    page_index=len(pages) - 1,
                    probe=puzzle.theme,
                )
            )

        if with_solutions:
            pages.append(
                PageSpec(
                    "section_divider",
                    kind="divider",
                    data={"section": "Solutions", "index": 1},
                    show_page_number=False,
                )
            )
            for start in range(0, puzzle_count, per_solution_page):
                batch = puzzles[start : start + per_solution_page]
                pages.append(
                    PageSpec(
                        "solution_page",
                        kind="content",
                        data={
                            "solutions": [
                                {
                                    "number": p.number,
                                    "theme": p.theme,
                                    "size": p.size,
                                    "grid": ["".join(row) for row in p.grid],
                                    "placements": [pl.as_dict() for pl in p.placements],
                                }
                                for p in batch
                            ]
                        },
                    )
                )

        pages.extend(self._back_matter(config))
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
                "puzzle_count": puzzle_count,
                "grid_size": size,
                "words_per_puzzle": per_puzzle,
                "difficulty": opts["difficulty"],
                "solutions": with_solutions,
                "themes": [
                    self._theme_label(theme, occurrence, uses)
                    for theme, occurrence, uses in schedule
                ],
                "puzzles": [p.as_dict() for p in puzzles],
                "target_pages": target_pages,
                "title_rationale": titles.rationale,
            },
        )

    def _solve_layout(
        self, target_pages: int, pinned: int | None, per_solution_page: int, with_solutions: bool
    ) -> int:
        if pinned:
            return int(pinned)
        front = len(self.front_matter_templates)
        back = len(self.back_matter_templates)
        target = even_up(max(int(target_pages), int(KDP_SPEC["page_count"]["min"])))
        best = 0
        for count in range(1, target + 1):
            solution_pages = (
                math.ceil(count / per_solution_page) + 1 if with_solutions else 0
            )
            total = front + count + solution_pages + back
            if total > target:
                break
            best = count
        if best == 0:
            raise ConfigError(
                f"target of {target_pages} pages leaves no room for puzzles once "
                f"front matter, solutions and back matter are accounted for"
            )
        return best

    def _theme_schedule(
        self,
        niche: Niche,
        rng: StageRandom,
        needed: int,
        options: dict[str, Any] | None = None,
    ) -> list[tuple[dict[str, Any], int, int]]:
        """Which theme each puzzle uses, and which slice of its words.

        Themes relevant to the niche come first, then the rest in a seeded
        order. When a book needs more puzzles than there are themes, a theme
        comes round again — but with a DISJOINT slice of its word list, and far
        away from its first appearance, so no two puzzles share a word list.
        """
        pack = load_pack("wordlists")
        all_themes = list(pack.get("themes") or [])
        if not all_themes:
            raise ConfigError("the wordlists pack contains no themes")
        words = tokens(niche.niche, niche.audience, niche.promise, *niche.keywords_seed)
        relevant = [
            t for t in all_themes if words & {tag.lower() for tag in (t.get("tags") or [])}
        ]
        rest = [t for t in all_themes if t not in relevant]
        ordered = relevant + rng.stream("themes").shuffled(rest)

        # A theme a previous run already used is dropped whole: the same grid
        # theme in two books reads as a reprint even with different words.
        avoid = (options or {}).get("avoid") or set()
        if avoid:
            ordered = [
                t for t in ordered
                if not any(normalize(t["name"]) in used for used in avoid)
            ]
            if len(ordered) * 2 < needed:
                raise ConfigError(
                    f"{needed} puzzles need more themes than are left after excluding "
                    f"the ones previous runs used. Add themes to wordlists.yaml."
                )

        schedule: list[tuple[dict[str, Any], int, int]] = []
        uses_by_theme: dict[str, int] = {}
        for index in range(needed):
            theme = ordered[index % len(ordered)]
            occurrence = index // len(ordered)
            uses_by_theme[theme["name"]] = occurrence + 1
            schedule.append((theme, occurrence, 0))
        return [
            (theme, occurrence, uses_by_theme[theme["name"]])
            for theme, occurrence, _ in schedule
        ]

    def _words_for(
        self,
        theme: dict[str, Any],
        occurrence: int,
        uses: int,
        per_puzzle: int,
        size: int,
        rng: StageRandom,
    ) -> list[str]:
        """A slice of this theme's words that no other puzzle in the book uses."""
        available = [w for w in theme["words"] if len(w) <= size]
        per_slice = min(int(per_puzzle), len(available) // max(1, uses))
        if per_slice < self.MIN_WORDS_PER_PUZZLE:
            raise ConfigError(
                f"theme {theme['name']!r} would need {uses} disjoint word sets of at "
                f"least {self.MIN_WORDS_PER_PUZZLE} words, but only {len(available)} of "
                f"its words fit a {size}x{size} grid. Add words to wordlists.yaml, add "
                f"more themes, or lower target_pages."
            )
        shuffled = rng.stream(f"words/{theme['name']}").shuffled(available)
        return shuffled[occurrence * per_slice : (occurrence + 1) * per_slice]

    def _theme_label(self, theme: dict[str, Any], occurrence: int, uses: int) -> str:
        return theme["name"] if uses == 1 else f"{theme['name']} (Part {occurrence + 1})"

    def _front_matter(
        self, config: EngineConfig, titles: TitleProposal, puzzle_count: int, size: int
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
                        "Puzzles and solutions generated and verified by the publisher.",
                        f"Published by {brand.imprint}." + (f" {brand.website}" if brand.website else ""),
                        "First edition.",
                    ]
                },
                show_page_number=False,
            ),
            PageSpec(
                "belongs_to_page",
                kind="front_matter",
                data={"heading": "This book belongs to", "fields": ["Name", "Started on"]},
                show_page_number=False,
            ),
            PageSpec(
                "how_to_use_page",
                kind="front_matter",
                data={
                    "heading": "How to use this book",
                    "intro": f"{puzzle_count} puzzles on {size}x{size} grids, with every answer in the back.",
                    "bullets": [
                        "Words run across, down and diagonally — and some run backwards.",
                        "Every word in the list is in the grid. If you cannot find one, it is there.",
                        "Solutions start after the last puzzle, in puzzle order.",
                        "One puzzle per sitting is plenty. The book is not going anywhere.",
                    ],
                },
                show_page_number=False,
            ),
        ]

    def _back_matter(self, config: EngineConfig) -> list[PageSpec]:
        brand = config.brand
        return [
            PageSpec(
                "closing_page",
                kind="back_matter",
                data={
                    "lines": [
                        "That is every puzzle in this book.",
                        "Every grid was checked by machine: each listed word appears, and every solution was re-derived from the grid itself.",
                    ],
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
        audience = niche.audience_phrase or "puzzle lovers"
        opts = self.options_for(niche)
        target_pages = int(niche.constraints.get("target_pages") or opts["target_pages"])
        count = self._solve_layout(
            target_pages,
            opts.get("puzzle_count"),
            max(1, int(opts["solutions_per_page"])),
            bool(opts["solutions"]),
        )
        return TitleProposal(
            title=stream.choice(TITLE_PATTERNS).format(topic=topic),
            subtitle=stream.stream("sub").choice(SUBTITLE_PATTERNS).format(
                count=count, audience=audience
            ),
            rationale=f"puzzle count {count} computed from the page plan, topic from the niche",
        )

    def verify(self, plan: InteriorPlan) -> list[tuple[str, bool, str]]:
        """Re-derive every answer from the grid. Trust nothing, check everything."""
        results: list[tuple[str, bool, str]] = []
        puzzles = [Puzzle.from_dict(p) for p in plan.metadata.get("puzzles", [])]
        page_puzzles = sum(1 for p in plan.pages if p.template == "puzzle_page")
        results.append(
            (
                "puzzle_pages_match",
                page_puzzles == len(puzzles),
                f"{page_puzzles} puzzle page(s) for {len(puzzles)} generated puzzle(s)",
            )
        )
        failures: list[str] = []
        for puzzle in puzzles:
            for name, passed, reason in verify_puzzle(puzzle):
                if not passed:
                    failures.append(f"{name}: {reason}")
        results.append(
            (
                "every_word_verified",
                not failures,
                f"all {len(puzzles)} grids re-verified against their word lists and solutions"
                if not failures
                else f"{len(failures)} puzzle defect(s): {failures[0]}",
            )
        )
        if plan.metadata.get("solutions"):
            solution_pages = sum(1 for p in plan.pages if p.template == "solution_page")
            results.append(
                (
                    "solutions_present",
                    solution_pages > 0,
                    f"{solution_pages} solution page(s) for {len(puzzles)} puzzles",
                )
            )
        return results


def _topic_from(niche: Niche) -> str:
    text = niche.niche.strip()
    for separator in (" for ", " per ", " — ", " - ", ":"):
        if separator in text:
            text = text.split(separator)[0].strip()
            break
    lowered = text.lower()
    for noise in ("word search", "wordsearch", "puzzle book", "puzzles", "puzzle", "book"):
        lowered = lowered.replace(noise, " ")
    cleaned = " ".join(w for w in lowered.split() if w not in {"a", "an", "the"})
    return cleaned.title() if cleaned.strip() else "Large Print"
