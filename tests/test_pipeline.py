"""The engine as a whole: stations in order, artifacts on disk, CLI wired up."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kdp_factory.cli import main
from kdp_factory.config import load_config
from kdp_factory.errors import ConfigError
from kdp_factory.niche import load_niche, load_niche_csv, niche_from_dict, rank, weights_sum
from kdp_factory.run.pipeline import Pipeline

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

EXPECTED_ARTIFACTS = {
    "niche", "niche_score", "niche_report",
    "interior_plan", "interior_pdf", "book_type_checks",
    "cover_pdf", "cover_proof_pdf", "cover_spec", "back_cover_copy",
    "listing", "listing_readable", "listing_keywords",
    "upload_plan", "upload_readable",
    "review_checklist", "run_report",
    "gate_g1_niche", "gate_g2_substance", "gate_g3_print",
}


class TestFullRun:
    def test_every_station_produces_its_artifacts(self, good_niche, config):
        result = Pipeline(config).run(good_niche, seed=2)
        assert result.ok, result.summary()
        roles = {a.role for a in result.context.manifest.artifacts}
        assert EXPECTED_ARTIFACTS <= roles

    def test_all_six_stations_ran(self, good_niche, config):
        result = Pipeline(config).run(good_niche, seed=2)
        stages = {s.station: s.status for s in result.context.manifest.stages}
        assert stages == {n: "ok" for n in range(1, 7)}

    def test_all_three_gates_ran(self, good_niche, config):
        result = Pipeline(config).run(good_niche, seed=2)
        assert [g["gate_id"] for g in result.context.manifest.gates] == [
            "g1_niche", "g2_substance", "g3_print"
        ]

    def test_stop_after_leaves_the_rest_undone(self, good_niche, config):
        result = Pipeline(config).run(good_niche, seed=2, stop_after=2)
        roles = {a.role for a in result.context.manifest.artifacts}
        assert "interior_pdf" in roles
        assert "cover_pdf" not in roles

    def test_the_manifest_survives_a_round_trip(self, good_niche, config):
        from kdp_factory.run.manifest import Manifest

        result = Pipeline(config).run(good_niche, seed=2)
        reloaded = Manifest.load(result.root / "manifest.json")
        assert reloaded.slug == result.slug
        assert reloaded.status == "complete"
        assert len(reloaded.artifacts) == len(result.context.manifest.artifacts)

    def test_page_count_on_record_is_the_pdf_page_count(self, good_niche, config):
        from kdp_factory.render.pdfutil import pdf_page_count

        result = Pipeline(config).run(good_niche, seed=2)
        interior = result.root / result.context.manifest.artifact("interior_pdf").path
        assert result.context.manifest.facts["page_count"] == pdf_page_count(interior)

    def test_an_unregistered_book_type_is_refused(self, niche_dict, config):
        niche_dict["book_type"] = "colouring"
        with pytest.raises(ConfigError, match="not registered"):
            Pipeline(config).run(niche_from_dict(niche_dict))


@pytest.mark.parametrize(
    "example", ["gratitude-new-mothers.yaml", "freelancer-planner.yaml", "senior-word-search.yaml"]
)
class TestShippedExamples:
    def test_the_example_builds(self, example, config):
        path = EXAMPLES / example
        if not path.is_file():
            pytest.skip(f"{example} not present")
        result = Pipeline(config).run(load_niche(path), seed=1)
        assert result.ok, result.summary()
        assert result.context.manifest.facts["page_count"] % 2 == 0


class TestNicheLoading:
    def test_the_weights_sum_to_one(self):
        assert weights_sum() == 1.0

    def test_a_niche_file_round_trips(self):
        niche = load_niche(EXAMPLES / "gratitude-new-mothers.yaml")
        assert niche.book_type == "journal"
        assert niche.signal("demand_volume").has_evidence

    def test_a_signal_outside_the_scale_is_refused(self):
        with pytest.raises(ConfigError, match="0–10"):
            niche_from_dict({"niche": "x", "book_type": "journal", "signals": {"demand_volume": 12}})

    def test_an_unknown_signal_is_refused(self):
        with pytest.raises(ConfigError, match="unknown signal"):
            niche_from_dict({"niche": "x", "book_type": "journal", "signals": {"vibes": 8}})

    def test_the_scout_csv_ranks_niches(self, tmp_path):
        csv = tmp_path / "niches.csv"
        csv.write_text(
            "niche,book_type,signal.demand_volume,evidence.demand_volume\n"
            "Low demand thing,journal,2,\"20 searches/mo\"\n"
            "High demand thing,journal,9,\"9000 searches/mo\"\n",
            encoding="utf-8",
        )
        ranked = rank(load_niche_csv(csv))
        assert ranked[0][0].niche == "High demand thing"
        assert ranked[0][1].missing_signals  # honest about what was never scored


class TestConfigFile:
    def test_a_brand_file_changes_the_book(self, tmp_path, good_niche):
        config_path = tmp_path / "engine.yaml"
        config_path.write_text(
            "brand:\n"
            "  imprint: Lamplight Books\n"
            "  author: A. Editor\n"
            "  copyright_year: 2026\n"
            "  palette_key: plum\n"
            f"output_root: {tmp_path / 'out'}\n",
            encoding="utf-8",
        )
        config = load_config(config_path)
        assert config.brand.imprint == "Lamplight Books"
        result = Pipeline(config).run(good_niche, seed=8)
        assert result.ok
        listing = json.loads((result.root / "04_listing" / "listing.json").read_text())
        assert listing["author"] == "A. Editor"

    def test_a_pinned_palette_reaches_the_book(self, tmp_path, good_niche):
        config_path = tmp_path / "engine.yaml"
        config_path.write_text(
            f"brand:\n  palette_key: moss\n  motif: stems\noutput_root: {tmp_path / 'out'}\n",
            encoding="utf-8",
        )
        result = Pipeline(load_config(config_path)).run(good_niche, seed=2)
        assert result.ok, result.summary()
        assert result.context.manifest.facts["palette"] == "moss"
        assert result.context.manifest.facts["motif"] == "stems"

    def test_the_old_hex_palette_block_explains_itself(self, tmp_path):
        """It was replaced by a named colour world; say so rather than ignoring it."""
        config_path = tmp_path / "engine.yaml"
        config_path.write_text("brand:\n  palette:\n    primary: '#2E2A26'\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="palette_key"):
            load_config(config_path)

    def test_an_unknown_config_key_is_refused(self, tmp_path):
        config_path = tmp_path / "engine.yaml"
        config_path.write_text("brand:\n  imprnt: typo\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="unknown key"):
            load_config(config_path)


def run_dir_in(root: Path) -> Path:
    """The build directory under an output root, ignoring `_telemetry/`."""
    return next(p for p in root.iterdir() if (p / "manifest.json").is_file())


class TestCLI:
    def test_types_lists_the_book_types(self, capsys):
        assert main(["types"]) == 0
        out = capsys.readouterr().out
        assert "journal" in out and "planner" in out and "puzzle" in out

    def test_spec_prints_the_card(self, capsys):
        assert main(["spec"]) == 0
        assert "SPINE" in capsys.readouterr().out

    def test_build_runs_end_to_end(self, tmp_path, capsys):
        code = main(
            ["build", str(EXAMPLES / "gratitude-new-mothers.yaml"), "--output", str(tmp_path)]
        )
        out = capsys.readouterr().out
        assert code == 0, out
        assert "GATE PASSED" in out
        runs = [p for p in tmp_path.iterdir() if p.name != "_telemetry"]
        assert len(runs) == 1
        assert (runs[0] / "manifest.json").is_file()
        # Telemetry follows --output, so `kdp gates` reports on these runs.
        assert (tmp_path / "_telemetry" / "gates.jsonl").is_file()

    def test_show_summarises_a_run(self, tmp_path, capsys):
        main(["build", str(EXAMPLES / "gratitude-new-mothers.yaml"), "--output", str(tmp_path)])
        run_dir = run_dir_in(tmp_path)
        capsys.readouterr()
        assert main(["show", str(run_dir)]) == 0
        assert "artifacts" in capsys.readouterr().out

    def test_gates_reports_fail_rates(self, tmp_path, capsys):
        main(["build", str(EXAMPLES / "gratitude-new-mothers.yaml"), "--output", str(tmp_path)])
        capsys.readouterr()
        assert main(["gates", "--output", str(tmp_path)]) == 0
        assert "g1_niche" in capsys.readouterr().out

    def test_upload_dry_run_needs_no_browser(self, tmp_path, capsys):
        main(["build", str(EXAMPLES / "gratitude-new-mothers.yaml"), "--output", str(tmp_path)])
        run_dir = run_dir_in(tmp_path)
        capsys.readouterr()
        assert main(["upload", str(run_dir)]) == 0
        out = capsys.readouterr().out
        assert "would type" in out and "stop" in out

    def test_a_failing_build_exits_nonzero(self, tmp_path, niche_dict, capsys):
        niche_dict["trademark"] = {"checked": False}
        niche_path = tmp_path / "bad.yaml"
        import yaml

        niche_path.write_text(yaml.safe_dump(niche_dict), encoding="utf-8")
        code = main(["build", str(niche_path), "--output", str(tmp_path / "out")])
        assert code == 2
        assert "GATE FAILED" in capsys.readouterr().out


class TestSeriesWithoutRepeats:
    """Book two in a niche should not be a rerun of book one."""

    def _prompts(self, result) -> set[str]:
        plan = json.loads((result.root / "02_interior" / "interior_plan.json").read_text())
        from kdp_factory.content.text import normalize

        return {normalize(u["text"]) for u in plan["content_units"]}

    def test_two_seeds_overlap_by_default(self, good_niche, config):
        """Documented behaviour: a seed alone does not know what else you printed."""
        first = Pipeline(config).run(good_niche, seed=1)
        second = Pipeline(config).run(good_niche, seed=2)
        assert self._prompts(first) & self._prompts(second)

    def test_avoid_makes_the_second_book_disjoint(self, good_niche, config):
        from kdp_factory.run.history import load_used_content

        first = Pipeline(config).run(good_niche, seed=1)
        used = load_used_content([first.root])
        assert used
        second = Pipeline(config).run(good_niche, seed=2, avoid=used)
        assert second.ok, second.summary()
        assert not (self._prompts(first) & self._prompts(second))

    def test_avoid_reads_a_folder_of_runs(self, good_niche, config):
        from kdp_factory.run.history import load_used_content

        Pipeline(config).run(good_niche, seed=1)
        Pipeline(config).run(good_niche, seed=2)
        used = load_used_content([config.output_root])
        assert len(used) > 109  # both books, not just one

    def test_a_broken_previous_run_is_skipped_not_fatal(self, tmp_path):
        from kdp_factory.run.history import load_used_content

        broken = tmp_path / "run" / "02_interior"
        broken.mkdir(parents=True)
        (broken / "interior_plan.json").write_text("{not json", encoding="utf-8")
        assert load_used_content([tmp_path]) == set()

    def test_exhausting_the_pack_is_refused_not_padded(self, good_niche, config):
        """When there is nothing new left to say, the engine says so."""
        from kdp_factory.content.packs import load_pack, pool_from_banks, select_banks
        from kdp_factory.content.text import normalize

        pack = load_pack("journal")
        banks = select_banks(pack, [good_niche.niche, good_niche.audience])
        everything = {normalize(line) for line in pool_from_banks(pack, banks)}
        with pytest.raises(ConfigError, match="previous run"):
            Pipeline(config).run(good_niche, seed=3, avoid=everything)


class TestCLIErgonomics:
    """Shared options must work on either side of the subcommand."""

    def test_config_before_the_subcommand(self, capsys):
        assert main(["--config", str(EXAMPLES / "engine.yaml"), "types"]) == 0

    def test_config_after_the_subcommand(self, tmp_path, capsys):
        code = main(
            [
                "score", str(EXAMPLES / "senior-word-search.yaml"),
                "--config", str(EXAMPLES / "engine.yaml"),
                "--output", str(tmp_path),
            ]
        )
        assert code == 0, capsys.readouterr().out

    def test_telemetry_follows_the_output_root(self, tmp_path):
        from kdp_factory.config import EngineConfig

        moved = EngineConfig().with_output_root(tmp_path / "elsewhere")
        assert moved.telemetry_path == tmp_path / "elsewhere" / "_telemetry" / "gates.jsonl"

    def test_a_pinned_telemetry_path_is_left_alone(self, tmp_path):
        from kdp_factory.config import EngineConfig

        pinned = EngineConfig(telemetry_path=tmp_path / "audit.jsonl")
        assert pinned.with_output_root(tmp_path / "elsewhere").telemetry_path == (
            tmp_path / "audit.jsonl"
        )
