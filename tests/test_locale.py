"""Locales: one engine, two languages, and English exactly as it was."""

from __future__ import annotations

import copy

import pytest

from kdp_factory.booktypes import get_book_type
from kdp_factory.config import Brand, EngineConfig
from kdp_factory.content.copy import back_cover_copy, description
from kdp_factory.content.locale import format_trim, normalize_language, strings
from kdp_factory.content.rng import StageRandom
from kdp_factory.errors import ConfigError
from kdp_factory.niche import niche_from_dict
from kdp_factory.stations.s3_cover import badge_for


def _keys(node, prefix=""):
    if isinstance(node, dict):
        out = set()
        for key, value in node.items():
            out |= {f"{prefix}{key}"} | _keys(value, f"{prefix}{key}.")
        return out
    return set()


class TestLanguage:
    @pytest.mark.parametrize("spelling,canonical", [
        ("english", "english"), ("EN", "english"), (" English ", "english"),
        ("italian", "italian"), ("it", "italian"), ("Italiano", "italian"),
    ])
    def test_spellings_normalise(self, spelling, canonical):
        assert Brand(language=spelling).language == canonical

    def test_an_unknown_language_names_the_options(self):
        with pytest.raises(ConfigError, match="italiano"):
            normalize_language("klingon")
        with pytest.raises(ConfigError, match="unknown language"):
            Brand(language="fr")

    def test_tables_are_cached(self):
        assert strings("en") is strings("english")

    def test_both_tables_have_the_same_keys(self):
        """A key missing from one table is a page that prints nothing."""
        english, italian = _keys(strings("english")), _keys(strings("italian"))
        assert english == italian

    def test_trims_are_written_per_language(self):
        assert format_trim(strings("english"), "6x9", "cover") == "6” x 9”"
        assert format_trim(strings("english"), "8.5x11", "listing") == "8.5 x 11 inches"
        assert format_trim(strings("italian"), "8.5x11", "cover") == "21,6 x 27,9 cm"


def _journal(niche_dict, language="english", **overrides):
    data = copy.deepcopy(niche_dict)
    data.update(overrides)
    niche = niche_from_dict(data)
    config = EngineConfig(brand=Brand(language=language))
    plan = get_book_type(niche.book_type).plan(niche, config, StageRandom(3, "interior"))
    return niche, config, plan


class TestEnglishIsUnchanged:
    """The strings the code used to hard-wire, now read from locale_en.yaml."""

    def test_the_copyright_page(self, niche_dict):
        _, _, plan = _journal(niche_dict)
        assert plan.pages[1].data["lines"] == [
            "Copyright © 2026 North Pine Press",
            "All rights reserved.",
            "No part of this book may be reproduced in any form without written "
            "permission from the publisher, except brief quotations in a review.",
            "Published by North Pine Press.",
            "First edition.",
        ]
        assert plan.pages[2].data["heading"] == "This journal belongs to"
        assert plan.pages[3].data["heading"] == "How to use this journal"

    def test_the_back_cover_and_the_description(self, niche_dict):
        niche, config, plan = _journal(niche_dict)
        copy_ = back_cover_copy(plan, niche, config.brand)
        count = plan.metadata["prompt_count"]
        assert copy_["body"] == (
            f"{plan.title} is built for {niche.audience_phrase}. {count} guided prompts, "
            f"120 pages, 6” x 9”."
        )
        assert copy_["closing"] == "From North Pine Press."
        assert "Single-sided layout and generous margins" in copy_["benefits"]
        html = description(plan, niche, config.brand)
        assert "<p><b>What's inside</b></p>" in html
        assert "<li>120 pages, 6 x 9 inches</li>" in html
        assert "<p><b>Why this one</b></p>" in html

    def test_the_cover_flash(self, niche_dict):
        _, _, plan = _journal(niche_dict)
        assert badge_for(plan) == f"{plan.metadata['prompt_count']} prompts"


