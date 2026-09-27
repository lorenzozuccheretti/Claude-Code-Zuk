"""Map team names from live feeds onto the historical (canonical) names.

Resolution order for a raw name within one league:

1. Cached alias in the ``team_aliases`` table (manual entries always win).
2. Exact match against the league's canonical names (case/accent-insensitive).
3. Seed alias from ``team_aliases.json`` shipped with the project, if its
   target is one of the league's canonical names.
4. ``rapidfuzz.process.extractOne`` on normalised names, accepted only at a
   score of at least ``threshold`` (default 80) and only if no other raw name
   already claimed that canonical team in the same batch.

Every successful resolution is written back to the table, so the second run
is a pure lookup. Fuzzy matches are stored with ``verified = false`` so they
can be reviewed via ``python main.py aliases``.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd
from rapidfuzz import fuzz, process

from src.data.database import upsert_df

SEED_FILE = Path(__file__).with_name("team_aliases.json")
DEFAULT_THRESHOLD = 80.0

# Tokens that carry no identity ("FC Barcelona" vs "Barcelona").
_NOISE = {
    "fc", "cf", "ac", "as", "ss", "sc", "afc", "cfc", "calcio", "club", "de", "sv",
    "vfb", "vfl", "tsg", "fsv", "bc", "1", "ud", "cd", "rc", "sd", "real", "the",
    "04", "05", "1899", "1846", "29", "hsc", "ogc", "stade", "olympique", "us",
}


def normalise(name: str) -> str:
    """Lower-case, strip accents and punctuation, drop noise tokens."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    tokens = [t for t in text.split() if t not in _NOISE]
    return " ".join(tokens) or text.strip()


def load_seed(path: Path = SEED_FILE) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


@dataclass(frozen=True)
class AliasMatch:
    raw_name: str
    canonical_name: str | None
    score: float
    method: str  # cached / exact / seed / fuzzy / manual / unmatched


class AliasMatcher:
    """Resolve raw names for one league against its canonical team list."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        league: str,
        canonical: list[str],
        source: str = "odds_api",
        threshold: float = DEFAULT_THRESHOLD,
        seed: dict[str, str] | None = None,
    ) -> None:
        self.con = con
        self.league = league
        self.source = source
        self.threshold = threshold
        self.canonical = sorted(set(canonical))
        self._norm_to_canon = {normalise(c): c for c in self.canonical}
        self._seed = load_seed() if seed is None else seed

    @classmethod
    def for_league(cls, con: duckdb.DuckDBPyConnection, league: str, **kwargs) -> "AliasMatcher":
        """Canonical names are every team that appears in the league's history."""
        rows = con.execute(
            "SELECT home_team FROM matches WHERE league = ? UNION SELECT away_team FROM matches WHERE league = ?",
            [league, league],
        ).fetchall()
        return cls(con, league, [r[0] for r in rows], **kwargs)

    def _cached(self, raw: str) -> AliasMatch | None:
        row = self.con.execute(
            "SELECT canonical_name, score, method FROM team_aliases WHERE league = ? AND source = ? AND raw_name = ?",
            [self.league, self.source, raw],
        ).fetchone()
        if row is None:
            return None
        # A cached target that disappeared from the history (e.g. data wiped) is stale.
        if row[0] not in self.canonical:
            return None
        return AliasMatch(raw, row[0], float(row[1]), "manual" if row[2] == "manual" else "cached")

    def _save(self, match: AliasMatch) -> None:
        verified = match.method in ("exact", "seed", "manual")
        self.con.execute(
            "INSERT OR REPLACE INTO team_aliases "
            "(league, source, raw_name, canonical_name, score, method, verified, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, current_timestamp)",
            [self.league, self.source, match.raw_name, match.canonical_name, match.score, match.method, verified],
        )

    def _deterministic(self, raw: str) -> AliasMatch | None:
        """Cache, exact or seed hit; ``None`` if only fuzzy matching is left."""
        if (hit := self._cached(raw)) is not None:
            return hit
        norm = normalise(raw)
        if raw in self.canonical:
            return AliasMatch(raw, raw, 100.0, "exact")
        if norm in self._norm_to_canon:
            return AliasMatch(raw, self._norm_to_canon[norm], 100.0, "exact")
        if self._seed.get(raw) in self.canonical:
            return AliasMatch(raw, self._seed[raw], 100.0, "seed")
        return None

    def _fuzzy(self, raw: str, taken: set[str]) -> AliasMatch | None:
        choices = {c: normalise(c) for c in self.canonical if c not in taken}
        best = process.extractOne(normalise(raw), choices, scorer=fuzz.WRatio, score_cutoff=self.threshold)
        if best is None:
            return None
        _, score, canon = best
        return AliasMatch(raw, canon, float(score), "fuzzy")

    def match(self, raw: str, taken: set[str] | None = None) -> AliasMatch:
        """Resolve one name. ``taken`` holds canonical names already claimed."""
        result = self._deterministic(raw) or self._fuzzy(raw, taken or set())
        if result is None:
            return AliasMatch(raw, None, 0.0, "unmatched")
        if result.method not in ("cached", "manual"):
            self._save(result)
        return result

    def match_many(self, raws: list[str]) -> dict[str, AliasMatch]:
        """Resolve a batch, keeping the mapping one-to-one.

        Deterministic matches are resolved first so a fuzzy guess can never
        steal a team that another raw name maps to exactly.
        """
        unique = list(dict.fromkeys(raws))
        out: dict[str, AliasMatch] = {}
        for raw in unique:
            if self._deterministic(raw) is not None:
                out[raw] = self.match(raw)
        taken = {m.canonical_name for m in out.values() if m.canonical_name}
        for raw in unique:
            if raw in out:
                continue
            out[raw] = m = self.match(raw, taken)
            if m.canonical_name:
                taken.add(m.canonical_name)
        return {raw: out[raw] for raw in unique}


def set_manual_alias(
    con: duckdb.DuckDBPyConnection, league: str, raw: str, canonical: str, source: str = "odds_api"
) -> None:
    """Pin a mapping by hand; it overrides every automatic method."""
    con.execute(
        "INSERT OR REPLACE INTO team_aliases "
        "(league, source, raw_name, canonical_name, score, method, verified, updated_at) "
        "VALUES (?, ?, ?, ?, 100, 'manual', true, current_timestamp)",
        [league, source, raw, canonical],
    )


def export_aliases(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    """Write the alias table to JSON so verified matches survive a DB rebuild."""
    df = con.execute("SELECT * EXCLUDE (updated_at) FROM team_aliases ORDER BY league, raw_name").df()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(df.to_json(orient="records", indent=2, force_ascii=False), encoding="utf-8")
    return len(df)


def import_aliases(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    """Load a JSON export back into the table (existing rows are replaced)."""
    if not path.exists():
        return 0
    df = pd.read_json(path, orient="records")
    if df.empty:
        return 0
    df["updated_at"] = pd.Timestamp.now()
    return upsert_df(con, "team_aliases", df)
