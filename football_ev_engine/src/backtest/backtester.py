"""Walk-forward, out-of-sample backtest.

For each block of ``refit_every_days`` in the test seasons, Dixon-Coles is
refit on matches strictly before the block (within the training window,
time-decayed) and used to price that block's matches. Nothing from a match
day leaks into its own prediction.

Betting uses the prices football-data.co.uk records before kick-off:

* soft prices: Bet365 pre-match (``B365H/D/A``, ``B365>2.5``/``<2.5``)
* sharp prices: Pinnacle pre-match (``PSH/D/A``, ``P>2.5``/``<2.5``)

and the same :func:`src.engine.value.evaluate_market` rules as the live
engine. Stakes are fractional Kelly on the bankroll at the start of each
match day. Where Pinnacle *closing* prices exist, each bet's closing line
value (CLV) is reported: the EV of the bet price against the de-vigged
closing line, the best available evidence of real edge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.engine.devig import devig
from src.engine.value import H2H_OUTCOMES, TOTALS_OUTCOMES, ValueRules, blend, evaluate_market
from src.models.dixon_coles import DixonColes, DixonColesParams
from src.models.ml_classifier import MLBenchmark

log = logging.getLogger(__name__)

H2H_COLS = {"soft": ("b365h", "b365d", "b365a"), "sharp": ("psh", "psd", "psa"), "close": ("psch", "pscd", "psca")}
TOT_COLS = {"soft": ("b365_over25", "b365_under25"), "sharp": ("ps_over25", "ps_under25"),
            "close": ("psc_over25", "psc_under25")}
RESULT_TO_OUTCOME = {"H": "home", "D": "draw", "A": "away"}


@dataclass
class BacktestConfig:
    seasons: list[str]
    rules: ValueRules = field(default_factory=ValueRules)
    bankroll: float = 1000.0
    refit_every_days: int = 7
    training_window_days: int = 1095
    xi: float = 0.005
    max_goals: int = 10
    include_totals: bool = True
    ml_kind: str | None = "logreg"  # None skips the ML benchmark


@dataclass
class BacktestResult:
    bets: pd.DataFrame
    predictions: pd.DataFrame
    equity: pd.Series
    metrics: dict[str, float]


def _prices(row: pd.Series, cols: tuple[str, ...], outcomes: tuple[str, ...]) -> dict[str, float]:
    out = {}
    for c, o in zip(cols, outcomes):
        v = row.get(c)
        if v is not None and pd.notna(v) and v > 1.0:
            out[o] = float(v)
    return out


def _fair(row: pd.Series, cols: tuple[str, ...], outcomes: tuple[str, ...], method: str) -> dict[str, float] | None:
    prices = _prices(row, cols, outcomes)
    if len(prices) != len(outcomes):
        return None
    return dict(zip(outcomes, devig([prices[o] for o in outcomes], method)))


def brier(probs: np.ndarray, outcome_idx: np.ndarray) -> float:
    """Multi-class Brier score: mean over matches of sum_k (p_k - 1[k = outcome])^2."""
    onehot = np.eye(probs.shape[1])[outcome_idx]
    return float(((probs - onehot) ** 2).sum(axis=1).mean())


def log_loss(probs: np.ndarray, outcome_idx: np.ndarray) -> float:
    p = np.clip(probs[np.arange(len(probs)), outcome_idx], 1e-12, 1)
    return float(-np.log(p).mean())


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough fall as a fraction of the peak (0.2 = -20%)."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float(((peak - equity) / peak).max())


def run_backtest(matches: pd.DataFrame, config: BacktestConfig) -> BacktestResult:
    """Simulate the strategy over ``config.seasons`` for one league's ``matches``."""
    df = matches.copy()
    df["match_date"] = pd.to_datetime(df["match_date"])
    df = df.sort_values(["match_date", "home_team"]).reset_index(drop=True)
    test_mask = df["season"].isin(config.seasons)
    if not test_mask.any():
        raise ValueError(f"No matches for seasons {config.seasons}")
    first_test = df.loc[test_mask, "match_date"].min()
    if not (df["match_date"] < first_test).any():
        raise ValueError("Backtest needs at least one season of history before the first test season")

    rules = config.rules
    model = DixonColes(xi=config.xi, max_goals=config.max_goals)
    params: DixonColesParams | None = None
    preds: list[dict] = []
    candidates: list[dict] = []

    test = df[test_mask]
    block_start = first_test
    end = test["match_date"].max()
    while block_start <= end:
        block_end = block_start + pd.Timedelta(days=config.refit_every_days)
        block = test[(test["match_date"] >= block_start) & (test["match_date"] < block_end)]
        if not block.empty:
            window_start = block_start - pd.Timedelta(days=config.training_window_days)
            train = df[(df["match_date"] < block_start) & (df["match_date"] >= window_start)]
            try:
                model.fit(train, as_of=block_start, init=params)
                params = model.params
            except ValueError as exc:
                log.warning("Skipping block %s: %s", block_start.date(), exc)
                block_start = block_end
                continue
            for idx, row in block.iterrows():
                if not (model.knows(row["home_team"]) and model.knows(row["away_team"])):
                    continue
                probs = model.predict(row["home_team"], row["away_team"])
                h2h = probs.h2h()
                sharp_fair = _fair(row, H2H_COLS["sharp"], H2H_OUTCOMES, rules.devig_method) or _fair(
                    row, H2H_COLS["soft"], H2H_OUTCOMES, rules.devig_method)
                pred = {"row": idx, "match_date": row["match_date"], "season": row["season"],
                        "home_team": row["home_team"], "away_team": row["away_team"], "ftr": row["ftr"],
                        **{f"dc_{o}": h2h[o] for o in H2H_OUTCOMES}}
                if sharp_fair:
                    pred.update({f"mkt_{o}": sharp_fair[o] for o in H2H_OUTCOMES})
                    pred.update({f"blend_{o}": blend(h2h[o], sharp_fair[o], rules.model_weight) for o in H2H_OUTCOMES})
                preds.append(pred)

                markets = [("h2h", 0.0, H2H_OUTCOMES, h2h, H2H_COLS)]
                if config.include_totals:
                    markets.append(("totals", 2.5, TOTALS_OUTCOMES, probs.totals(2.5), TOT_COLS))
                goals = row["fthg"] + row["ftag"]
                for market, point, outcomes, p_dc, cols in markets:
                    soft = _prices(row, cols["soft"], outcomes)
                    if not soft:
                        continue
                    sharp = _prices(row, cols["sharp"], outcomes)
                    for bet in evaluate_market(str(idx), market, point, outcomes, p_dc, sharp,
                                               {"bet365": soft}, rules):
                        won = (RESULT_TO_OUTCOME[row["ftr"]] == bet.outcome if market == "h2h"
                               else (goals > point) == (bet.outcome == "over"))
                        close = _fair(row, cols["close"], outcomes, rules.devig_method)
                        candidates.append({
                            "match_date": row["match_date"], "season": row["season"],
                            "home_team": row["home_team"], "away_team": row["away_team"],
                            "market": market, "outcome": bet.outcome, "point": point, "price": bet.price,
                            "p_dc": bet.p_dc, "p_sharp": bet.p_sharp, "p_model": bet.p_model, "ev": bet.ev,
                            "stake_pct": bet.stake_pct, "won": bool(won),
                            "clv": None if close is None else bet.price * close[bet.outcome] - 1.0,
                        })
        block_start = block_end

    bets = _settle(pd.DataFrame(candidates), config.bankroll)
    predictions = pd.DataFrame(preds)
    if config.ml_kind and not predictions.empty:
        predictions = _add_ml_benchmark(df, predictions, config)
    equity = _equity(bets, config.bankroll)
    metrics = _metrics(bets, predictions, equity, config.bankroll)
    return BacktestResult(bets, predictions, equity, metrics)


