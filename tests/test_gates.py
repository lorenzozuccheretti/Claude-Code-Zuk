"""The gates must be able to fail a build. A gate that cannot is decoration.

Every test here builds a real artifact, breaks something specific about it, and
asserts that the right check goes red — and that the build stops.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kdp_factory.errors import GateFailure
from kdp_factory.gates.base import GateInput, summarize_telemetry
from kdp_factory.gates.g1_niche import NicheGate
from kdp_factory.gates.g2_substance import SubstanceGate
from kdp_factory.gates.g3_print import PrintReadyGate
from kdp_factory.niche import niche_from_dict
from kdp_factory.run.pipeline import Pipeline


def verdicts(report):
    return {check.name: check.passed for check in report.checks}


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestNicheGate:
    def _inputs(self, tmp_path: Path, niche_dict: dict) -> GateInput:
        niche = niche_from_dict(niche_dict)
        return GateInput(
            subject="test",
            paths={
                "niche": write(tmp_path / "niche.json", niche.as_dict()),
                "niche_score": write(tmp_path / "score.json", niche.score().as_dict()),
            },
        )

    def test_a_good_niche_passes(self, tmp_path, niche_dict, config):
        report = NicheGate(config).run(self._inputs(tmp_path, niche_dict))
        assert report.passed, report.to_text()

    def test_a_hunch_fails_the_gate(self, tmp_path, niche_dict, config):
        """"You can name the actual demand signal — not a hunch." """
        for key in ("demand_volume", "proven_sales", "differentiation"):
            niche_dict["signals"][key] = {"score": 9.0}
        report = NicheGate(config).run(self._inputs(tmp_path, niche_dict))
        assert not report.passed
        assert not verdicts(report)["demand_evidence_named"]

    def test_unchecked_trademark_fails(self, tmp_path, niche_dict, config):
        niche_dict["trademark"] = {"checked": False}
        report = NicheGate(config).run(self._inputs(tmp_path, niche_dict))
        assert not verdicts(report)["trademark_checked"]
        assert not report.passed

    def test_a_trademark_hit_fails(self, tmp_path, niche_dict, config):
        niche_dict["trademark"]["hits"] = ["LIVE mark 5,123,456 for the exact phrase"]
        report = NicheGate(config).run(self._inputs(tmp_path, niche_dict))
        assert not verdicts(report)["trademark_clear"]

    def test_a_weak_niche_fails_on_score(self, tmp_path, niche_dict, config):
        for key in niche_dict["signals"]:
            niche_dict["signals"][key]["score"] = 3.0
        report = NicheGate(config).run(self._inputs(tmp_path, niche_dict))
        assert not verdicts(report)["score_threshold"]

    def test_a_missing_signal_is_caught(self, tmp_path, niche_dict, config):
        del niche_dict["signals"]["longevity"]
        report = NicheGate(config).run(self._inputs(tmp_path, niche_dict))
        assert not verdicts(report)["signals_complete"]

    def test_an_unprintable_target_is_caught(self, tmp_path, niche_dict, config):
        niche_dict["constraints"]["target_pages"] = 9
        report = NicheGate(config).run(self._inputs(tmp_path, niche_dict))
        assert not verdicts(report)["target_pages_printable"]

    def test_the_gate_stops_the_build(self, niche_dict, config):
        niche_dict["trademark"] = {"checked": False}
        result = Pipeline(config).run(niche_from_dict(niche_dict), seed=1)
        assert not result.ok
        assert result.failed_gate.gate_id == "g1_niche"
        assert (result.root / "STOPPED.md").is_file()
        # It stopped *before* building anything.
        assert result.context.manifest.artifact("interior_pdf") is None


@pytest.fixture
def built(good_niche, config):
    """A real, passing build — the baseline the sabotage tests start from."""
    result = Pipeline(config).run(good_niche, seed=3)
    assert result.ok, result.summary()
    return result


class TestSubstanceGate:
    def _inputs(self, result, **overrides) -> GateInput:
        paths = {
            artifact.role: result.root / artifact.path
            for artifact in result.context.manifest.artifacts
        }
        paths.update(overrides)
        return GateInput(subject=result.slug, paths=paths, facts=result.context.manifest.facts)

    def test_the_real_build_passes(self, built, config):
        report = SubstanceGate(config).run(self._inputs(built))
        assert report.passed, report.to_text()

    def test_padding_fails(self, built, config, tmp_path):
        plan = json.loads((built.root / "02_interior" / "interior_plan.json").read_text())
        for page in plan["pages"][10:60]:
            page["kind"] = "filler"
        plan["filler_pages"] = 50
        report = SubstanceGate(config).run(
            self._inputs(built, interior_plan=write(tmp_path / "padded.json", plan))
        )
        assert not verdicts(report)["padding"]
        assert not report.passed

    def test_repeated_content_fails(self, built, config, tmp_path):
        plan = json.loads((built.root / "02_interior" / "interior_plan.json").read_text())
        first = plan["content_units"][0]["text"]
        for unit in plan["content_units"][1:30]:
            unit["text"] = first
        report = SubstanceGate(config).run(
            self._inputs(built, interior_plan=write(tmp_path / "repeat.json", plan))
        )
        assert not verdicts(report)["repetition_exact"]

    def test_near_duplicates_fail(self, built, config, tmp_path):
        plan = json.loads((built.root / "02_interior" / "interior_plan.json").read_text())
        plan["content_units"][1]["text"] = plan["content_units"][0]["text"] + " today"
        report = SubstanceGate(config).run(
            self._inputs(built, interior_plan=write(tmp_path / "near.json", plan))
        )
        assert not verdicts(report)["repetition_near_duplicate"]

    def test_an_unsupported_number_in_the_title_fails(self, built, config, tmp_path):
        """The subtitle promises 365 prompts; the book has nothing like 365 of anything."""
        plan = json.loads((built.root / "02_interior" / "interior_plan.json").read_text())
        plan["subtitle"] = "365 Guided Prompts for New Mothers"
        report = SubstanceGate(config).run(
            self._inputs(built, interior_plan=write(tmp_path / "promise.json", plan))
        )
        assert not verdicts(report)["promise_numbers"]

    def test_content_that_never_reached_the_page_fails(self, built, config, tmp_path):
        plan = json.loads((built.root / "02_interior" / "interior_plan.json").read_text())
        plan["content_units"][4]["probe"] = "a prompt that was never printed anywhere"
        report = SubstanceGate(config).run(
            self._inputs(built, interior_plan=write(tmp_path / "ghost.json", plan))
        )
        assert not verdicts(report)["content_reaches_the_page"]

    def test_a_failed_format_check_fails_the_gate(self, built, config, tmp_path):
        checks = [{"name": "every_word_verified", "passed": False, "reason": "MULCH is not in grid 7"}]
        report = SubstanceGate(config).run(
            self._inputs(built, book_type_checks=write(tmp_path / "checks.json", checks))
        )
        assert not verdicts(report)["errors_format_verification"]

    def test_missing_front_matter_fails(self, built, config, tmp_path):
        plan = json.loads((built.root / "02_interior" / "interior_plan.json").read_text())
        plan["front_matter_pages"] = 0
        report = SubstanceGate(config).run(
            self._inputs(built, interior_plan=write(tmp_path / "nofront.json", plan))
        )
        assert not verdicts(report)["completeness_front_matter"]

    def test_the_gate_reads_the_pdf_not_the_plan(self, built, config, tmp_path):
        """Claiming pages the PDF does not have must not sneak past."""
        plan = json.loads((built.root / "02_interior" / "interior_plan.json").read_text())
        plan["page_count"] = 999
        report = SubstanceGate(config).run(
            self._inputs(built, interior_plan=write(tmp_path / "lie.json", plan))
        )
        assert report.metrics["page_count"] == 120


class TestPrintGate:
    def _inputs(self, result, **overrides) -> GateInput:
        paths = {
            artifact.role: result.root / artifact.path
            for artifact in result.context.manifest.artifacts
        }
        paths.update(overrides)
        return GateInput(subject=result.slug, paths=paths, facts=result.context.manifest.facts)

    def test_the_real_build_passes(self, built, config):
        report = PrintReadyGate(config).run(self._inputs(built))
        assert report.passed, report.to_text()

    def test_a_cover_built_for_the_wrong_page_count_fails(self, built, config, tmp_path):
        """The classic KDP rejection: a spine sized for a book you did not print."""
        from kdp_factory.content.copy import back_cover_copy
        from kdp_factory.render.cover import render_cover
        from kdp_factory.spec.kdp import cover_geometry

        wrong = cover_geometry("6x9", 300, "bw_cream")  # the book is 120 pages
        path = tmp_path / "wrong_cover.pdf"
        render_cover(wrong, "Title", "Subtitle", {"hook": "x", "body": "y", "benefits": []},
                     config, path)
        report = PrintReadyGate(config).run(self._inputs(built, cover_pdf=path))
        assert not verdicts(report)["cover_size_matches_page_count"]
        assert not report.passed

    def test_a_missing_artifact_is_itself_a_failure(self, built, config):
        data = GateInput(subject="x", paths={}, facts={})
        with pytest.raises(GateFailure, match="interior_pdf"):
            PrintReadyGate(config).run(data)


class TestTelemetry:
    def test_every_gate_run_is_counted(self, good_niche, config):
        Pipeline(config).run(good_niche, seed=11)
        stats = summarize_telemetry(config.telemetry_path)
        assert set(stats) == {"g1_niche", "g2_substance", "g3_print"}
        assert all(entry["runs"] == 1 for entry in stats.values())

    def test_a_gate_that_never_fails_is_reported_as_suspicious(self, tmp_path):
        path = tmp_path / "gates.jsonl"
        path.write_text(
            "\n".join(
                json.dumps({"gate_id": "g1_niche", "passed": True, "failed_checks": []})
                for _ in range(6)
            ),
            encoding="utf-8",
        )
        stats = summarize_telemetry(path)
        assert "never fails" in stats["g1_niche"]["verdict"]


class TestTypographyAndFrames:
    """Two defects found on a real build, each now guarded."""

    def test_the_print_gate_catches_a_book_set_in_a_builtin_face(self, built, config, tmp_path):
        """A missing font directory renders a memo, and nothing else notices."""
        from kdp_factory.gates.base import GateInput
        from reportlab.pdfgen import canvas as pdfcanvas

        plain = tmp_path / "helvetica.pdf"
        c = pdfcanvas.Canvas(str(plain), pagesize=(432, 648))
        for _ in range(120):
            c.setFont("Helvetica", 12)
            c.drawString(72, 300, "set in a builtin face")
            c.showPage()
        c.save()

        paths = {a.role: built.root / a.path for a in built.context.manifest.artifacts}
        paths["interior_pdf"] = plain
        report = PrintReadyGate(config).run(
            GateInput(subject="x", paths=paths, facts=built.context.manifest.facts)
        )
        assert not verdicts(report)["typography_embedded"]

    def test_a_real_build_is_set_in_the_vendored_faces(self, built, config):
        from kdp_factory.render.pdfutil import embedded_fonts

        interior = built.root / built.context.manifest.artifact("interior_pdf").path
        families = {name.split("-")[0] for name in embedded_fonts(interior)}
        assert {"Lora", "CormorantGaramond"} <= families

    def test_no_cover_text_is_drawn_outside_the_safe_area(self, config):
        """Letterspacing leaks into the PDF text state; it once pushed a title
        over the trim while ReportLab still measured it as fitting."""
        from kdp_factory.render.cover import CoverRenderer
        from kdp_factory.render.design import PALETTES_BY_KEY
        from kdp_factory.spec.kdp import cover_geometry

        copy = {
            "hook": "Five quiet minutes at the end of a day that had none.",
            "body": "A long line of body copy that has to stay inside its own panel.",
            "benefits": ["One prompt per page — no blank-page paralysis"] * 4,
            "closing": "Books that do one thing well.",
        }
        for trim, pages in (("6x9", 120), ("8.5x11", 200), ("5x8", 24)):
            renderer = CoverRenderer(
                cover_geometry(trim, pages, "bw_white"),
                "The Daily Mental Health Journal",
                "A 109-Prompt Guided Journal for women running on empty",
                copy, config, PALETTES_BY_KEY["dusk"], "arc",
            )
            renderer.render(config.output_root / f"safe-{trim}.pdf")
            low, high = renderer.safe_box()
            outside = [(a, b) for a, b in renderer.text_extents
                       if a < low - 0.5 or b > high + 0.5]
            assert outside == [], f"{trim}: text outside the safe area: {outside}"
            assert len(renderer.text_extents) > 5


class TestCoverStopsTheScroll:
    """A cover is first seen ~200px tall in a grid. That is measurable."""

    def _render(self, config, **kwargs):
        from kdp_factory.render.cover import CoverRenderer
        from kdp_factory.render.design import PALETTES_BY_KEY
        from kdp_factory.spec.kdp import cover_geometry

        copy = {"hook": "A hook.", "body": "Body.", "benefits": ["One"], "closing": "End."}
        renderer = CoverRenderer(
            cover_geometry("6x9", 120, "bw_white"),
            kwargs.pop("title", "The Daily Mental Health Journal"),
            "A 109-Prompt Guided Journal for women running on empty",
            copy, config, PALETTES_BY_KEY[kwargs.pop("palette", "dusk")], "arc", 1,
            **kwargs,
        )
        renderer.render(config.output_root / "thumb.pdf")
        return renderer

    @pytest.mark.parametrize("composition", ["banded", "reversed", "framed", "emblem"])
    def test_every_composition_is_readable_at_thumbnail_size(self, composition, config):
        renderer = self._render(config, composition=composition, badge="109 prompts")
        legibility = renderer.legibility()
        assert legibility["measured"]
        assert legibility["title_cap_px"] >= config.quality.min_title_cap_px
        assert legibility["title_contrast"] >= config.quality.min_title_contrast

    @pytest.mark.parametrize("composition", ["banded", "reversed", "framed", "emblem"])
    def test_no_composition_puts_text_outside_the_safe_area(self, composition, config):
        renderer = self._render(config, composition=composition, badge="109 prompts")
        low, high = renderer.safe_box()
        assert [(a, b) for a, b in renderer.text_extents if a < low - 0.5 or b > high + 0.5] == []

    def test_a_long_title_still_clears_the_bar(self, config):
        renderer = self._render(
            config, composition="emblem",
            title="The Complete Undated Gratitude and Reflection Journal for Beginners",
        )
        assert renderer.legibility()["title_cap_px"] >= config.quality.min_title_cap_px

    def test_the_gate_fails_a_cover_that_disappears_in_a_grid(self, built, config, tmp_path):
        """Tiny type on a low-contrast ground is the defect this guards."""
        import json
        from kdp_factory.gates.base import GateInput

        spec = json.loads((built.root / "03_cover" / "cover_spec.json").read_text())
        spec["legibility"] = {
            "measured": True, "thumbnail_height_px": 200,
            "title_size_pt": 11, "title_lines": 3,
            "title_cap_px": 2.4, "title_contrast": 1.9,
        }
        path = tmp_path / "faint.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        paths = {a.role: built.root / a.path for a in built.context.manifest.artifacts}
        paths["cover_spec"] = path
        report = PrintReadyGate(config).run(
            GateInput(subject="x", paths=paths, facts=built.context.manifest.facts))
        assert not verdicts(report)["cover_reads_at_thumbnail"]
        assert not report.passed

    def test_artwork_on_its_own_colour_is_drawn_in_something_else(self):
        """Art on the bold field was being tinted toward bold — invisible."""
        from kdp_factory.render.artwork import ArtSpec
        from kdp_factory.render.design import PALETTES_BY_KEY, contrast

        palette = PALETTES_BY_KEY["dusk"]
        on_panel = ArtSpec(x=0, y=0, width=100, height=100, palette=palette,
                           field=palette.panel)
        on_bold = ArtSpec(x=0, y=0, width=100, height=100, palette=palette,
                          field=palette.bold)
        assert on_panel.primary == palette.bold
        assert contrast(on_bold.primary, palette.bold) > 2.0


class TestCoverLayout:
    """What the last round of feedback was actually about."""

    def _render(self, config, composition, **kwargs):
        from kdp_factory.render.cover import CoverRenderer
        from kdp_factory.render.design import PALETTES_BY_KEY
        from kdp_factory.spec.kdp import cover_geometry

        copy = {
            "hook": "Five quiet minutes at the end of a day that had none.",
            "body": "Built for women running on empty. 109 guided prompts, 120 pages.",
            "benefits": ["One prompt per page — no blank-page paralysis",
                         "Room to write, not just room to tick",
                         "Undated, so a missed day costs you nothing",
                         "Bound to open flat and stay open"],
            "closing": "Books that do one thing well.",
        }
        renderer = CoverRenderer(
            cover_geometry(kwargs.pop("trim", "6x9"), kwargs.pop("pages", 120), "bw_white"),
            kwargs.pop("title", "The Daily Mental Health Journal"),
            kwargs.pop("subtitle", "A 109-Prompt Guided Journal for women running on empty"),
            copy, config, PALETTES_BY_KEY[kwargs.pop("palette", "balm")], "arc", 1,
            composition=composition, badge="109 prompts", **kwargs,
        )
        renderer.render(config.output_root / f"{composition}.pdf")
        return renderer

    @pytest.mark.parametrize("composition", ["banded", "reversed", "framed", "emblem"])
    def test_nothing_readable_sits_under_the_barcode(self, composition, config):
        """The white box that used to hide this area is gone, so it has to be
        kept clear rather than covered up."""
        renderer = self._render(config, composition)
        assert renderer.text_in_barcode_area() == []
        assert renderer.legibility()["barcode_area_clear"] is True

    @pytest.mark.parametrize("composition", ["banded", "reversed", "framed", "emblem"])
    def test_a_long_title_shrinks_instead_of_overflowing(self, composition, config):
        """A stack taller than its zone used to run over the artwork below it."""
        renderer = self._render(
            config, composition,
            title="The Complete Undated Gratitude Reflection and Evening Wind-Down Journal",
        )
        low, high = renderer.safe_box()
        assert [(a, b) for a, b in renderer.text_extents if a < low - 0.5 or b > high + 0.5] == []
        assert renderer.text_in_barcode_area() == []

    def test_a_title_too_long_for_its_layout_is_reported_not_shipped(self, config):
        """Compositions differ in how much room they give a title: `emblem`
        carries ten words where `banded` cannot. Where it cannot, the cover is
        measured as unreadable rather than quietly shipped."""
        long_title = "The Complete Undated Gratitude Reflection and Evening Wind-Down Journal"
        measured = {
            composition: self._render(config, composition, title=long_title)
                             .legibility()["title_cap_px"]
            for composition in ("banded", "reversed", "framed", "emblem")
        }
        assert measured["banded"] < config.quality.min_title_cap_px
        assert measured["emblem"] > measured["banded"]

    @pytest.mark.parametrize("composition", ["banded", "reversed", "framed", "emblem"])
    def test_the_titles_the_engine_writes_do_read_on_a_phone(self, composition, config):
        for title in ("The Daily Mental Health Journal", "Large Print Word Search",
                      "The Undated Weekly Planner", "Gratitude, One Page at a Time"):
            renderer = self._render(config, composition, title=title)
            assert renderer.legibility()["title_cap_px"] >= config.quality.min_title_cap_px, title

    def test_the_cover_never_carries_the_proof_markings(self, config):
        """Shipping the proof file is the mistake this guards."""
        from kdp_factory.render.pdfutil import extract_text

        renderer = self._render(config, "reversed")
        cover = config.output_root / "cover-clean.pdf"
        proof = config.output_root / "cover-proof.pdf"
        renderer.render(cover, guides=False)
        renderer.render(proof, guides=True)
        assert "barcode keep-out" not in extract_text(cover)
        assert "barcode keep-out" in extract_text(proof)
        assert "Do not upload" in extract_text(proof)


class TestCoverSubtitle:
    def test_the_cover_takes_the_first_clause(self):
        from kdp_factory.render.cover import short_subtitle

        assert short_subtitle(
            "A 109-Prompt Guided Journal for women running on empty"
        ) == "A 109-Prompt Guided Journal"

    def test_a_short_subtitle_is_left_alone(self):
        from kdp_factory.render.cover import short_subtitle

        assert short_subtitle("Five Minutes Before the Day Starts") == (
            "Five Minutes Before the Day Starts")

    def test_it_never_returns_a_stub(self):
        from kdp_factory.render.cover import short_subtitle

        # Splitting on " for " here would leave two words, so it must not.
        assert len(short_subtitle("Prompts for women who are done shrinking").split()) >= 3

    def test_the_listing_keeps_the_full_subtitle(self, good_niche, config):
        """Only the cover shortens it; the product page gets the whole thing."""
        import json
        from kdp_factory.run.pipeline import Pipeline

        result = Pipeline(config).run(good_niche, seed=6)
        listing = json.loads((result.root / "04_listing" / "listing.json").read_text())
        plan = json.loads((result.root / "02_interior" / "interior_plan.json").read_text())
        assert listing["subtitle"] == plan["subtitle"]
        assert len(listing["subtitle"].split()) > 4
