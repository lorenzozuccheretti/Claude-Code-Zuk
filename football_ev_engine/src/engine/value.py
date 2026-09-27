r"""Expected-value discovery.

For an outcome priced at soft odds :math:`O_{soft}`:

.. math::

    \text{EV} = P_{model} \times O_{soft} - 1

:math:`P_{model}` is a blend of the Dixon-Coles probability and the
de-vigged sharp (Pinnacle) probability:

.. math::

    P_{model} = w \cdot P_{DC} + (1 - w) \cdot P_{sharp}

A raw statistical model disagrees with a sharp closing line far more often
than it is right to, so taking the model at face value "finds" value almost
everywhere. Shrinking toward the sharp price keeps only the disagreements
large enough to survive. ``w = 1`` reproduces the pure-model engine; the
backtest reports Brier scores for the model, the market and the blend so
``w`` can be chosen on evidence. When no sharp price exists the de-vigged
consensus of the soft books stands in for it.

A bet is kept only if ``EV >= min_ev``, ``P_model >= min_prob`` and
``min_odds <= O_soft <= max_odds``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from src.config import Settings
from src.engine.devig import devig
from src.engine.staking import stake_fraction

H2H_OUTCOMES = ("home", "draw", "away")
TOTALS_OUTCOMES = ("over", "under")


@dataclass(frozen=True)
class ValueRules:
    min_ev: float = 0.03
    min_prob: float = 0.35
    min_odds: float = 1.40
    max_odds: float = 4.50
    model_weight: float = 0.35
    devig_method: str = "multiplicative"
    kelly_fraction: float = 0.25
    max_stake_pct: float = 0.025

    @classmethod
    def from_settings(cls, s: Settings, **overrides: float) -> "ValueRules":
        base = dict(
            min_ev=s.min_ev, min_prob=s.min_model_prob, min_odds=s.min_odds, max_odds=s.max_odds,
            model_weight=s.model_weight, devig_method=s.devig_method,
            kelly_fraction=s.kelly_fraction, max_stake_pct=s.max_stake_pct,
        )
        base.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**base)

    def accepts(self, p: float, odds: float, ev: float) -> bool:
        return ev >= self.min_ev and p >= self.min_prob and self.min_odds <= odds <= self.max_odds


def expected_value(p: float, odds: float) -> float:
    return p * odds - 1.0


def blend(p_dc: float | None, p_sharp: float | None, model_weight: float) -> float | None:
    """Linear pool of model and market; falls back to whichever exists."""
    if p_dc is None and p_sharp is None:
        return None
    if p_sharp is None:
        return p_dc
    if p_dc is None:
        return p_sharp
    return model_weight * p_dc + (1.0 - model_weight) * p_sharp


@dataclass
class ValueBet:
    event_id: str
    market: str
    outcome: str
    point: float
    bookmaker: str
    price: float
    p_dc: float | None
    p_sharp: float | None
    sharp_source: str | None  # pinnacle / consensus / None
    p_model: float
    ev: float
    stake_pct: float

    def to_dict(self) -> dict:
        return asdict(self)


def fair_from_books(
    prices: Mapping[str, Mapping[str, float]], outcomes: tuple[str, ...], method: str
) -> dict[str, float] | None:
    """Average de-vigged probabilities across books that price every outcome."""
    rows = []
    for book_prices in prices.values():
        if all(o in book_prices for o in outcomes):
            rows.append(devig([book_prices[o] for o in outcomes], method))
    if not rows:
        return None
    mean = np.mean(rows, axis=0)
    return dict(zip(outcomes, mean / mean.sum()))


def evaluate_market(
    event_id: str,
    market: str,
    point: float,
    outcomes: tuple[str, ...],
    p_dc: Mapping[str, float] | None,
    sharp: Mapping[str, float] | None,
    soft: Mapping[str, Mapping[str, float]],
    rules: ValueRules,
) -> list[ValueBet]:
    """Value bets for one complete market (e.g. an event's 1X2).

    ``sharp`` maps outcome -> sharp price; ``soft`` maps bookmaker ->
    outcome -> price. For each outcome only the best soft price is
    considered, so one selection produces at most one bet.
    """
    p_sharp: dict[str, float] | None = None
    source: str | None = None
    if sharp and all(o in sharp for o in outcomes):
        p_sharp = dict(zip(outcomes, devig([sharp[o] for o in outcomes], rules.devig_method)))
        source = "pinnacle"
    elif (consensus := fair_from_books(soft, outcomes, rules.devig_method)) is not None:
        p_sharp, source = consensus, "consensus"

    bets: list[ValueBet] = []
    for o in outcomes:
        offers = [(price, book) for book, bp in soft.items() if (price := bp.get(o)) and price > 1.0]
        if not offers:
            continue
        price, book = max(offers)
        pd_ = None if p_dc is None else p_dc.get(o)
        ps = None if p_sharp is None else p_sharp[o]
        p = blend(pd_, ps, rules.model_weight)
        if p is None:
            continue
        ev = expected_value(p, price)
        if rules.accepts(p, price, ev):
            stake = stake_fraction(p, price, rules.kelly_fraction, rules.max_stake_pct)
            bets.append(ValueBet(event_id, market, o, point, book, price, pd_, ps, source, p, ev, stake))
    return bets


def scan(
    fixtures: pd.DataFrame,
    odds: pd.DataFrame,
    predict: Callable[[str, str], object | None],
    rules: ValueRules,
    sharp_book: str,
    soft_books: list[str],
) -> tuple[list[ValueBet], list[str]]:
    """Evaluate every fixture's h2h and half-goal totals markets.

    ``predict(home, away)`` returns a
    :class:`~src.models.dixon_coles.MatchProbabilities` or ``None`` when the
    model can't price the match. Returns the bets and human-readable notes
    about fixtures that were skipped.
    """
    bets: list[ValueBet] = []
    notes: list[str] = []
    soft_set = set(soft_books)
    by_event = {eid: grp for eid, grp in odds.groupby("event_id")} if not odds.empty else {}
    for fx in fixtures.itertuples(index=False):
        label = f"{fx.home_team_raw} v {fx.away_team_raw}"
        if not fx.home_team or not fx.away_team or pd.isna(fx.home_team) or pd.isna(fx.away_team):
            notes.append(f"{label}: team name not matched to history")
            continue
        probs = predict(fx.home_team, fx.away_team)
        if probs is None:
            notes.append(f"{label}: team not in fitted model (promoted or too few matches)")
        ev_odds = by_event.get(fx.event_id)
        if ev_odds is None:
            notes.append(f"{label}: no odds")
            continue
        for (market, point), grp in ev_odds.groupby(["market", "point"]):
            if market == "h2h":
                outcomes = H2H_OUTCOMES
                p_dc = probs.h2h() if probs is not None else None
            elif market == "totals" and (point % 1) == 0.5:
                outcomes = TOTALS_OUTCOMES
                p_dc = probs.totals(point) if probs is not None else None
            else:
                continue  # integer/quarter lines need push handling; not priced
            sharp = grp[grp["bookmaker"] == sharp_book].set_index("outcome")["price"].to_dict()
            soft: dict[str, dict[str, float]] = {}
            for book, bgrp in grp[grp["bookmaker"].isin(soft_set)].groupby("bookmaker"):
                soft[book] = bgrp.set_index("outcome")["price"].to_dict()
            if probs is None and not sharp:
                continue  # neither model nor sharp line: nothing to anchor on
            bets.extend(evaluate_market(fx.event_id, market, float(point), outcomes, p_dc, sharp, soft, rules))
    return bets, notes
