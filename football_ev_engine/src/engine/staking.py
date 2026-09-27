r"""Fractional Kelly staking.

.. math::

    f^* = \text{fraction} \times \frac{p\,b - q}{b}, \qquad b = O - 1,\; q = 1 - p

The default fraction is 0.25 (quarter Kelly) and the result is capped at
2.5% of bankroll. Negative-edge bets get a stake of zero.
"""

from __future__ import annotations

import math


def kelly_fraction(p: float, odds: float) -> float:
    """Full-Kelly fraction of bankroll (may be negative when there is no edge)."""
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"Probability must be in [0, 1], got {p}")
    if odds <= 1.0:
        raise ValueError(f"Decimal odds must be > 1, got {odds}")
    b = odds - 1.0
    return (p * b - (1.0 - p)) / b


def stake_fraction(p: float, odds: float, fraction: float = 0.25, cap: float = 0.025) -> float:
    """Fractional-Kelly stake as a share of bankroll, in ``[0, cap]``."""
    return min(max(fraction * kelly_fraction(p, odds), 0.0), cap)


def stake_amount(
    p: float, odds: float, bankroll: float, fraction: float = 0.25, cap: float = 0.025, round_to: float = 0.01
) -> float:
    """Stake in currency units, rounded down to ``round_to``."""
    raw = stake_fraction(p, odds, fraction, cap) * bankroll
    if round_to <= 0:
        return raw
    # The epsilon stops float noise turning 25.00 into 24.99.
    return round(math.floor(raw / round_to + 1e-9) * round_to, 10)
