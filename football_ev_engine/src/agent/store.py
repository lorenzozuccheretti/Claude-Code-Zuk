"""Persistence for dispatched picks: dedup, daily cap, settlement, track record.

A pick is written as ``sending`` *before* the Telegram call and flipped to
``sent`` after it succeeds (or ``failed`` if it doesn't). A crash between
the two leaves a ``sending`` row, which still blocks a resend: missing one
message is better than sending the same pick twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from src.engine.devig import devig

ACTIVE = ("sending", "sent")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def fixture_key(league: str, home: str, away: str, commence_time: datetime) -> str:
    """Stable identity of a fixture across odds snapshots (event ids can change)."""
    return f"{league}|{home}|{away}|{pd.Timestamp(commence_time).date().isoformat()}"


def blocked_fixtures(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute(
        f"SELECT fixture_key FROM sent_picks WHERE status IN {ACTIVE}"
    ).fetchall()
    return {r[0] for r in rows}


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC-naive [start, end) of a calendar day in ``tz``."""
    start = datetime.combine(day, datetime.min.time(), tz)
    end = start + timedelta(days=1)
    return (start.astimezone(timezone.utc).replace(tzinfo=None),
            end.astimezone(timezone.utc).replace(tzinfo=None))


def sent_count_on(con: duckdb.DuckDBPyConnection, day: date, tz: ZoneInfo) -> int:
    start, end = local_day_bounds(day, tz)
    return con.execute(
        f"SELECT count(*) FROM sent_picks WHERE status IN {ACTIVE} AND sent_at >= ? AND sent_at < ?",
        [start, end],
    ).fetchone()[0]


def record_sending(con: duckdb.DuckDBPyConnection, row: dict) -> None:
    """Insert (or retry a previously failed) pick in the ``sending`` state."""
    con.execute("DELETE FROM sent_picks WHERE fixture_key = ? AND status = 'failed'", [row["fixture_key"]])
    cols = list(row) + ["status", "sent_at"]
    values = list(row.values()) + ["sending", utcnow()]
    con.execute(
        f"INSERT INTO sent_picks ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", values
    )


def mark(con: duckdb.DuckDBPyConnection, key: str, status: str, message_id: int | None = None) -> None:
    con.execute(
        "UPDATE sent_picks SET status = ?, telegram_message_id = coalesce(?, telegram_message_id) "
        "WHERE fixture_key = ?",
        [status, message_id, key],
    )


# --- once-a-day events -------------------------------------------------------


def event_done(con: duckdb.DuckDBPyConnection, kind: str, day: date) -> bool:
    return con.execute(
        "SELECT count(*) FROM agent_events WHERE kind = ? AND local_date = ?", [kind, day]
    ).fetchone()[0] > 0


def record_event(con: duckdb.DuckDBPyConnection, kind: str, day: date, detail: str = "") -> None:
    con.execute(
        "INSERT OR REPLACE INTO agent_events VALUES (?, ?, ?, ?)", [kind, day, utcnow(), detail]
    )


def event_done_since(con: duckdb.DuckDBPyConnection, kind: str, day: date) -> bool:
    return con.execute(
        "SELECT count(*) FROM agent_events WHERE kind = ? AND local_date >= ?", [kind, day]
    ).fetchone()[0] > 0


# --- settlement ---------------------------------------------------------------

_CLOSE_COLS = {
    "h2h": (("home", "draw", "away"), ("psch", "pscd", "psca")),
    "totals": (("over", "under"), ("psc_over25", "psc_under25")),
}


