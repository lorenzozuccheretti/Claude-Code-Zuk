"""Book types: page counts land on purpose, content is distinct, puzzles are correct."""

from __future__ import annotations

import pytest

from kdp_factory.booktypes import available, get_book_type
from kdp_factory.content.rng import StageRandom, derive_seed
from kdp_factory.content.wordsearch import (
    PuzzleGenerationError,
    generate_puzzle,
    verify_puzzle,
)
from kdp_factory.errors import ConfigError
from kdp_factory.niche import niche_from_dict


def plan_for(book_type: str, config, target_pages: int = 120, options=None,
             **constraints):
    niche = niche_from_dict(
        {
            "niche": {
                "journal": "Gratitude journal for new mothers",
                "planner": "Undated weekly planner for freelancers",
                "puzzle": "Large print word search for seniors",
            }[book_type],
            "book_type": book_type,
            "audience": "readers who bought the last one",
            "keywords_seed": ["one", "two", "three"],
            "constraints": {"target_pages": target_pages, **constraints},
        }
    )
    return niche, get_book_type(book_type).plan(
        niche, config, StageRandom(derive_seed(book_type, 1), "interior"),
        options=options,
    )


class TestRegistry:
    def test_three_types_are_registered(self):
        assert {b.key for b in available()} == {"journal", "planner", "puzzle"}

    def test_unknown_type_names_the_alternatives(self):
        with pytest.raises(ConfigError, match="registered types"):
            get_book_type("colouring")


@pytest.mark.parametrize("book_type", ["journal", "planner", "puzzle"])
class TestEveryBookType:
    def test_page_count_lands_on_the_target(self, book_type, config):
        _, plan = plan_for(book_type, config, target_pages=120)
        assert plan.page_count == 120

    def test_page_count_is_even(self, book_type, config):
        _, plan = plan_for(book_type, config, target_pages=118)
        assert plan.page_count % 2 == 0

    def test_content_units_are_distinct(self, book_type, config):
        _, plan = plan_for(book_type, config)
        normalized = [u.normalized for u in plan.content_units]
        assert len(set(normalized)) == len(normalized)

    def test_front_and_back_matter_exist(self, book_type, config):
        _, plan = plan_for(book_type, config)
        assert plan.front_matter_pages >= 2
        assert plan.back_matter_pages >= 1

    def test_own_verification_passes(self, book_type, config):
        _, plan = plan_for(book_type, config)
        failures = [c for c in get_book_type(book_type).verify(plan) if not c[1]]
        assert failures == []

    def test_planning_is_deterministic(self, book_type, config):
        _, first = plan_for(book_type, config)
        _, second = plan_for(book_type, config)
        assert first.as_dict() == second.as_dict()


class TestJournal:
    def test_refuses_to_pad_with_repeats(self, config):
        """A book the pack cannot fill is an error, not a book full of repeats."""
        with pytest.raises(ConfigError, match="prompts|overlapping"):
            plan_for("journal", config, target_pages=700)

    def test_prompt_count_appears_in_the_subtitle(self, config):
        _, plan = plan_for("journal", config)
        assert str(plan.metadata["prompt_count"]) in plan.subtitle

    def test_every_prompt_page_has_writing_space(self, config):
        _, plan = plan_for("journal", config)
        pages = [p for p in plan.pages if p.template == "prompt_page"]
        assert pages and all(p.data["lines"] >= 8 for p in pages)


class TestPlanner:
    def test_each_week_gets_a_plan_and_a_review_page(self, config):
        _, plan = plan_for("planner", config)
        weeks = plan.metadata["weeks"]
        assert sum(1 for p in plan.pages if p.template == "week_plan_page") == weeks
        assert sum(1 for p in plan.pages if p.template == "week_review_page") == weeks

    def test_focus_lines_are_not_recycled(self, config):
        _, plan = plan_for("planner", config)
        focuses = [u.text for u in plan.content_units]
        assert len(set(focuses)) == len(focuses)


