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