class TestItalianMatter:
    def test_shared_front_matter_follows_the_imprint_language(self, niche_dict):
        _, _, plan = _journal(niche_dict, "italian")
        assert plan.pages[1].data["lines"][1] == "Tutti i diritti riservati."
        assert plan.pages[2].data["heading"] == "Questo diario appartiene a"
        assert plan.pages[3].data["intro"].endswith("nell'ordine che preferisci.")

    def test_italian_copy(self, niche_dict):
        niche, config, plan = _journal(niche_dict, "italian")
        copy_ = back_cover_copy(plan, niche, config.brand)
        assert copy_["body"].endswith("120 pagine, formato 15,2 x 22,9 cm.")
        assert copy_["closing"] == "Da North Pine Press."
        assert badge_for(plan, "italian").endswith("domande")


class TestNicheCopy:
    COPY = {
        "hook": "The book your midwife wishes you had.",
        "body": "Written by a mother of three, for the first year.",
        "benefits": ["Lies flat", "Undated"],
        "closing": "With love, from the press.",
        "description_paragraphs": ["First paragraph.", "Second paragraph."],
    }

    def test_hand_written_copy_is_used_verbatim(self, niche_dict):
        niche, config, plan = _journal(niche_dict, copy=self.COPY)
        copy_ = back_cover_copy(plan, niche, config.brand)
        assert copy_ == {key: self.COPY[key] for key in ("hook", "body", "benefits", "closing")}
        html = description(plan, niche, config.brand)
        assert html.startswith("<h2>The book your midwife wishes you had.</h2>"
                               "<p>First paragraph.</p><p>Second paragraph.</p>")
        assert "<li>Lies flat</li><li>Undated</li>" in html
        assert "Written by a mother of three" not in html  # the paragraphs replace the body

    def test_an_absent_block_changes_nothing_on_record(self, niche_dict):
        assert "copy" not in niche_from_dict(niche_dict).as_dict()
        data = dict(niche_dict, copy={"hook": "Hi."})
        assert niche_from_dict(data).as_dict()["copy"]["hook"] == "Hi."

    def test_an_unknown_key_is_refused(self, niche_dict):
        with pytest.raises(ConfigError, match="unknown key"):
            niche_from_dict(dict(niche_dict, copy={"tagline": "x"}))

    def test_the_listing_still_enforces_the_description_limit(self, niche_dict, config):
        from kdp_factory.errors import KdpFactoryError
        from kdp_factory.run.pipeline import Pipeline

        data = dict(niche_dict, copy={"description_paragraphs": ["word " * 1000]})
        with pytest.raises(KdpFactoryError, match="description is"):
            Pipeline(config).run(niche_from_dict(data), seed=1)


def test_cover_subtitle_from_the_niche_is_used_verbatim():
    from kdp_factory.render.cover import CoverRenderer
    from kdp_factory.spec.kdp import cover_geometry

    niche = niche_from_dict({
        "niche": "Diario del caregiver", "book_type": "logbook",
        "copy": {"cover_subtitle": "Farmaci, sintomi  e ore di assistenza"},
    })
    assert niche.copy.cover_subtitle == "Farmaci, sintomi  e ore di assistenza"
    assert niche.copy.as_dict()["cover_subtitle"] == niche.copy.cover_subtitle
    config = EngineConfig(brand=Brand(language="italian"))
    geometry = cover_geometry("8.5x11", 160, "bw_white")
    subtitle = "Registro giornaliero di farmaci, sintomi e parametri vitali, con le ore di assistenza"
    cut = CoverRenderer(geometry, "Titolo", subtitle, {}, config)
    assert cut.cover_subtitle == "Registro giornaliero di farmaci"
    pinned = CoverRenderer(geometry, "Titolo", subtitle, {}, config,
                           cover_subtitle=niche.copy.cover_subtitle)
    assert pinned.cover_subtitle == "Farmaci, sintomi e ore di assistenza"
