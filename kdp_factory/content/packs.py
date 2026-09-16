"""Template packs — the content the generators pour the niche through.

Packs are YAML so that adding vocabulary to the factory is an edit, not a
deploy. Selection is deterministic: a bank is in or out based on whether its
tags appear in the niche text, never on chance.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from ..errors import ConfigError
from .text import similarity, token_set, token_similarity

TEMPLATES_DIR = Path(__file__).parent / "templates"
_WORD = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=None)
def load_pack(name: str) -> dict[str, Any]:
    path = TEMPLATES_DIR / f"{name}.yaml"
    if not path.is_file():
        raise ConfigError(f"template pack not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"template pack {path} must contain a mapping")
    return data


def tokens(*texts: str) -> set[str]:
    out: set[str] = set()
    for text in texts:
        out.update(_WORD.findall((text or "").lower()))
    return out


def select_banks(
    pack: dict[str, Any], haystack: Iterable[str], always: str = "core"
) -> list[str]:
    """Banks whose tags appear in the niche text, plus the always-on bank.

    Returned in pack order so the result does not depend on set iteration.
    """
    words = tokens(*haystack)
    banks = pack.get("banks") or {}
    chosen = [
        name
        for name, bank in banks.items()
        if name != always and words & {t.lower() for t in (bank.get("tags") or [])}
    ]
    if always in banks:
        chosen.append(always)
    if not chosen:
        raise ConfigError("no template bank matched and no 'core' bank exists")
    return chosen


def expand_bank(bank: dict[str, Any]) -> list[str]:
    """Every distinct line a bank can produce: written prompts + frame×subject.

    A frame is either a string, which combines with every subject in the bank,
    or a mapping with its own ``subjects`` — because some frames only fit some
    subjects. "Where in your body do you notice {subject}?" wants a feeling, and
    pairing it with "your hands" produces a question nobody would ask.
    """
    out: list[str] = list(bank.get("prompts") or [])
    subjects = bank.get("subjects") or []
    for frame in bank.get("frames") or []:
        if isinstance(frame, dict):
            text = str(frame.get("text", ""))
            frame_subjects = frame.get("subjects") or subjects
        else:
            text, frame_subjects = str(frame), subjects
        if not text:
            continue
        if "{subject}" not in text:
            out.append(text)
            continue
        out.extend(text.replace("{subject}", subject) for subject in frame_subjects)
    return out


def select_diverse(
    candidates: Sequence[str], count: int, max_similarity: float, label: str = "items"
) -> list[str]:
    """Take ``count`` lines, none of which reads like another.

    Frames multiply cheaply — "…about your morning…" and "…about your evening…"
    are one idea, not two — and a buyer notices. So the generator holds itself
    to the same near-duplicate bar the substance gate will apply, greedily, in
    the order it was handed (which the caller has already seeded-shuffled).

    Raises when the pool cannot fill the book, instead of quietly shipping
    padding: that is a content problem to fix in the template pack.
    """
    chosen: list[str] = []
    chosen_tokens: list[set[str]] = []
    for candidate in candidates:
        tokens_here = token_set(candidate)
        if any(
            token_similarity(tokens_here, other) > max_similarity
            for other in chosen_tokens
        ):
            continue
        chosen.append(candidate)
        chosen_tokens.append(tokens_here)
        if len(chosen) == count:
            return chosen
    raise ConfigError(
        f"only {len(chosen)} of {count} {label} could be drawn without two of them "
        f"overlapping more than {max_similarity:.0%} — the pool holds "
        f"{len(candidates)} lines but too many are variations of each other. "
        f"Add distinct lines to the template pack, or lower target_pages."
    )


def pool_from_banks(pack: dict[str, Any], bank_names: Iterable[str]) -> list[str]:
    """Deduplicated pool across the selected banks, in a stable order."""
    banks = pack.get("banks") or {}
    seen: set[str] = set()
    pool: list[str] = []
    for name in bank_names:
        bank = banks.get(name)
        if not bank:
            continue
        for line in expand_bank(bank):
            key = " ".join(_WORD.findall(line.lower()))
            if key in seen:
                continue
            seen.add(key)
            pool.append(line)
    return pool
