"""Use-case functions behind each CLI command.

They take an open DuckDB connection and :class:`Settings`, do the work and
return plain data; rendering is the CLI's job. Keeping them here makes every
command testable without a terminal.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import duckdb
import httpx
import numpy as np
import pandas as pd

from src.backtest.backtester import BacktestConfig, BacktestResult, brier, run_backtest
from src.config import League, Settings, parse_season
from src.data.alias_matcher import AliasMatcher
from src.data.database import load_matches, upsert_df
from src.data.ingest_csv import FetchResult, download_seasons
from src.data.ingest_international import download_results
from src.data.ingest_odds_api import OddsApiClient, Quota, events_to_frames, store_snapshot
from src.engine.value import ValueBet, ValueRules, scan
from src.models.dixon_coles import DixonColes, DixonColesParams, MatchProbabilities
from src.models.ml_classifier import MLBenchmark

log = logging.getLogger(__name__)
DC_MODEL = "dixon_coles"


# --- fetch-historical -------------------------------------------------------


def fetch_historical(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    leagues: list[League],
    seasons: list[str],
    client: httpx.AsyncClient | None = None,
) -> list[FetchResult]:
    """Download history for ``leagues``; each league's ``source`` decides where from."""
    results: list[FetchResult] = []
    clubs = [lg for lg in leagues if lg.source == "football-data"]
    if clubs:
        frames, results = asyncio.run(
            download_seasons(clubs, seasons, settings.football_data_base_url, client=client)
        )
        for frame in frames:
            upsert_df(con, "matches", frame)
    if any(lg.source == "international" for lg in leagues):
        # One file covers every national team; start from January of the oldest season.
        since = date(parse_season(seasons[0])[0], 1, 1)
        try:
            frame = asyncio.run(download_results(settings.international_results_url, since, client=client))
            upsert_df(con, "matches", frame)
            results.append(FetchResult("international", f"since {since}", len(frame)))
        except (httpx.HTTPError, ValueError) as exc:
            results.append(FetchResult("international", f"since {since}", 0, str(exc)))
    return results


# --- fetch-odds -------------------------------------------------------------


@dataclass
class OddsFetchReport:
    league: str
    events: int = 0
    prices: int = 0
    unmatched: list[str] = field(default_factory=list)
    fuzzy: list[tuple[str, str, float]] = field(default_factory=list)
    error: str | None = None


def fetch_odds(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    leagues: list[League],
    client: httpx.AsyncClient | None = None,
) -> tuple[list[OddsFetchReport], Quota]:
    """Pull odds for each league, map team names, store the snapshot."""

    async def run() -> tuple[list[OddsFetchReport], Quota]:
        reports = []
        async with OddsApiClient(settings, con, client=client) as api:
            active = {s.key for s in await api.sports() if s.active}
            for lg in leagues:
                report = OddsFetchReport(lg.slug)
                reports.append(report)
                if lg.odds_api_key not in active:
                    report.error = "not active on The Odds API right now (off-season?)"
                    continue
                events = await api.odds(lg.odds_api_key)
                fixtures, odds = events_to_frames(events, lg)
                if not fixtures.empty:
                    matcher = AliasMatcher.for_league(con, lg.fd_code)
                    names = list(fixtures["home_team_raw"]) + list(fixtures["away_team_raw"])
                    resolved = matcher.match_many(names)
                    fixtures["home_team"] = fixtures["home_team_raw"].map(lambda n: resolved[n].canonical_name)
                    fixtures["away_team"] = fixtures["away_team_raw"].map(lambda n: resolved[n].canonical_name)
                    report.unmatched = sorted(n for n, m in resolved.items() if m.canonical_name is None)
                    report.fuzzy = sorted((n, m.canonical_name, m.score) for n, m in resolved.items()
                                          if m.method == "fuzzy")
                store_snapshot(con, fixtures, odds)
                report.events, report.prices = len(fixtures), len(odds)
            return reports, api.quota

    return asyncio.run(run())


# --- train ------------------------------------------------------------------


@dataclass
class TrainReport:
    league: str
    matches: int
    teams: int
    home_advantage: float
    rho: float
    converged: bool
    ml_holdout: dict[str, float] = field(default_factory=dict)


def save_params(con: duckdb.DuckDBPyConnection, league: str, params: DixonColesParams) -> None:
    con.execute(
        "INSERT OR REPLACE INTO model_params VALUES (?, ?, ?, ?)",
        [DC_MODEL, league, datetime.now(timezone.utc).replace(tzinfo=None), json.dumps(params.to_dict())],
    )


def load_params(con: duckdb.DuckDBPyConnection, league: str) -> DixonColesParams | None:
    row = con.execute(
        "SELECT params FROM model_params WHERE model = ? AND league = ?", [DC_MODEL, league]
    ).fetchone()
    return None if row is None else DixonColesParams.from_dict(json.loads(row[0]))


