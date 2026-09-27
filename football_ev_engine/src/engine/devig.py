r"""Remove the bookmaker margin (the "vig") from a set of odds.

Multiplicative (the spec's method):

.. math::

    P_{implied,i} = 1 / O_i, \quad M = \sum_i P_{implied,i} - 1, \quad
    P_{fair,i} = P_{implied,i} / (1 + M)

Power: find *k* with :math:`\sum_i P_{implied,i}^{k} = 1`. It moves more of
the margin onto longshots, which matches the favourite-longshot bias better.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.optimize import brentq


def implied_probabilities(odds: Sequence[float]) -> np.ndarray:
    arr = np.asarray(odds, dtype=float)
    if arr.ndim != 1 or len(arr) < 2:
        raise ValueError("Need at least two outcomes to de-vig")
    if np.any(~np.isfinite(arr)) or np.any(arr <= 1.0):
        raise ValueError(f"Decimal odds must be finite and > 1, got {list(arr)}")
    return 1.0 / arr


def margin(odds: Sequence[float]) -> float:
    """Overround :math:`M` (0.05 = 5% margin)."""
    return float(implied_probabilities(odds).sum() - 1.0)


def devig_multiplicative(odds: Sequence[float]) -> np.ndarray:
    implied = implied_probabilities(odds)
    return implied / (1.0 + (implied.sum() - 1.0))


def devig_power(odds: Sequence[float]) -> np.ndarray:
    implied = implied_probabilities(odds)
    total = implied.sum()
    if np.isclose(total, 1.0):
        return implied / total
    # sum(p^k) is monotone decreasing in k; bracket the root generously.
    f = lambda k: float((implied**k).sum() - 1.0)  # noqa: E731
    k = brentq(f, 0.2, 5.0)
    fair = implied**k
    return fair / fair.sum()


def devig(odds: Sequence[float], method: str = "multiplicative") -> np.ndarray:
    """Fair probabilities for a complete, mutually exclusive set of outcomes."""
    if method == "multiplicative":
        return devig_multiplicative(odds)
    if method == "power":
        return devig_power(odds)
    raise ValueError(f"Unknown de-vig method {method!r}")
