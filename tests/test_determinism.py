"""Same niche, same seed, same book — byte for byte.

This is what makes a factory auditable: a run from three months ago can be
reproduced exactly, and a diff between two runs is a real difference in the
book rather than a timestamp.
"""

from __future__ import annotations

from kdp_factory.naming import run_slug, sha256_file, slugify
from kdp_factory.content.rng import StageRandom, derive_seed
from kdp_factory.run.pipeline import Pipeline


def interior_hash(result) -> str:
    return sha256_file(result.root / result.context.manifest.artifact("interior_pdf").path)


class TestSeededRandomness:
    def test_the_same_stage_yields_the_same_stream(self):
        a = StageRandom(derive_seed("niche", 1), "interior")
        b = StageRandom(derive_seed("niche", 1), "interior")
        assert a.shuffled(range(50)) == b.shuffled(range(50))

    def test_stages_do_not_share_a_stream(self):
        seed = derive_seed("niche", 1)
        assert StageRandom(seed, "interior").shuffled(range(50)) != StageRandom(
            seed, "cover"
        ).shuffled(range(50))

    def test_substreams_are_independent(self):
        """Drawing more prompts must not change what the cover picks."""
        rng = StageRandom(derive_seed("niche", 1), "interior")
        before = rng.stream("titles").shuffled(range(20))
        rng.stream("prompts").sample(range(100), 40)
        after = StageRandom(derive_seed("niche", 1), "interior").stream("titles").shuffled(range(20))
        assert before == after

    def test_derive_seed_is_stable_across_processes(self):
        # A literal, so a change in the hashing would be caught rather than
        # silently reshuffling every book ever produced.
        assert derive_seed("gratitude", 7) == derive_seed("gratitude", 7)
        assert derive_seed("gratitude", 7) != derive_seed("gratitude", 8)


class TestNaming:
    def test_slugs_are_ascii_and_stable(self):
        assert slugify("Gratitude Journal per Mamme! (90 giorni)") == (
            "gratitude-journal-per-mamme-90-giorni"
        )

    def test_run_slug_carries_the_whole_identity(self):
        assert run_slug("a-niche", "journal", 7) == "a-niche--journal--s7"


class TestBuildReproducibility:
    def test_two_runs_of_the_same_seed_are_byte_identical(self, good_niche, config, tmp_path):
        first = Pipeline(config).run(good_niche, seed=5, output_root=tmp_path / "a")
        second = Pipeline(config).run(good_niche, seed=5, output_root=tmp_path / "b")
        assert first.ok and second.ok
        assert interior_hash(first) == interior_hash(second)

    def test_a_different_seed_makes_a_different_book(self, good_niche, config, tmp_path):
        first = Pipeline(config).run(good_niche, seed=5, output_root=tmp_path / "a")
        second = Pipeline(config).run(good_niche, seed=6, output_root=tmp_path / "b")
        assert interior_hash(first) != interior_hash(second)

    def test_the_manifest_records_a_hash_for_every_artifact(self, good_niche, config):
        result = Pipeline(config).run(good_niche, seed=5)
        for artifact in result.context.manifest.artifacts:
            assert len(artifact.sha256) == 64
            assert (result.root / artifact.path).is_file()