def train(
    con: duckdb.DuckDBPyConnection, settings: Settings, league: League, ml_kind: str | None = "logreg"
) -> TrainReport:
    """Fit Dixon-Coles on the training window and the ML benchmark on all history.

    The ML model's holdout Brier (last 20% of matches, chronologically) is
    reported next to Dixon-Coles' on the same matches for comparison.
    """
    matches = load_matches(con, league.fd_code)
    if matches.empty:
        raise ValueError(f"No historical matches for {league.name}; run fetch-historical first")
    as_of = matches["match_date"].max() + pd.Timedelta(days=1)
    window_days = league.window_days or settings.training_window_days
    window = matches[matches["match_date"] >= as_of - pd.Timedelta(days=window_days)]
    xi = settings.decay_xi if league.decay_xi is None else league.decay_xi
    dc = DixonColes(xi=xi, max_goals=settings.max_goals).fit(window, as_of=as_of)
    assert dc.params is not None
    save_params(con, league.fd_code, dc.params)
    report = TrainReport(league.slug, dc.params.n_matches, len(dc.params.teams), dc.params.home_advantage,
                         dc.params.rho, dc.params.converged)

    if ml_kind:
        report.ml_holdout = _ml_holdout(matches, settings, ml_kind)
        MLBenchmark(kind=ml_kind).fit(matches).save(
            settings.resolve(settings.models_dir) / f"ml_{ml_kind}_{league.fd_code}.joblib"
        )
    return report


def _ml_holdout(matches: pd.DataFrame, settings: Settings, ml_kind: str) -> dict[str, float]:
    cut = int(len(matches) * 0.8)
    train_df, test_df = matches.iloc[:cut], matches.iloc[cut:]
    try:
        clf = MLBenchmark(kind=ml_kind).fit(train_df)
        dc = DixonColes(xi=settings.decay_xi, max_goals=settings.max_goals).fit(
            train_df, as_of=test_df["match_date"].min()
        )
    except ValueError as exc:
        log.warning("Holdout comparison skipped: %s", exc)
        return {}
    ml = clf.predict_proba_history(matches).iloc[cut:].reset_index(drop=True)
    rows, ml_rows, y = [], [], []
    for i, r in enumerate(test_df.itertuples(index=False)):
        if ml.iloc[i].isna().any() or not (dc.knows(r.home_team) and dc.knows(r.away_team)):
            continue
        rows.append(list(dc.predict(r.home_team, r.away_team).h2h().values()))
        ml_rows.append(ml.iloc[i][["H", "D", "A"]].to_numpy(dtype=float))
        y.append({"H": 0, "D": 1, "A": 2}[r.ftr])
    if not y:
        return {}
    yv = np.array(y)
    return {"n": float(len(y)), "brier_dc": brier(np.array(rows), yv), "brier_ml": brier(np.array(ml_rows), yv)}


# --- predict ----------------------------------------------------------------


@dataclass
class PredictResult:
    bets: pd.DataFrame
    notes: list[str]
    fixtures: int


def predict(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    league: League,
    rules: ValueRules,
    bankroll: float,
    now: datetime | None = None,
    save: bool = True,
) -> PredictResult:
    """Price upcoming fixtures and return the value bets that pass every filter."""
    params = load_params(con, league.fd_code)
    if params is None:
        raise ValueError(f"No trained model for {league.name}; run train first")
    model = DixonColes.from_params(params, max_goals=settings.max_goals)
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    fixtures = con.execute(
        "SELECT * FROM fixtures WHERE league = ? AND commence_time > ? ORDER BY commence_time",
        [league.fd_code, now],
    ).df()
    if fixtures.empty:
        return PredictResult(pd.DataFrame(), ["No upcoming fixtures stored; run fetch-odds first"], 0)
    odds = con.execute(
        f"SELECT * FROM odds WHERE event_id IN ({', '.join('?' for _ in fixtures['event_id'])})",
        list(fixtures["event_id"]),
    ).df()

    def price(home: str, away: str) -> MatchProbabilities | None:
        return model.predict(home, away) if model.can_price(home, away, settings.min_team_matches) else None

    bets, notes = scan(fixtures, odds, price, rules, settings.sharp_bookmaker, settings.soft_bookmakers)
    table = _bets_frame(bets, fixtures, bankroll)
    if save and not table.empty:
        rows = table.assign(created_at=now, league=league.fd_code)
        upsert_df(con, "value_bets", rows)
    return PredictResult(table, notes, len(fixtures))


def _bets_frame(bets: list[ValueBet], fixtures: pd.DataFrame, bankroll: float) -> pd.DataFrame:
    if not bets:
        return pd.DataFrame()
    df = pd.DataFrame([b.to_dict() for b in bets])
    meta = fixtures[["event_id", "home_team", "away_team", "commence_time"]]
    df = df.merge(meta, on="event_id", how="left")
    df["stake"] = (df["stake_pct"] * bankroll).round(2)
    return df.sort_values(["commence_time", "ev"], ascending=[True, False]).reset_index(drop=True)


# --- backtest ---------------------------------------------------------------


def backtest(
    con: duckdb.DuckDBPyConnection, settings: Settings, league: League, config: BacktestConfig
) -> BacktestResult:
    matches = load_matches(con, league.fd_code)
    if matches.empty:
        raise ValueError(f"No historical matches for {league.name}; run fetch-historical first")
    return run_backtest(matches, config)
