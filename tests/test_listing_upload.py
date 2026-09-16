"""Stations 4 and 5: the listing obeys KDP's limits, and the run stops at Publish."""

from __future__ import annotations

import json

import pytest

from kdp_factory.errors import SpecViolation
from kdp_factory.run.pipeline import Pipeline
from kdp_factory.spec.kdp import listing_limits
from kdp_factory.upload.plan import FORBIDDEN_ACTIONS, build_upload_plan
from kdp_factory.upload.playwright_driver import (
    PublishAttempted,
    UploadRun,
    assert_not_forbidden,
)


@pytest.fixture
def listing(good_niche, config):
    result = Pipeline(config).run(good_niche, seed=4)
    assert result.ok, result.summary()
    return result, json.loads((result.root / "04_listing" / "listing.json").read_text())


class TestListingLimits:
    def test_seven_keywords_exactly(self, listing):
        _, data = listing
        assert len(data["keywords"]) == listing_limits()["keyword_slots"]

    def test_no_keyword_exceeds_the_character_limit(self, listing):
        _, data = listing
        assert all(len(k) <= listing_limits()["keyword_chars"] for k in data["keywords"])

    def test_no_keyword_slot_only_repeats_the_title(self, listing):
        """"7 keywords, no slot wasted repeating title words." """
        _, data = listing
        title_words = set(f"{data['title']} {data['subtitle']}".lower().split())
        for keyword in data["keywords"]:
            assert set(keyword.split()) - title_words, keyword

    def test_keywords_are_unique(self, listing):
        _, data = listing
        assert len(set(data["keywords"])) == len(data["keywords"])

    def test_three_categories(self, listing):
        _, data = listing
        assert len(data["categories"]) == listing_limits()["categories"]

    def test_title_and_subtitle_fit(self, listing):
        _, data = listing
        assert len(data["title"]) + len(data["subtitle"]) <= listing_limits()[
            "title_plus_subtitle_chars"
        ]

    def test_description_fits(self, listing):
        _, data = listing
        assert len(data["description_html"]) <= listing_limits()["description_chars"]

    def test_the_first_keywords_come_from_the_niche_evidence(self, listing, good_niche):
        _, data = listing
        assert data["keywords"][0] in [k.lower() for k in good_niche.keywords_seed]


class TestPricing:
    def test_price_clears_the_printing_cost(self, listing):
        _, data = listing
        price = data["price"]
        assert price["list_price_usd"] >= price["min_list_price_usd"]
        assert price["royalty_usd"] > 0

    def test_price_meets_the_policy_royalty_floor(self, listing, config):
        _, data = listing
        assert data["price"]["royalty_usd"] >= config.price.min_royalty_usd

    def test_a_pinned_price_below_the_floor_is_refused(self, good_niche, config):
        from dataclasses import replace

        niche = replace(good_niche, constraints={**good_niche.constraints, "price": 3.00})
        with pytest.raises(SpecViolation, match="lose money|royalty"):
            Pipeline(config).run(niche, seed=4)


class TestUploadPlan:
    def test_every_listing_field_is_in_the_plan(self, listing):
        result, data = listing
        plan = json.loads((result.root / "05_upload" / "upload_plan.json").read_text())
        values = {str(f["value"]) for f in plan["fields"]}
        assert data["title"] in values
        assert all(keyword in values for keyword in data["keywords"])
        assert f"{data['price']['list_price_usd']:.2f}" in values

    def test_the_files_it_points_at_exist(self, listing):
        result, _ = listing
        plan = json.loads((result.root / "05_upload" / "upload_plan.json").read_text())
        from pathlib import Path

        assert all(Path(f["value"]).is_file() for f in plan["files"])

    def test_the_plan_declares_where_it_stops(self, listing):
        result, _ = listing
        plan = json.loads((result.root / "05_upload" / "upload_plan.json").read_text())
        assert "publish" in plan["stops_before"].lower()
        assert plan["forbidden_actions"] == list(FORBIDDEN_ACTIONS)

    def test_it_reads_the_form_back(self, listing):
        result, _ = listing
        plan = json.loads((result.root / "05_upload" / "upload_plan.json").read_text())
        assert "title" in plan["read_back"]
        assert "list_price" in plan["read_back"]

    def test_a_dry_run_describes_everything_without_a_browser(self, listing):
        result, data = listing
        plan = json.loads((result.root / "05_upload" / "upload_plan.json").read_text())
        described = UploadRun(plan, dry_run=True).describe()
        assert data["title"] in described
        assert "stop" in described.lower()

    def test_publishing_is_refused(self):
        assert_not_forbidden("fill the title field")
        with pytest.raises(PublishAttempted):
            assert_not_forbidden("click Publish Your Paperback Book")

    def test_categories_are_handed_to_the_human(self, listing):
        result, data = listing
        plan = json.loads((result.root / "05_upload" / "upload_plan.json").read_text())
        notes = " ".join(plan["notes"])
        assert data["categories"][0] in notes


