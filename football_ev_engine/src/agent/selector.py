"""Turn the value scan into at most ``MAX_DAILY_PICKS`` picks.

Rules, in order:

1. Kick-off between ``min_lead_minutes`` and ``pick_horizon_hours`` from now.
2. The fixture has never been sent before (any market).
3. One pick per fixture: a home win and an under on the same match are
   correlated, so only the better-ranked one survives.
4. Rank by ``EV x P_model`` and keep the top ``remaining`` slots, where
   ``remaining = max_daily_picks - picks already sent today``.

The value rules themselves (odds window, EV floor) are applied upstream by
:func:`src.engine.value.evaluate_market`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from src.agent.store import fixture_key
from src.engine.value import ValueBet


@dataclass
class Pick:
    fixture_key: str
    event_id: str
    league: str  # football-data code
    league_name: str
    home_team: str
    away_team: str
    commence_time: datetime  # UTC, naive
    market: str
    outcome: str
    point: float
    bookmaker: str
    price: float
    p_model: float
    p_dc: float | None
    p_sharp: float | None
    ev: float
    stake_pct: float
    reasoning: str = ""

    @property
    def score(self) -> float:
        return self.ev * self.p_model

    @property
    def fair_odds(self) -> float:
        return 1.0 / self.p_model

    @property
    def selection_label(self) -> str:
        if self.market == "h2h":
            return {"home": "Home Win (1)", "draw": "Draw (X)", "away": "Away Win (2)"}[self.outcome]
        return f"{self.outcome.capitalize()} {self.point:g}"

    def db_row(self) -> dict:
        return {k: getattr(self, k) for k in (
            "fixture_key", "event_id", "league", "home_team", "away_team", "commence_time", "market",
            "outcome", "point", "bookmaker", "price", "p_model", "p_dc", "p_sharp", "ev", "stake_pct",
            "reasoning")}


def to_picks(bets: list[ValueBet], fixtures: pd.DataFrame, league_name: str) -> list[Pick]:
    meta = fixtures.set_index("event_id")
    out = []
    for b in bets:
        fx = meta.loc[b.event_id]
        kick = pd.Timestamp(fx["commence_time"]).to_pydatetime()
        out.append(Pick(
            fixture_key=fixture_key(fx["league"], fx["home_team"], fx["away_team"], kick),
            event_id=b.event_id, league=fx["league"], league_name=league_name,
            home_team=fx["home_team"], away_team=fx["away_team"], commence_time=kick,
            market=b.market, outcome=b.outcome, point=b.point, bookmaker=b.bookmaker, price=b.price,
            p_model=b.p_model, p_dc=b.p_dc, p_sharp=b.p_sharp, ev=b.ev, stake_pct=b.stake_pct,
        ))
    return out


def select(
    candidates: list[Pick],
    now: datetime,
    blocked: set[str],
    sent_today: int,
    max_daily: int,
    horizon_hours: int,
    min_lead_minutes: int,
) -> list[Pick]:
    remaining = max(max_daily - sent_today, 0)
    if remaining == 0:
        return []
    earliest = now + timedelta(minutes=min_lead_minutes)
    latest = now + timedelta(hours=horizon_hours)
    eligible = [c for c in candidates
                if earliest <= c.commence_time <= latest and c.fixture_key not in blocked]
    eligible.sort(key=lambda c: (c.score, c.ev), reverse=True)
    chosen: list[Pick] = []
    seen: set[str] = set()
    for c in eligible:
        if c.fixture_key in seen:
            continue
        seen.add(c.fixture_key)
        chosen.append(c)
        if len(chosen) == remaining:
            break
    return chosen
