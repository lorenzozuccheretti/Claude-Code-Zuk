"""What this factory has already published.

A single build is deterministic and distinct on its own. Across builds it is
not: two journals in the same niche, drawn from the same pack with different
seeds, overlap by roughly a third. For a factory meant to be run fifty times,
that is a defect — book two should not be a rerun of book one.

This module reads the content units of previous runs so the generators can rule
them out. It is opt-in (``kdp build --avoid <dir>``), because excluding things
changes what a seed produces, and a reproducible build should not silently
depend on what else happens to be in your output folder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..content.text import normalize


def _plan_files(path: Path) -> list[Path]:
    """Every interior_plan.json at or under a path (a run dir, or a parent)."""
    if path.is_file():
        return [path] if path.name == "interior_plan.json" else []
    if not path.is_dir():
        return []
    direct = path / "02_interior" / "interior_plan.json"
    if direct.is_file():
        return [direct]
    return sorted(path.glob("*/02_interior/interior_plan.json"))


def load_used_content(paths: Iterable[str | Path]) -> set[str]:
    """Normalized content-unit texts from previous runs.

    Unreadable or half-finished runs are skipped rather than raising: a broken
    old run should not stop a new build.
    """
    used: set[str] = set()
    for raw in paths:
        for plan_file in _plan_files(Path(raw)):
            try:
                plan = json.loads(plan_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for unit in plan.get("content_units", []):
                text = unit.get("text", "")
                if text:
                    used.add(normalize(text))
                probe = unit.get("probe", "")
                if probe:
                    used.add(normalize(probe))
    return used


def describe(used: set[str], sources: Iterable[str | Path]) -> str:
    sources = [str(s) for s in sources]
    return (
        f"avoiding {len(used)} content unit(s) already used in "
        f"{len(sources)} source(s): {', '.join(sources)}"
    )
