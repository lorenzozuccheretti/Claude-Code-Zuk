"""Seeded randomness, scoped per stage.

The pack asks for determinism wherever it is available. Content generation is
random-looking but reproducible: the same (run seed, stage) pair always yields
the same stream, and adding a new stage never shifts an existing one — each
stage derives its own seed from a hash of its name.
"""

from __future__ import annotations

import hashlib
import random
from typing import Iterable, Sequence, TypeVar

T = TypeVar("T")


def derive_seed(*parts: object) -> int:
    """A 64-bit seed from any parts, stable across processes and platforms.

    ``random.seed(str)`` is stable too, but hashing the parts explicitly keeps
    the derivation visible and lets stages be namespaced without collisions.
    """
    payload = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


class StageRandom:
    """A ``random.Random`` bound to one stage of one run.

    Use ``stream("prompts")`` to branch a sub-stream rather than consuming the
    parent: that way, changing how many prompts you draw does not change what
    the cover picks.
    """

    def __init__(self, run_seed: int, stage: str) -> None:
        self.run_seed = run_seed
        self.stage = stage
        self._rng = random.Random(derive_seed(run_seed, stage))

    def stream(self, name: str) -> "StageRandom":
        return StageRandom(self.run_seed, f"{self.stage}/{name}")

    @property
    def random(self) -> random.Random:
        return self._rng

    def choice(self, seq: Sequence[T]) -> T:
        return self._rng.choice(seq)

    def sample(self, population: Sequence[T], k: int) -> list[T]:
        return self._rng.sample(list(population), k)

    def shuffled(self, items: Iterable[T]) -> list[T]:
        out = list(items)
        self._rng.shuffle(out)
        return out

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)

    def cycle_without_repeats(self, population: Sequence[T], k: int) -> list[T]:
        """Draw k items, reshuffling the pool each pass instead of repeating.

        With k larger than the pool this still repeats — but never twice in a
        row and never in the same order, which is what the repetition check in
        the substance gate is looking for.
        """
        if not population:
            raise ValueError("cannot draw from an empty population")
        out: list[T] = []
        pool: list[T] = []
        while len(out) < k:
            if not pool:
                pool = self.shuffled(population)
                if out and pool[0] == out[-1] and len(pool) > 1:
                    pool[0], pool[-1] = pool[-1], pool[0]
            out.append(pool.pop(0))
        return out
