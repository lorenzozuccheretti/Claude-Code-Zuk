"""The logbook: pack-driven forms that land on their page count and never overflow."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from kdp_factory.booktypes import get_book_type
from kdp_factory.booktypes.logbook import load_logbook_pack, parse_pack
from kdp_factory.config import Brand, EngineConfig
from kdp_factory.content.rng import StageRandom, derive_seed
from kdp_factory.errors import ConfigError, RenderError
from kdp_factory.niche import load_niche, niche_from_dict
from kdp_factory.render.interior import InteriorRenderer, render_interior
from kdp_factory.render.pdfutil import extract_pages_text
from kdp_factory.run.pipeline import Pipeline

from conftest import GOOD_NICHE

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LOGBOOK = get_book_type("logbook")


def logbook_niche(target_pages: int = 120, **extra) -> object:
    data = {
        "niche": "Daily care log for family caregivers",
        "book_type": "logbook",
        "audience": "family caregivers",
        "keywords_seed": ["one", "two", "three"],
        "constraints": {"target_pages": target_pages},
    }
    data.update(extra)
    return niche_from_dict(data)


def plan(niche, config=None, seed: int = 1, options=None):
    return LOGBOOK.plan(niche, config or EngineConfig(),
                        StageRandom(derive_seed("logbook", seed), "interior"), options=options)


class TestPageSolving:
    @pytest.mark.parametrize("target", [60, 90, 120])
    def test_lands_on_the_target(self, target):
        result = plan(logbook_niche(target))
        assert result.page_count == target
        assert all(passed for _, passed, _ in LOGBOOK.verify(result))

    @pytest.mark.parametrize("target", [60, 90, 120])
    def test_weekly_and_monthly_pages_are_counted(self, target):
        result = plan(logbook_niche(target))
        meta = result.metadata
        cycle = [p.data["cycle"] for p in result.pages if p.data.get("cycle")]
        assert cycle.count("daily") == meta["days"]
        assert cycle.count("weekly") == meta["days"] // 7 == meta["weeks"]
        assert cycle.count("monthly") == meta["weeks"] // 4 == meta["months"]

    def test_a_weekly_page_follows_every_seventh_day(self):
        result = plan(logbook_niche(90))
        cycle = [p.data for p in result.pages if p.data.get("cycle")]
        for index, data in enumerate(cycle):
            if data["cycle"] == "weekly":
                previous = cycle[index - 1]
                assert previous["cycle"] == "daily" and previous["n"] == data["n"] * 7

    def test_pinned_days_win_over_the_page_count(self):
        result = plan(logbook_niche(120), options={"days": 30})
        assert result.metadata["days"] == 30
        assert result.page_count == 120  # the rest is notes pages, counted as filler
        assert result.filler_pages > 0

    def test_the_subtitle_carries_the_solved_day_count(self):
        result = plan(logbook_niche(90))
        assert f"{result.metadata['days']} Days" in result.subtitle

    def test_a_target_too_small_for_the_extras_is_refused(self, tmp_path):
        import yaml

        from kdp_factory.content.packs import load_pack

        pack = copy.deepcopy(load_pack("logbook_example"))
        pack["extras"][0]["count"] = 20
        path = tmp_path / "big_extras.yaml"
        path.write_text(yaml.safe_dump(pack, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ConfigError, match="leaves room for only"):
            plan(logbook_niche(40, options={"pack": str(path)}))


class TestPrompts:
    def test_refuses_rather_than_repeat_prompts(self):
        """The example pack holds about a hundred daily prompts; 200 pages need more."""
        with pytest.raises(ConfigError, match="distinct daily prompts"):
            plan(logbook_niche(200))

    def test_every_prompt_is_a_distinct_unit_on_its_own_page(self):
        result = plan(logbook_niche(120))
        prompts = [u for u in result.content_units if u.kind.endswith("_prompt")]
        assert len({u.normalized for u in prompts}) == len(prompts)
        for unit in prompts:
            assert result.pages[unit.page_index].data["prompt"] == unit.text

    def test_headings_are_units_too(self):
        result = plan(logbook_niche(120))
        kinds = {u.kind for u in result.content_units}
        assert {"reference", "record", "daily_prompt", "weekly_prompt"} <= kinds
        # a ledger repeated over several pages is one record, not several
        headings = [u.text for u in result.content_units if u.kind == "record"]
        assert len(headings) == len(set(headings))


class TestPackValidation:
    BASE = {
        "cycle": {"daily": {"heading": "Today", "elements": [
            {"type": "prompt", "label": "For you"},
            {"type": "lines", "label": "Notes", "count": "fill"},
        ]}},
        "daily_prompts": ["a"],
    }

    def test_fill_must_be_the_last_element(self):
        pack = copy.deepcopy(self.BASE)
        pack["cycle"]["daily"]["elements"].reverse()
        with pytest.raises(ConfigError, match="not the last element"):
            parse_pack(pack, "test")

    def test_a_daily_page_needs_exactly_one_prompt(self):
        pack = copy.deepcopy(self.BASE)
        pack["cycle"]["daily"]["elements"].pop(0)
        with pytest.raises(ConfigError, match="exactly one prompt"):
            parse_pack(pack, "test")

    def test_an_unknown_element_key_is_named(self):
        pack = copy.deepcopy(self.BASE)
        pack["cycle"]["daily"]["elements"][1]["colour"] = "red"
        with pytest.raises(ConfigError, match="colour"):
            parse_pack(pack, "test")

    def test_a_pack_can_be_named_by_path(self):
        pack = load_logbook_pack(str(FIXTURES / "logbook_it_small.yaml"))
        assert pack.language == "italian"
        assert pack.daily.every == 7


class TestForms:
    def _renderer(self, result):
        return InteriorRenderer(result, EngineConfig())

    def test_count_fill_takes_the_space_that_is_left(self):
        from kdp_factory.render.interior import LINE_PITCH

        result = plan(logbook_niche(120))
        renderer = self._renderer(result)
        index = next(i for i, p in enumerate(result.pages) if p.data.get("cycle") == "daily")
        laid = renderer.layout_form(result.pages[index], index + 1)
        last = laid[-1]
        assert last["element"]["count"] == "fill" and last["count"] >= 1
        _, bottom, _, _ = renderer.text_frame(index + 1)
        left = last["top"] - last["height"] - bottom
        assert 0 <= left < LINE_PITCH  # filled, and not one line more would fit

    def test_rows_fill_on_a_ledger(self):
        result = plan(logbook_niche(120))
        renderer = self._renderer(result)
        index = next(i for i, p in enumerate(result.pages)
                     if p.data.get("heading") == "Care expenses")
        laid = renderer.layout_form(result.pages[index], index + 1)
        assert laid[-1]["element"]["type"] == "table" and laid[-1]["count"] > 10

    def test_nothing_is_drawn_below_the_live_area(self):
        result = plan(logbook_niche(120), )
        renderer = self._renderer(result)
        for index, page in enumerate(result.pages):
            if page.template != "form_page":
                continue
            _, bottom, _, _ = renderer.text_frame(index + 1)
            for entry in renderer.layout_form(page, index + 1):
                assert entry["top"] - entry["height"] >= bottom - 0.01

    def test_a_form_that_cannot_fit_raises_naming_the_element(self, tmp_path):
        niche = logbook_niche(24, options={"pack": str(FIXTURES / "logbook_overflow.yaml"),
                                           "days": 7})
        result = plan(niche)
        with pytest.raises(RenderError, match=r"Far too many rows.*element 1 \(table 'Rows'\)"):
            render_interior(result, EngineConfig(), tmp_path / "x.pdf")

    def test_a_reference_page_that_will_not_fit_raises(self, tmp_path):
        result = plan(logbook_niche(120))
        page = next(p for p in result.pages if p.template == "text_page")
        page.data["paragraphs"] = ["A sentence that goes on for a while. " * 40] * 6
        with pytest.raises(RenderError, match="even at 9pt"):
            render_interior(result, EngineConfig(), tmp_path / "x.pdf")


ITALIAN = EngineConfig(brand=Brand(language="italian"))


def italian_niche() -> dict:
    data = copy.deepcopy(GOOD_NICHE)
    data.update({
        "niche": "Diario di cura per caregiver familiari",
        "book_type": "logbook",
        "audience": "familiari che assistono a casa un genitore",
        "audience_short": "caregiver familiari",
        "promise": "",
        "keywords_seed": ["diario caregiver", "registro farmaci", "quaderno assistenza anziani"],
        "constraints": {"trim_size": "6x9", "paper": "bw_white", "target_pages": 40},
        "options": {"pack": str(FIXTURES / "logbook_it_small.yaml")},
    })
    data["competitor"] = {"title": "Diario", "price": 9.99, "reviews": 10,
                          "gaps": ["le pagine sono troppo piccole"]}
    return data


class TestItalianBuild:
    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        root = tmp_path_factory.mktemp("it")
        config = EngineConfig(brand=Brand(language="it"), output_root=root,
                              telemetry_path=root / "t.jsonl")
        result = Pipeline(config).run(niche_from_dict(italian_niche()), seed=3)
        return result

    def test_the_build_passes_every_gate(self, built):
        assert built.ok, built.summary()

    def test_the_book_lands_exactly_on_forty_pages(self, built):
        assert built.context.manifest.facts["page_count"] == 40

    def test_the_interior_is_in_italian(self, built):
        import re

        from kdp_factory.content.text import normalize

        interior = built.root / built.context.manifest.artifact("interior_pdf").path
        pages = extract_pages_text(interior)
        text = normalize(" ".join(pages))
        assert "tutti i diritti riservati" in text            # front matter, from the pack
        assert "questo diario appartiene a" in text
        assert "come usare questo libro" in normalize(pages[3])  # eyebrow, from the locale
        assert "questa è l'ultima pagina del diario" in normalize(pages[-1])
        assert normalize("Giorno 1") in text and normalize("Settimana 4") in text
        # no English label the renderer prints itself
        for english in ("How to use this book", "Also from this press", "Notes", "Day 1"):
            assert not re.search(rf"\b{re.escape(english.lower())}\b", " ".join(pages).lower())

    def test_the_listing_is_in_italian(self, built):
        import json

        listing = json.loads((built.root / built.context.manifest.artifact("listing").path)
                             .read_text(encoding="utf-8"))
        assert listing["language"] == "Italian"
        assert "Cosa c'è dentro" in listing["description_html"]
        assert "15,2 x 22,9 cm" in listing["description_html"]
        assert all(c.startswith("Libri > ") for c in listing["categories"])
        assert "diario" in " ".join(listing["keywords"])

    def test_the_cover_flash_and_back_copy_are_in_italian(self, built):
        import json

        spec = json.loads((built.root / built.context.manifest.artifact("cover_spec").path)
                          .read_text(encoding="utf-8"))
        assert spec["badge"] == "28 giorni"
        body = spec["back_cover_copy"]["body"]
        assert "è pensato per caregiver familiari" in body and "15,2 x 22,9 cm" in body
        assert "Caratteri grandi e leggibili in tutto il libro" in spec["back_cover_copy"]["benefits"]


class TestEndToEnd:
    def test_the_english_fixture_niche_builds_through_all_three_gates(self, config):
        niche = load_niche(FIXTURES / "logbook-care-log-niche.yaml")
        result = Pipeline(config).run(niche, seed=1)
        assert result.ok, result.summary()
        assert [g["gate_id"] for g in result.context.manifest.gates] == [
            "g1_niche", "g2_substance", "g3_print"]
        assert all(g["passed"] for g in result.context.manifest.gates)

    def test_planning_and_rendering_are_deterministic(self, tmp_path):
        from kdp_factory.naming import sha256_file

        hashes = set()
        for name in ("a", "b"):
            result = plan(logbook_niche(90), seed=4)
            hashes.add(sha256_file(render_interior(result, EngineConfig(), tmp_path / f"{name}.pdf")))
        assert len(hashes) == 1