class TestPuzzle:
    def test_solutions_section_exists(self, config):
        _, plan = plan_for("puzzle", config)
        assert any(p.template == "solution_page" for p in plan.pages)

    @staticmethod
    def _leaving(count: int) -> dict:
        """Options that hide every theme but `count` of them.

        The scarcity is constructed rather than borrowed from the pack's size,
        so adding themes to wordlists.yaml cannot quietly stop these two tests
        exercising anything.
        """
        import yaml

        from kdp_factory.content.text import normalize

        themes = yaml.safe_load(
            open("kdp_factory/content/templates/wordlists.yaml"))["themes"]
        hidden = [t["name"] for t in themes[count:]]
        return {"avoid": {frozenset({normalize(name)}) for name in hidden}}

    def test_reused_themes_never_share_a_word_list(self, config):
        """More puzzles than themes is fine — sharing a word list is not."""
        _, plan = plan_for("puzzle", config, target_pages=120, trim_size="8.5x11",
                           options=self._leaving(40))
        themes = plan.metadata["themes"]
        word_sets = [frozenset(p["words"]) for p in plan.metadata["puzzles"]]
        assert len(word_sets) > len(set(t.split(" (Part")[0] for t in themes))
        assert len(set(word_sets)) == len(word_sets)

    def test_refuses_when_the_themes_would_be_stretched_too_thin(self, config):
        """Two slices of twenty words is the most a theme can give."""
        with pytest.raises(ConfigError, match="disjoint word sets|more themes than"):
            plan_for("puzzle", config, target_pages=120, trim_size="8.5x11",
                     options=self._leaving(20))


class TestWordSearchAlgorithm:
    def test_generated_puzzle_verifies(self):
        puzzle = generate_puzzle(
            1, "Garden", ["ROSE", "TULIP", "SPADE", "COMPOST", "MULCH", "HEDGE"],
            12, StageRandom(1, "t"),
        )
        assert all(passed for _, passed, _ in verify_puzzle(puzzle))

    def test_a_word_that_cannot_fit_is_refused(self):
        with pytest.raises(PuzzleGenerationError, match="do not fit"):
            generate_puzzle(1, "Long", ["EXTRAORDINARILYLONGWORD"], 10, StageRandom(1, "t"))

    def test_verification_catches_a_tampered_grid(self):
        """The verifier must not trust the generator that made the grid."""
        puzzle = generate_puzzle(
            1, "Garden", ["ROSE", "TULIP", "SPADE", "COMPOST", "MULCH", "HEDGE"],
            12, StageRandom(2, "t"),
        )
        placement = puzzle.placements[0]
        row, col = placement.cells()[0]
        puzzle.grid[row][col] = "Z" if puzzle.grid[row][col] != "Z" else "Q"

        results = dict((name, passed) for name, passed, _ in verify_puzzle(puzzle))
        assert not results["p1_solution_correct"]

    def test_generation_is_reproducible(self):
        words = ["ROSE", "TULIP", "SPADE", "COMPOST", "MULCH", "HEDGE"]
        a = generate_puzzle(1, "Garden", words, 12, StageRandom(5, "t"))
        b = generate_puzzle(1, "Garden", words, 12, StageRandom(5, "t"))
        assert a.as_dict() == b.as_dict()


class TestTemplatePacks:
    """Frames multiply cheaply, and a frame that fits only some subjects
    produces prompts no human wrote and no reader would ask."""

    def test_a_frame_can_restrict_its_own_subjects(self):
        from kdp_factory.content.packs import expand_bank

        bank = {
            "subjects": ["your hands", "a tension you are carrying"],
            "frames": [
                "Describe {subject}.",
                {"text": "Where in your body is {subject}?",
                 "subjects": ["a tension you are carrying"]},
            ],
        }
        lines = expand_bank(bank)
        assert "Describe your hands." in lines
        assert "Where in your body is your hands?" not in lines
        assert "Where in your body is a tension you are carrying?" in lines

    def test_a_plain_string_frame_still_takes_every_subject(self):
        from kdp_factory.content.packs import expand_bank

        lines = expand_bank({"subjects": ["a", "b"], "frames": ["Describe {subject}."]})
        assert lines == ["Describe a.", "Describe b."]

    def test_no_bank_pairs_a_singular_frame_with_a_plural_subject(self):
        """'…be exactly as it is?' breaks on a plural subject like 'your hands'."""
        from kdp_factory.content.packs import expand_bank, load_pack

        pack = load_pack("journal")
        offenders = []
        for name, bank in pack["banks"].items():
            for line in expand_bank(bank):
                if " as it is" in line and (" hands " in line or " your hands" in line):
                    offenders.append((name, line))
        assert offenders == []


