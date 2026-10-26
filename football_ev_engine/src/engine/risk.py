"""Risk overrides on top of the value rules ("kill-switches").

The value rules (:class:`~src.engine.value.ValueRules`) apply one EV floor and
one probability to every market. These overrides tighten them where the
model is known to be weakest. Each is tunable from ``.env`` and switched off
by setting its parameter to 0 (or an empty list).

1. **Extreme totals lines.** Over 3.5+ and Under 1.5- sit in the tail of the
   Poisson goal distribution, where small rating errors move the probability
   most. They must clear ``EXTREME_LINE_MIN_EV`` (12%) instead of ``MIN_EV``.
2. **Sandbox leagues.** Picks from these leagues are paper-traded by the
   agent: recorded and settled, never sent. Applied in
   :mod:`src.agent.runner`; :meth:`RiskRules.is_sandboxed` decides.
3. **Big-match Under 2.5.** When both sides rank in their league's top
   ``BIG_MATCH_TOP_N`` attacks, ``BIG_MATCH_UNDER_PENALTY`` comes off P_model
   for the under before EV and stake are computed.

Rules 2 and 3 are off by default. Neither held up on history: Ligue 1 was
not an outlier in the backtest, and rule 3 fired on 8 of 562 bets. The
totals calibration (:mod:`src.models.calibration`) corrects the model's
overconfidence on unders across all matches instead.

Overrides only ever make the engine stricter: an EV floor is raised, never
lowered, and the probability shift lowers the stake together with the EV, so
Kelly sizing stays consistent and a rejected bet never reaches staking.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from src.config import LEAGUES, Settings


@dataclass(frozen=True)
class MarketRisk:
    """Per-outcome overrides for one market of one fixture."""

    min_ev: Mapping[str, float] = field(default_factory=dict)      # outcome -> EV floor
    prob_shift: Mapping[str, float] = field(default_factory=dict)  # outcome -> added to P_model

    def floor(self, outcome: str, default: float) -> float:
        return max(default, self.min_ev.get(outcome, default))

    def adjust(self, outcome: str, p: float) -> float:
        return min(max(p + self.prob_shift.get(outcome, 0.0), 0.0), 1.0)


NO_RISK = MarketRisk()


@dataclass(frozen=True)
class RiskRules:
    extreme_over_from: float = 3.5
    extreme_under_to: float = 1.5
    extreme_line_min_ev: float = 0.12
    big_match_under_line: float = 2.5
    big_match_under_penalty: float = 0.0
    big_match_top_n: int = 7
    sandbox_leagues: frozenset[str] = frozenset()  # football-data codes

    @classmethod
    def from_settings(cls, s: Settings) -> "RiskRules":
        return cls(
            extreme_over_from=s.extreme_over_from, extreme_under_to=s.extreme_under_to,
            extreme_line_min_ev=s.extreme_line_min_ev, big_match_under_line=s.big_match_under_line,
            big_match_under_penalty=s.big_match_under_penalty, big_match_top_n=s.big_match_top_n,
            sandbox_leagues=league_codes(s.sandbox_leagues),
        )

    def is_sandboxed(self, league: str) -> bool:
        return league in self.sandbox_leagues

    def is_extreme(self, outcome: str, point: float) -> bool:
        return ((outcome == "over" and point >= self.extreme_over_from)
                or (outcome == "under" and point <= self.extreme_under_to))

    def is_big_match(self, home: str, away: str, top_attack: set[str]) -> bool:
        return self.big_match_top_n > 0 and home in top_attack and away in top_attack

    def market_risk(self, market: str, point: float, home: str, away: str,
                    top_attack: set[str] = frozenset()) -> MarketRisk:
        if market != "totals":
            return NO_RISK
        min_ev = {}
        if self.extreme_line_min_ev > 0:
            min_ev = {o: self.extreme_line_min_ev for o in ("over", "under") if self.is_extreme(o, point)}
        shift = {}
        if (self.big_match_under_penalty > 0 and point == self.big_match_under_line
                and self.is_big_match(home, away, top_attack)):
            shift["under"] = -self.big_match_under_penalty
        return MarketRisk(min_ev, shift) if min_ev or shift else NO_RISK


def league_codes(names: Iterable[str]) -> frozenset[str]:
    """Slugs (``ligue-1``) or football-data codes (``F1``) -> codes."""
    by_slug = {slug: lg.fd_code for slug, lg in LEAGUES.items()}
    codes = {lg.fd_code for lg in LEAGUES.values()}
    out = set()
    for name in names:
        key = name.strip()
        if not key:
            continue
        if key.lower() in by_slug:
            out.add(by_slug[key.lower()])
        elif key.upper() in codes:
            out.add(key.upper())
        else:
            raise ValueError(f"Unknown league in SANDBOX_LEAGUES: {name!r}")
    return frozenset(out)


def top_attack(attack: Mapping[str, float], n: int, pool: Iterable[str] | None = None) -> set[str]:
    """The ``n`` strongest attacks among ``pool`` (default: every rated team)."""
    if n <= 0:
        return set()
    teams = [t for t in (pool if pool is not None else attack) if t in attack] or list(attack)
    return set(sorted(teams, key=lambda t: -attack[t])[:n])
