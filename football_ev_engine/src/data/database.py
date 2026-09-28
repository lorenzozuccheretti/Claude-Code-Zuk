"""DuckDB schema and connection management.

All tables are created idempotently by :func:`init_schema`. Writes go through
small helpers that upsert pandas DataFrames, so re-running an ingest never
duplicates rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pandas as pd

SCHEMA: tuple[str, ...] = (
    # Historical results and closing/opening prices from football-data.co.uk.
    """
    CREATE TABLE IF NOT EXISTS matches (
        league      VARCHAR NOT NULL,   -- football-data code, e.g. I1
        season      VARCHAR NOT NULL,   -- 2024-2025
        match_date  DATE    NOT NULL,
        home_team   VARCHAR NOT NULL,
        away_team   VARCHAR NOT NULL,
        fthg        INTEGER NOT NULL,
        ftag        INTEGER NOT NULL,
        ftr         VARCHAR NOT NULL,   -- H / D / A
        hs  INTEGER, as_ INTEGER, hst INTEGER, ast INTEGER,
        -- Bet365 pre-match and closing 1X2
        b365h DOUBLE, b365d DOUBLE, b365a DOUBLE,
        b365ch DOUBLE, b365cd DOUBLE, b365ca DOUBLE,
        -- Pinnacle pre-match and closing 1X2
        psh DOUBLE, psd DOUBLE, psa DOUBLE,
        psch DOUBLE, pscd DOUBLE, psca DOUBLE,
        -- Market average 1X2
        avgh DOUBLE, avgd DOUBLE, avga DOUBLE,
        -- Over/Under 2.5 goals
        b365_over25 DOUBLE, b365_under25 DOUBLE,
        ps_over25 DOUBLE, ps_under25 DOUBLE,
        psc_over25 DOUBLE, psc_under25 DOUBLE,
        neutral BOOLEAN DEFAULT false,      -- played at a neutral venue (internationals)
        PRIMARY KEY (league, match_date, home_team, away_team)
    )
    """,
    # Upcoming events from The Odds API, with team names mapped to the
    # historical (canonical) naming.
    """
    CREATE TABLE IF NOT EXISTS fixtures (
        event_id       VARCHAR PRIMARY KEY,
        league         VARCHAR NOT NULL,
        sport_key      VARCHAR NOT NULL,
        commence_time  TIMESTAMP NOT NULL,
        home_team_raw  VARCHAR NOT NULL,
        away_team_raw  VARCHAR NOT NULL,
        home_team      VARCHAR,          -- NULL when no alias could be matched
        away_team      VARCHAR,
        fetched_at     TIMESTAMP NOT NULL
    )
    """,
    # Latest snapshot of every bookmaker price for those events.
    """
    CREATE TABLE IF NOT EXISTS odds (
        event_id     VARCHAR NOT NULL,
        bookmaker    VARCHAR NOT NULL,
        market       VARCHAR NOT NULL,   -- h2h / totals
        outcome      VARCHAR NOT NULL,   -- home / draw / away / over / under
        point        DOUBLE  NOT NULL DEFAULT 0,  -- totals line; 0 for h2h
        price        DOUBLE  NOT NULL,
        last_update  TIMESTAMP,
        fetched_at   TIMESTAMP NOT NULL,
        PRIMARY KEY (event_id, bookmaker, market, outcome, point)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS team_aliases (
        league          VARCHAR NOT NULL,
        source          VARCHAR NOT NULL,   -- e.g. odds_api
        raw_name        VARCHAR NOT NULL,
        canonical_name  VARCHAR NOT NULL,
        score           DOUBLE  NOT NULL,
        method          VARCHAR NOT NULL,   -- exact / seed / fuzzy / manual
        verified        BOOLEAN NOT NULL,
        updated_at      TIMESTAMP NOT NULL DEFAULT current_timestamp,
        PRIMARY KEY (league, source, raw_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_params (
        model      VARCHAR NOT NULL,
        league     VARCHAR NOT NULL,
        fitted_at  TIMESTAMP NOT NULL,
        params     JSON NOT NULL,
        PRIMARY KEY (model, league)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_usage (
        requested_at        TIMESTAMP NOT NULL,
        endpoint            VARCHAR NOT NULL,
        status_code         INTEGER NOT NULL,
        requests_used       INTEGER,
        requests_remaining  INTEGER,
        last_cost           INTEGER
    )
    """,
    # Picks the Telegram agent has dispatched. fixture_key is unique so the
    # same fixture is never sent twice, whatever the market.
    """
    CREATE TABLE IF NOT EXISTS sent_picks (
        fixture_key    VARCHAR PRIMARY KEY,  -- league|home|away|kickoff date
        event_id       VARCHAR NOT NULL,
        league         VARCHAR NOT NULL,
        home_team      VARCHAR NOT NULL,
        away_team      VARCHAR NOT NULL,
        commence_time  TIMESTAMP NOT NULL,   -- UTC
        market         VARCHAR NOT NULL,
        outcome        VARCHAR NOT NULL,
        point          DOUBLE NOT NULL,
        bookmaker      VARCHAR NOT NULL,
        price          DOUBLE NOT NULL,
        p_model        DOUBLE NOT NULL,
        p_dc           DOUBLE,
        p_sharp        DOUBLE,
        ev             DOUBLE NOT NULL,
        stake_pct      DOUBLE NOT NULL,
        reasoning      VARCHAR,
        status         VARCHAR NOT NULL,     -- sending / sent / failed
        sent_at        TIMESTAMP NOT NULL,   -- UTC
        telegram_message_id BIGINT,
        -- Filled in by settlement once football-data has the result.
        won            BOOLEAN,
        profit_units   DOUBLE,               -- per 1 unit staked
        clv            DOUBLE                -- EV vs Pinnacle's de-vigged closing line
    )
    """,
    # Once-a-day agent events (daily run, no-pick notice, weekly report), so a
    # backup run or a retry never repeats them.
    """
    CREATE TABLE IF NOT EXISTS agent_events (
        kind        VARCHAR NOT NULL,
        local_date  DATE NOT NULL,
        created_at  TIMESTAMP NOT NULL,
        detail      VARCHAR,
        PRIMARY KEY (kind, local_date)
    )
    """,
    # Every bet the predict command recommends, for later review.
    """
    CREATE TABLE IF NOT EXISTS value_bets (
        created_at   TIMESTAMP NOT NULL,
        event_id     VARCHAR NOT NULL,
        league       VARCHAR NOT NULL,
        home_team    VARCHAR NOT NULL,
        away_team    VARCHAR NOT NULL,
        commence_time TIMESTAMP NOT NULL,
        market       VARCHAR NOT NULL,
        outcome      VARCHAR NOT NULL,
        point        DOUBLE NOT NULL,
        bookmaker    VARCHAR NOT NULL,
        price        DOUBLE NOT NULL,
        p_model      DOUBLE NOT NULL,
        p_dc         DOUBLE,
        p_sharp      DOUBLE,
        ev           DOUBLE NOT NULL,
        stake_pct    DOUBLE NOT NULL,
        PRIMARY KEY (event_id, market, outcome, point, bookmaker, created_at)
    )
    """,
)


def connect(path: Path | str, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open (and create the folder for) a DuckDB database. ``":memory:"`` works too."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


@contextmanager
def session(path: Path | str) -> Iterator[duckdb.DuckDBPyConnection]:
    """Context-managed connection with the schema guaranteed to exist."""
    con = connect(path)
    try:
        init_schema(con)
        yield con
    finally:
        con.close()


# Columns added after the first release; ALTER keeps existing databases in step.
MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS neutral BOOLEAN DEFAULT false",
)


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in SCHEMA:
        con.execute(ddl)
    for ddl in MIGRATIONS:
        con.execute(ddl)


def table_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [row[0] for row in con.execute(f"DESCRIBE {table}").fetchall()]


def upsert_df(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    """Insert rows, replacing any that collide on the primary key.

    Columns missing from ``df`` are left NULL/default; extra columns are
    ignored. Returns the number of rows written.
    """
    if df.empty:
        return 0
    cols = [c for c in table_columns(con, table) if c in df.columns]
    frame = df[cols]
    con.register("_upsert_src", frame)
    try:
        col_list = ", ".join(cols)
        con.execute(f"INSERT OR REPLACE INTO {table} ({col_list}) SELECT {col_list} FROM _upsert_src")
    finally:
        con.unregister("_upsert_src")
    return len(frame)


def load_matches(
    con: duckdb.DuckDBPyConnection,
    league: str | None = None,
    seasons: list[str] | None = None,
) -> pd.DataFrame:
    """Historical matches ordered by date, optionally filtered."""
    where, params = [], []
    if league:
        where.append("league = ?")
        params.append(league)
    if seasons:
        where.append(f"season IN ({', '.join('?' for _ in seasons)})")
        params.extend(seasons)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    df = con.execute(
        f"SELECT * FROM matches {clause} ORDER BY match_date, home_team", params
    ).df()
    if not df.empty:
        df["match_date"] = pd.to_datetime(df["match_date"])
    return df
