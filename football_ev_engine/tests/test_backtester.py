import numpy as np
import pandas as pd
import pytest

from src.backtest.backtester import BacktestConfig, brier, log_loss, max_drawdown, run_backtest
from src.engine.value import ValueRules


def test_brier_and_log_loss():
    p = np.array([[1.0, 0, 0], [0.5, 0.25, 0.25]])
    y = np.array([0, 1])
    assert brier(p, y) == pytest.approx((0 + (0.25 + 0.5625 + 0.0625)) / 2)
    assert log_loss(np.array([[0.5, 0.5, 0.0]]), np.array([0])) == pytest.approx(np.log(2))


def test_max_drawdown():
    assert max_drawdown(pd.Series([100, 120, 90, 130, 104])) == pytest.approx(0.25)
    assert max_drawdown(pd.Series([100, 101, 102])) == 0.0


@pytest.fixture(scope="module")
def result(synthetic_matches):
    cfg = BacktestConfig(seasons=["2024-2025"], rules=ValueRules(model_weight=0.5), refit_every_days=14)
    return run_backtest(synthetic_matches, cfg)


def test_backtest_runs_and_reports(result):
    m = result.metrics
    for key in ("bets", "yield_pct", "roi_pct", "max_drawdown_pct", "brier_dc", "brier_mkt", "brier_blend",
                "brier_ml", "avg_clv_pct"):
        assert key in m, key
    assert m["matches_priced"] == 380
    assert m["bets"] > 0
    b = result.bets
    # Stakes are sized on the bankroll at the start of each match day.
    before = b["bankroll"] - b["profit"]
    opening = before.groupby(b["match_date"]).transform("first")
    assert (b["stake_pct"] <= 0.025).all()
    assert np.allclose(b["stake"], (b["stake_pct"] * opening).round(2))
    assert b["price"].between(1.40, 4.50).all() and (b["p_model"] >= 0.35).all() and (b["ev"] >= 0.03).all()
    assert m["final_bankroll"] == pytest.approx(1000 + b["profit"].sum())
    assert result.equity.iloc[-1] == pytest.approx(m["final_bankroll"])


def test_sharp_market_is_best_calibrated(result):
    # Synthetic Pinnacle prices are the true probabilities minus a small margin.
    m = result.metrics
    assert m["brier_mkt"] <= m["brier_dc"] + 1e-3


def test_no_lookahead(synthetic_matches):
    """Rewriting results after a date must not change bets placed before it."""
    cfg = BacktestConfig(seasons=["2024-2025"], rules=ValueRules(model_weight=0.5), refit_every_days=14, ml_kind=None)
    base = run_backtest(synthetic_matches, cfg).bets
    cutoff = pd.Timestamp("2025-01-01")
    altered = synthetic_matches.copy()
    later = pd.to_datetime(altered["match_date"]) >= cutoff
    altered.loc[later, ["fthg", "ftag"]] = altered.loc[later, ["ftag", "fthg"]].to_numpy()
    altered.loc[later, "ftr"] = altered.loc[later, "ftr"].map({"H": "A", "A": "H", "D": "D"})
    new = run_backtest(altered, cfg).bets
    cols = ["match_date", "home_team", "market", "outcome", "price", "p_model", "stake"]
    pd.testing.assert_frame_equal(base.loc[base["match_date"] < cutoff, cols].reset_index(drop=True),
                                  new.loc[new["match_date"] < cutoff, cols].reset_index(drop=True))


def test_needs_prior_history(synthetic_matches):
    with pytest.raises(ValueError):
        run_backtest(synthetic_matches, BacktestConfig(seasons=["2022-2023"]))
    with pytest.raises(ValueError):
        run_backtest(synthetic_matches, BacktestConfig(seasons=["1990-1991"]))