def _settle(bets: pd.DataFrame, bankroll: float) -> pd.DataFrame:
    """Size stakes on each day's opening bankroll, then settle the day."""
    if bets.empty:
        return pd.DataFrame(columns=["match_date", "market", "outcome", "price", "stake", "won", "profit", "bankroll"])
    bets = bets.sort_values(["match_date", "home_team", "market", "outcome"]).reset_index(drop=True)
    stakes, profits, balances = [], [], []
    bank = bankroll
    for _, day in bets.groupby("match_date", sort=True):
        opening = bank
        for _, b in day.iterrows():
            stake = round(b["stake_pct"] * opening, 2)
            profit = stake * (b["price"] - 1.0) if b["won"] else -stake
            bank += profit
            stakes.append(stake)
            profits.append(profit)
            balances.append(bank)
    bets["stake"], bets["profit"], bets["bankroll"] = stakes, profits, balances
    return bets


def _equity(bets: pd.DataFrame, bankroll: float) -> pd.Series:
    if bets.empty:
        return pd.Series([bankroll], dtype=float)
    return pd.concat([pd.Series([bankroll]), bets["bankroll"]], ignore_index=True).astype(float)


def _add_ml_benchmark(df: pd.DataFrame, predictions: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    """Train the classifier on everything before each test season; predict that season."""
    out = predictions.copy()
    for season in config.seasons:
        season_rows = df.index[df["season"] == season]
        if season_rows.empty:
            continue
        start = df.loc[season_rows, "match_date"].min()
        try:
            clf = MLBenchmark(kind=config.ml_kind).fit(df[df["match_date"] < start])
        except ValueError as exc:
            log.warning("ML benchmark skipped for %s: %s", season, exc)
            continue
        # Features need earlier rows, so predict over history + season and keep the season.
        upto = df[df["match_date"] <= df.loc[season_rows, "match_date"].max()]
        proba = clf.predict_proba_history(upto)
        proba.index = upto.index
        sel = out["row"].isin(season_rows)
        for cls, o in zip(("H", "D", "A"), H2H_OUTCOMES):
            out.loc[sel, f"ml_{o}"] = out.loc[sel, "row"].map(proba[cls]).to_numpy()
    return out


def _metrics(bets: pd.DataFrame, preds: pd.DataFrame, equity: pd.Series, bankroll: float) -> dict[str, float]:
    m: dict[str, float] = {}
    staked = float(bets["stake"].sum()) if not bets.empty else 0.0
    profit = float(bets["profit"].sum()) if not bets.empty else 0.0
    m["matches_priced"] = float(len(preds))
    m["bets"] = float(len(bets))
    m["staked"] = staked
    m["profit"] = profit
    m["yield_pct"] = 100 * profit / staked if staked else 0.0
    m["roi_pct"] = 100 * (float(equity.iloc[-1]) - bankroll) / bankroll
    m["final_bankroll"] = float(equity.iloc[-1])
    m["max_drawdown_pct"] = 100 * max_drawdown(equity)
    if not bets.empty:
        m["hit_rate_pct"] = 100 * float(bets["won"].mean())
        m["avg_odds"] = float(bets["price"].mean())
        m["avg_ev_pct"] = 100 * float(bets["ev"].mean())
        clv = pd.to_numeric(bets["clv"], errors="coerce").dropna()
        if not clv.empty:
            m["avg_clv_pct"] = 100 * float(clv.mean())
    if not preds.empty:
        # Score every model on the same matches, or the comparison is meaningless.
        y = preds["ftr"].map({"H": 0, "D": 1, "A": 2}).to_numpy()
        groups = {
            prefix: cols
            for prefix in ("dc", "mkt", "blend", "ml")
            if set(cols := [f"{prefix}_{o}" for o in H2H_OUTCOMES]) <= set(preds.columns)
        }
        ok = np.ones(len(preds), dtype=bool)
        for cols in groups.values():
            ok &= preds[cols].notna().all(axis=1).to_numpy()
        m["matches_scored"] = float(ok.sum())
        if ok.any():
            for prefix, cols in groups.items():
                p = preds.loc[ok, cols].to_numpy(dtype=float)
                m[f"brier_{prefix}"] = brier(p, y[ok])
                m[f"logloss_{prefix}"] = log_loss(p, y[ok])
    return m
