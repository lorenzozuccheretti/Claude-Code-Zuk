from datetime import datetime

import pandas as pd
import pytest

from src.engine.value import ValueRules, blend, evaluate_market, expected_value, fair_from_books, scan

RULES = ValueRules(model_weight=1.0)  # pure model unless a test says otherwise


def test_expected_value():
    assert expected_value(0.5, 2.2) == pytest.approx(0.1)


def test_blend():
    assert blend(0.6, 0.4, 0.25) == pytest.approx(0.45)
    assert blend(None, 0.4, 0.25) == 0.4
    assert blend(0.6, None, 0.25) == 0.6
    assert blend(None, None, 0.5) is None


@pytest.mark.parametrize("p,odds,ok", [
    (0.50, 2.20, True),    # EV 10%
    (0.50, 2.05, False),   # EV 2.5% < 3%
    (0.34, 3.50, False),   # EV 19% but P < 0.35
    (0.80, 1.35, False),   # odds below 1.40
    (0.36, 4.60, False),   # odds above 4.50
    (0.35, 2.95, True),    # boundaries: P = 0.35, EV = 3.25%
])
def test_filters(p, odds, ok):
    assert RULES.accepts(p, odds, expected_value(p, odds)) is ok


def test_best_soft_price_and_stake():
    soft = {"bet365": {"home": 2.10, "draw": 3.3, "away": 3.9}, "unibet": {"home": 2.25, "draw": 3.2, "away": 3.8}}
    bets = evaluate_market("e1", "h2h", 0.0, ("home", "draw", "away"),
                           {"home": 0.50, "draw": 0.26, "away": 0.24}, None, soft, RULES)
    assert [(b.outcome, b.bookmaker, b.price) for b in bets] == [("home", "unibet", 2.25)]
    b = bets[0]
    assert b.ev == pytest.approx(0.125)
    assert b.stake_pct == pytest.approx(0.25 * (0.5 * 1.25 - 0.5) / 1.25)


def test_sharp_price_is_devigged_and_blended():
    sharp = {"home": 1.95, "draw": 3.6, "away": 4.2}
    soft = {"bet365": {"home": 2.30, "draw": 3.4, "away": 3.8}}
    rules = ValueRules(model_weight=0.5)
    bets = evaluate_market("e1", "h2h", 0.0, ("home", "draw", "away"),
                           {"home": 0.50, "draw": 0.25, "away": 0.25}, sharp, soft, rules)
    implied = [1 / 1.95, 1 / 3.6, 1 / 4.2]
    p_pin_home = implied[0] / sum(implied)
    assert len(bets) == 1 and bets[0].sharp_source == "pinnacle"
    assert bets[0].p_sharp == pytest.approx(p_pin_home)
    assert bets[0].p_model == pytest.approx(0.5 * 0.5 + 0.5 * p_pin_home)


def test_incomplete_sharp_falls_back_to_consensus():
    soft = {"a": {"home": 2.0, "draw": 3.5, "away": 4.0}, "b": {"home": 2.4, "draw": 3.4, "away": 3.6}}
    cons = fair_from_books(soft, ("home", "draw", "away"), "multiplicative")
    bets = evaluate_market("e", "h2h", 0.0, ("home", "draw", "away"), {"home": 0.5, "draw": 0.25, "away": 0.25},
                           {"home": 1.9}, soft, ValueRules(model_weight=0.5))
    assert bets and all(b.sharp_source == "consensus" for b in bets)
    assert bets[0].p_sharp == pytest.approx(cons[bets[0].outcome])


class _Probs:
    def h2h(self):
        return {"home": 0.55, "draw": 0.25, "away": 0.20}

    def totals(self, line):
        return {"over": 0.60, "under": 0.40, "push": 0.0}


def test_scan_prices_h2h_and_half_goal_totals_only():
    fixtures = pd.DataFrame([
        {"event_id": "e1", "home_team_raw": "Inter Milan", "away_team_raw": "AC Milan", "home_team": "Inter",
         "away_team": "Milan", "commence_time": datetime(2030, 1, 1)},
        {"event_id": "e2", "home_team_raw": "X", "away_team_raw": "Y", "home_team": None, "away_team": None,
         "commence_time": datetime(2030, 1, 1)},
    ])
    rows = []
    for book in ("pinnacle", "unibet_eu"):
        bump = 0.0 if book == "pinnacle" else 0.25
        rows += [("e1", book, "h2h", "home", 0.0, 1.80 + bump), ("e1", book, "h2h", "draw", 0.0, 3.8),
                 ("e1", book, "h2h", "away", 0.0, 4.6),
                 ("e1", book, "totals", "over", 2.5, 1.70 + bump), ("e1", book, "totals", "under", 2.5, 2.2),
                 ("e1", book, "totals", "over", 3.0, 2.5), ("e1", book, "totals", "under", 3.0, 1.5)]
    odds = pd.DataFrame(rows, columns=["event_id", "bookmaker", "market", "outcome", "point", "price"])
    bets, notes = scan(fixtures, odds, lambda h, a: _Probs(), ValueRules(model_weight=0.5), "pinnacle", ["unibet_eu"])
    assert {(b.market, b.outcome, b.point) for b in bets} == {("h2h", "home", 0.0), ("totals", "over", 2.5)}
    assert any("not matched" in n for n in notes)


def test_scan_without_model_uses_sharp_only():
    fixtures = pd.DataFrame([{"event_id": "e1", "home_team_raw": "A", "away_team_raw": "B", "home_team": "A",
                              "away_team": "B", "commence_time": datetime(2030, 1, 1)}])
    odds = pd.DataFrame([("e1", "pinnacle", "h2h", "home", 0.0, 2.0), ("e1", "pinnacle", "h2h", "draw", 0.0, 3.6),
                         ("e1", "pinnacle", "h2h", "away", 0.0, 3.9), ("e1", "soft", "h2h", "home", 0.0, 2.3)],
                        columns=["event_id", "bookmaker", "market", "outcome", "point", "price"])
    bets, notes = scan(fixtures, odds, lambda h, a: None, ValueRules(), "pinnacle", ["soft"])
    assert len(bets) == 1 and bets[0].p_dc is None and bets[0].p_model == bets[0].p_sharp
    assert any("not in fitted model" in n for n in notes)