class TestTitles:
    """A title that repeats a word reads as generated, because it was."""

    @pytest.mark.parametrize("book_type,niche_name", [
        ("planner", "Undated weekly planner for freelancers"),
        ("journal", "Gratitude journal for new mothers"),
        ("puzzle", "Large print word search for seniors"),
        ("planner", "Weekly planner for weekly planning"),
        ("journal", "Journal for people who journal"),
    ])
    def test_no_title_repeats_a_word(self, book_type, niche_name, config):
        from kdp_factory.booktypes.base import TITLE_STOPWORDS
        from kdp_factory.content.text import normalize
        from kdp_factory.niche import niche_from_dict

        niche = niche_from_dict({
            "niche": niche_name, "book_type": book_type,
            "audience": "readers", "constraints": {"target_pages": 120},
        })
        for seed in range(6):
            proposal = get_book_type(book_type).titles(
                niche, StageRandom(derive_seed(niche.slug, book_type, seed), "interior"))
            words = [w for w in normalize(proposal.title).split() if w not in TITLE_STOPWORDS]
            assert len(words) == len(set(words)), f"seed {seed}: {proposal.title!r}"

    def test_pick_title_falls_back_rather_than_failing(self):
        from kdp_factory.booktypes.base import pick_title

        # Every pattern repeats; it still has to return something.
        title = pick_title(("{topic} {topic}",), StageRandom(1, "t"), topic="Echo")
        assert title == "Echo Echo"


class TestFitSize:
    def test_a_word_wider_than_the_measure_shrinks_the_type(self):
        """It never raised the line count, so it used to overflow silently."""
        from kdp_factory.render.layout import fit_size, wrap
        from kdp_factory.render.typography import font
        from reportlab.pdfbase import pdfmetrics

        face = font("poster", "bold")
        text = "Extraordinarily Long Compound Title"
        width = 200.0
        size = fit_size(text, face, width, 60, 8, 3)
        lines = wrap(text, face, size, width)
        assert all(pdfmetrics.stringWidth(line, face, size) <= width for line in lines)


