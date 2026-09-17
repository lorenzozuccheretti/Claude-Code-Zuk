"""One colour world per book, chosen once.

The cover and the interior must not choose separately: a book whose inside is
sage and whose outside is plum is two objects in one binding. Both stations ask
here, with the same inputs, and get the same answer.

A niche can pin either choice through ``options.palette`` / ``options.motif``,
and an imprint can pin them for every book through the brand config.
"""

from __future__ import annotations

from ..booktypes.base import InteriorPlan
from ..config import EngineConfig
from ..content.rng import StageRandom
from ..niche import Niche
from .design import Palette, choose_motif, choose_palette


def identity_words(niche: Niche, plan: InteriorPlan) -> list[str]:
    return [
        niche.niche, niche.audience, niche.promise, plan.book_type,
        plan.title, plan.subtitle, *niche.keywords_seed,
    ]


def choose_identity(
    niche: Niche, plan: InteriorPlan, config: EngineConfig, seed: int = 0
) -> tuple[Palette, str]:
    options = niche.options or {}
    palette_key = options.get("palette") or config.brand.palette_key or None
    motif_key = options.get("motif") or config.brand.motif or None
    rng = StageRandom(seed, "cover")
    words = identity_words(niche, plan)
    return choose_palette(words, rng, palette_key), choose_motif(words, rng, motif_key)