def settle(con: duckdb.DuckDBPyConnection, now: datetime | None = None) -> int:
    """Grade sent picks whose match now appears in the football-data history.

    Profit is per 1 unit staked. CLV compares the price taken with Pinnacle's
    de-vigged closing line when football-data has it (h2h and totals 2.5).
    Returns the number of picks settled.
    """
    now = now or utcnow()
    # Picks graded from live scores still lack CLV until football-data has the closing prices.
    pending = con.execute(
        "SELECT * FROM sent_picks WHERE status = 'sent' AND (won IS NULL OR clv IS NULL) AND commence_time < ?",
        [now],
    ).df()
    settled = 0
    for p in pending.itertuples(index=False):
        kick = pd.Timestamp(p.commence_time)
        match = con.execute(
            "SELECT * FROM matches WHERE league = ? AND home_team = ? AND away_team = ? "
            "AND match_date BETWEEN ? AND ? ORDER BY coalesce(provisional, false)",
            [p.league, p.home_team, p.away_team, (kick - pd.Timedelta(days=1)).date(),
             (kick + pd.Timedelta(days=1)).date()],
        ).df()
        if match.empty:
            continue
        m = match.iloc[0]
        won = grade(p.market, p.outcome, p.point, int(m["fthg"]), int(m["ftag"]))
        clv = None
        outcomes, cols = _CLOSE_COLS.get(p.market, ((), ()))
        if cols and (p.market == "h2h" or p.point == 2.5):
            closing = [m.get(c) for c in cols]
            if all(pd.notna(x) and x > 1 for x in closing):
                fair = dict(zip(outcomes, devig(closing)))
                clv = float(p.price * fair[p.outcome] - 1)
        if pd.isna(p.won):
            con.execute(
                "UPDATE sent_picks SET won = ?, profit_units = ?, clv = ? WHERE fixture_key = ?",
                [bool(won), float(p.price - 1) if won else -1.0, clv, p.fixture_key],
            )
            settled += 1
        elif clv is not None:
            con.execute("UPDATE sent_picks SET clv = ? WHERE fixture_key = ?", [clv, p.fixture_key])
    return settled


def grade(market: str, outcome: str, point: float, home_goals: int, away_goals: int) -> bool:
    if market == "h2h":
        result = "home" if home_goals > away_goals else "away" if home_goals < away_goals else "draw"
        return result == outcome
    total = home_goals + away_goals
    return total > point if outcome == "over" else total < point


def settle_from_scores(con: duckdb.DuckDBPyConnection, scores: list[dict]) -> int:
    """Grade pending picks from The Odds API ``/scores`` payload (completed events only)."""
    by_id = {s["id"]: s for s in scores if s.get("completed") and s.get("scores")}
    if not by_id:
        return 0
    pending = con.execute(
        f"SELECT fixture_key, event_id, market, outcome, point, price FROM sent_picks "
        f"WHERE status = 'sent' AND won IS NULL AND event_id IN ({', '.join('?' for _ in by_id)})",
        list(by_id),
    ).fetchall()
    settled = 0
    for key, event_id, market, outcome, point, price in pending:
        ev = by_id[event_id]
        goals = {s["name"]: int(float(s["score"])) for s in ev["scores"]}
        if ev["home_team"] not in goals or ev["away_team"] not in goals:
            continue
        won = grade(market, outcome, point, goals[ev["home_team"]], goals[ev["away_team"]])
        con.execute("UPDATE sent_picks SET won = ?, profit_units = ? WHERE fixture_key = ?",
                    [won, float(price - 1) if won else -1.0, key])
        settled += 1
    return settled


def unsettled_sports(con: duckdb.DuckDBPyConnection, now: datetime) -> list[str]:
    """Odds API sport keys with sent picks that should have finished (kick-off 2h-3 days ago)."""
    rows = con.execute(
        "SELECT DISTINCT f.sport_key FROM sent_picks p JOIN fixtures f USING (event_id) "
        "WHERE p.status = 'sent' AND p.won IS NULL AND p.commence_time BETWEEN ? AND ?",
        [now - timedelta(days=3), now - timedelta(hours=2)],
    ).fetchall()
    return [r[0] for r in rows]


@dataclass
class TrackRecord:
    picks: int
    settled: int
    wins: int
    units: float
    avg_clv: float | None

    @property
    def yield_pct(self) -> float:
        return 100 * self.units / self.settled if self.settled else 0.0


def track_record(con: duckdb.DuckDBPyConnection) -> TrackRecord:
    row = con.execute(
        "SELECT count(*), count(won), count(*) FILTER (WHERE won), coalesce(sum(profit_units), 0), avg(clv) "
        "FROM sent_picks WHERE status = 'sent'"
    ).fetchone()
    return TrackRecord(int(row[0]), int(row[1]), int(row[2]), float(row[3]),
                       None if row[4] is None else float(row[4]))