class TestNicheNamesTheBook:
    """A press that has settled on a name should not re-roll a seed to keep it."""

    def _plan(self, niche_dict, seed=3, **overrides):
        from kdp_factory.booktypes import get_book_type
        from kdp_factory.config import EngineConfig
        from kdp_factory.content.rng import StageRandom
        from kdp_factory.niche import niche_from_dict

        niche_dict = dict(niche_dict)
        niche_dict.update(overrides)
        niche = niche_from_dict(niche_dict)
        book = get_book_type(niche.book_type)
        return book.plan(niche, EngineConfig(), StageRandom(seed, "interior"))

    def test_a_niche_can_name_the_book(self, niche_dict):
        plan = self._plan(niche_dict, title="The Daily Mental Health Journal",
                          subtitle="Guided Journal for women running on empty")
        assert plan.title == "The Daily Mental Health Journal"
        assert plan.subtitle == "Guided Journal for women running on empty"
        assert "set by the niche file" in plan.metadata["title_rationale"]

    def test_the_name_survives_a_change_of_seed(self, niche_dict):
        """The whole point: the same book, whatever the seed rolls."""
        pinned = {"title": "The Daily Gratitude Journal",
                  "subtitle": "109 Prompts for first-time mothers"}
        names = {(plan.title, plan.subtitle) for plan in
                 (self._plan(niche_dict, seed=s, **pinned) for s in (1, 2, 7))}
        assert names == {(pinned["title"], pinned["subtitle"])}
        # Without the pin the same seeds disagree — that is what it is fixing.
        assert len({self._plan(niche_dict, seed=s).subtitle for s in (1, 2, 7)}) > 1

    def test_each_half_is_taken_on_its_own(self, niche_dict):
        """Pinning the title must not throw away a subtitle that carries the count."""
        plan = self._plan(niche_dict, title="The Daily Gratitude Journal")
        assert plan.title == "The Daily Gratitude Journal"
        assert plan.subtitle
        assert str(len([u for u in plan.content_units if u.kind == "prompt"])) in plan.subtitle

    def test_an_unnamed_niche_still_gets_a_proposal(self, niche_dict):
        plan = self._plan(niche_dict)
        assert plan.title and plan.subtitle
        assert "set by the niche file" not in plan.metadata["title_rationale"]

    def test_a_name_too_long_for_kdp_is_stopped_at_the_listing(self, niche_dict, config):
        """The override is not a way past the character limit."""
        from kdp_factory.errors import KdpFactoryError
        from kdp_factory.niche import niche_from_dict
        from kdp_factory.run.pipeline import Pipeline

        niche_dict = dict(niche_dict)
        niche_dict["subtitle"] = "A Guided Journal " * 20
        with pytest.raises(KdpFactoryError) as caught:
            Pipeline(config).run(niche_from_dict(niche_dict), seed=3)
        assert "characters" in str(caught.value).lower()


class TestPackCapacity:
    """How many books an imprint can draw from the packs before they run dry.

    This is the constraint that actually bites: the packs, not the niches. The
    engine refuses to pad, so a thin pack shows up as a hard stop three books
    in. These numbers are the floor the packs are expected to hold; they fail
    loudly if someone trims a bank.
    """

    @staticmethod
    def _prompts(bank: str) -> int:
        import yaml

        path = "kdp_factory/content/templates/journal.yaml"
        return len(yaml.safe_load(open(path))["banks"][bank]["prompts"])

    def test_the_shared_core_bank_carries_four_journals(self):
        """Every journal draws on `core`, so journals compete with each other
        for it. Four books at 109 prompts is the bar."""
        assert self._prompts("core") >= 200

    @pytest.mark.parametrize("bank,floor", [
        ("grief", 50), ("fitness", 50), ("mindfulness", 45),
        ("gratitude", 35), ("parenting", 35),
    ])
    def test_each_topic_bank_can_fill_a_book_of_its_own(self, bank, floor):
        assert self._prompts(bank) >= floor

    def test_the_planner_holds_two_books_of_undated_weeks(self):
        """56 weeks a book, and a focus line is never reused across books."""
        import yaml

        pack = yaml.safe_load(open("kdp_factory/content/templates/planner.yaml"))
        assert len(pack["focus_lines"]) >= 112
        assert len(set(pack["focus_lines"])) == len(pack["focus_lines"])

    def test_the_wordlists_hold_two_puzzle_books(self):
        """A theme is dropped whole once a previous book used it, and a book of
        76 puzzles needs 38 themes, so two books need 76."""
        import yaml

        themes = yaml.safe_load(open("kdp_factory/content/templates/wordlists.yaml"))["themes"]
        assert len(themes) >= 76
        assert len({t["name"] for t in themes}) == len(themes)
        assert all(len(t["words"]) >= 20 for t in themes)

    def test_no_two_lines_in_a_pack_are_near_duplicates(self):
        """The generator holds itself to gate 2's bar, so the packs have to be
        able to meet it. A pair over the limit is dead weight: it can never be
        drawn alongside its twin."""
        import itertools

        import yaml

        from kdp_factory.content.text import similarity

        pack = yaml.safe_load(open("kdp_factory/content/templates/planner.yaml"))
        worst = max(similarity(a, b)
                    for a, b in itertools.combinations(pack["focus_lines"], 2))
        assert worst < 0.70, f"two focus lines overlap {worst:.0%}"