class TestUploadPlanShape:
    def test_missing_selectors_do_not_silently_drop_a_field(self, tmp_path):
        listing = {
            "title": "T", "subtitle": "S", "author": "A B", "language": "english",
            "description_html": "<p>x</p>", "keywords": [f"k{i}" for i in range(7)],
            "categories": ["c1", "c2", "c3"],
            "price": {"list_price_usd": 9.99},
            "print": {"trim_size": "6x9", "paper": "bw_white", "bleed": False,
                      "cover_finish": "matte", "page_count": 120},
        }
        manuscript = tmp_path / "i.pdf"
        manuscript.write_bytes(b"%PDF-1.4\n")
        cover = tmp_path / "c.pdf"
        cover.write_bytes(b"%PDF-1.4\n")
        plan = build_upload_plan("slug", listing, manuscript, cover)
        for field in plan.fields:
            assert field.labels or field.css, field.name


class TestBackCoverCopy:
    """A gap is research. Only a gap that maps to a claim becomes a bullet."""

    def _plan(self, config):
        from kdp_factory.booktypes import get_book_type
        from kdp_factory.content.rng import StageRandom
        from kdp_factory.niche import niche_from_dict

        niche = niche_from_dict({
            "niche": "Gratitude journal for new mothers",
            "book_type": "journal",
            "audience": "first-time mothers",
            "constraints": {"target_pages": 120},
        })
        return niche, get_book_type("journal").plan(niche, config, StageRandom(1, "t"))

    def test_a_recognised_complaint_becomes_a_claim(self, config):
        from dataclasses import replace
        from kdp_factory.content.copy import back_cover_copy
        from kdp_factory.niche import Competitor

        niche, plan = self._plan(config)
        niche = replace(niche, competitor=Competitor(gaps=["the binding came undone the first time I opened it"]))
        bullets = back_cover_copy(plan, niche, config.brand)["benefits"]
        assert any("open flat" in b for b in bullets)

    def test_an_unmatched_review_is_never_reprinted(self, config):
        """The failure this guards: a stranger's one-star review on your cover."""
        from dataclasses import replace
        from kdp_factory.content.copy import back_cover_copy, description
        from kdp_factory.niche import Competitor

        review = (
            "Did not like this book at all. It wasn't like a good prompt journal to "
            "help you get feelings out, it was basic and bland. I returned it."
        )
        niche, plan = self._plan(config)
        niche = replace(niche, competitor=Competitor(gaps=[review]))
        bullets = back_cover_copy(plan, niche, config.brand)["benefits"]
        assert all("basic and bland" not in b.lower() for b in bullets)
        assert "basic and bland" not in description(plan, niche, config.brand).lower()

    def test_bullets_stay_short_enough_to_print(self, config):
        from dataclasses import replace
        from kdp_factory.content.copy import MAX_BENEFIT_CHARS, back_cover_copy
        from kdp_factory.niche import Competitor

        niche, plan = self._plan(config)
        niche = replace(niche, competitor=Competitor(gaps=[
            "pages bleed through terribly and " + "x" * 300,
            "the binding is poorly made",
        ]))
        bullets = back_cover_copy(plan, niche, config.brand)["benefits"]
        assert all(len(b) <= MAX_BENEFIT_CHARS for b in bullets)
