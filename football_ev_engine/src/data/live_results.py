"""Fresh results from The Odds API ``/scores``, ahead of the slow history sources.

football-data.co.uk updates a couple of times a week and the national-team
dataset can lag by weeks, so the model would otherwise train on stale form.
Completed events we stored as fixtures are written to ``matches`` as
*provisional* rows (score only, no shots or prices). When the official
source publishes the same match, :func:`purge_superseded` drops the
provisional copy; its date may differ by a day (UTC vs local kick-off date).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import pandas as pd

from src.data.database import upsert_df

# /scores only reaches 3 days back; keep a margin for the run's own timing.
SCORES_WINDOW = timedelta(days=3)
FINISHED_AFTER = timedelta(hours=2)
# Fetch once the oldest missing result is this old, before it leaves the window.
FETCH_BY_AGE = timedelta(hours=40)


def season_label(league: str, day: date) -> str:
    if league == "INT":
        return str(day.year)
    start = day.year if day.month >= 7 else day.year - 1
    return f"{start}-{start + 1}"


def _missing(con: duckdb.DuckDBPyConnection, now: datetime) -> pd.DataFrame:
    """Stored fixtures that have finished but have no result in ``matches`` yet."""
    return con.execute(
        "SELECT f.event_id, f.league, f.sport_key, f.commence_time FROM fixtures f "
        "WHERE f.home_team IS NOT NULL AND f.away_team IS NOT NULL "
        "AND f.commence_time BETWEEN ? AND ? AND NOT EXISTS ("
        "  SELECT 1 FROM matches m WHERE m.league = f.league AND m.home_team = f.home_team "
        "  AND m.away_team = f.away_team "
        "  AND m.match_date BETWEEN CAST(f.commence_time AS DATE) - 1 AND CAST(f.commence_time AS DATE) + 1)",
        [now - SCORES_WINDOW, now - FINISHED_AFTER],
    ).df()


def sports_to_refresh(con: duckdb.DuckDBPyConnection, now: datetime, horizon: timedelta) -> list[str]:
    """Sport keys worth a ``/scores`` call (2 credits each) on this run.

    A league is fetched when its round is over (no more stored fixtures
    before the next run, ``horizon`` away) or when its oldest missing result
    is about to fall out of the 3-day window, so a weekend round costs one
    call instead of one per day.
    """
    missing = _missing(con, now)
    if missing.empty:
        return []
    upcoming = {r[0] for r in con.execute(
        "SELECT DISTINCT sport_key FROM fixtures WHERE commence_time BETWEEN ? AND ?",
        [now - FINISHED_AFTER, now + horizon]).fetchall()}
    keys = []
    for key, g in missing.groupby("sport_key"):
        oldest = pd.Timestamp(g["commence_time"].min()).to_pydatetime()
        if key not in upcoming or now - oldest >= FETCH_BY_AGE:
            keys.append(key)
    return sorted(keys)


def results_from_scores(con: duckdb.DuckDBPyConnection, scores: list[dict], now: datetime) -> pd.DataFrame:
    """Provisional ``matches`` rows for completed events still missing a result."""
    by_id = {s["id"]: s for s in scores if s.get("completed") and s.get("scores")}
    if not by_id:
        return pd.DataFrame()
    missing = set(_missing(con, now)["event_id"])
    fixtures = con.execute(
        f"SELECT * FROM fixtures WHERE event_id IN ({', '.join('?' for _ in by_id)})", list(by_id)).df()
    rows = []
    for f in fixtures.itertuples(index=False):
        ev = by_id[f.event_id]
        if f.event_id not in missing:
            continue
        try:
            goals = {s["name"]: int(float(s["score"])) for s in ev["scores"]}
            hg, ag = goals[f.home_team_raw], goals[f.away_team_raw]
        except (KeyError, TypeError, ValueError):
            continue
        day = pd.Timestamp(f.commence_time).date()
        rows.append({
            "league": f.league, "season": season_label(f.league, day), "match_date": day,
            "home_team": f.home_team, "away_team": f.away_team, "fthg": hg, "ftag": ag,
            "ftr": "H" if hg > ag else "A" if hg < ag else "D", "provisional": True,
        })
    return pd.DataFrame(rows)


def store_results(con: duckdb.DuckDBPyConnection, scores: list[dict], now: datetime) -> int:
    return upsert_df(con, "matches", results_from_scores(con, scores, now))


def purge_superseded(con: duckdb.DuckDBPyConnection) -> int:
    """Delete provisional rows the official source now covers (same teams, +-1 day)."""
    doomed = "FROM matches p WHERE p.provisional AND EXISTS (" \
             "SELECT 1 FROM matches o WHERE NOT coalesce(o.provisional, false) AND o.league = p.league " \
             "AND o.home_team = p.home_team AND o.away_team = p.away_team " \
             "AND o.match_date BETWEEN p.match_date - 1 AND p.match_date + 1)"
    n = con.execute(f"SELECT count(*) {doomed}").fetchone()[0]
    if n:
        con.execute(f"DELETE {doomed}")
    return n
