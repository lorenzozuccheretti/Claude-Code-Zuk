r"""Calibration of the Dixon-Coles totals probabilities.

Walk-forward, Dixon-Coles is overconfident on goal totals: when it rates
Over 3.5 above 55% the over lands about 51% of the time, and Under 2.5
rated above 65% lands about 59%. Its ratings spread the matches too far
apart. A logistic recalibration per line corrects this:

.. math::

    \operatorname{logit} P'_{over} = a_{line} + b_{line} \cdot \operatorname{logit} P_{over}

The coefficients are fitted on out-of-sample predictions only: the model is
refitted every ``refit_days`` on the history before each block and predicts
the block, exactly as it would have live. They are pooled across
competitions, because one league's ~1,000 matches give noisy fits. On the
2025-26 season, with coefficients fitted on 2023-25, log loss improved on
every line (Over 2.5: 0.6925 -> 0.6777) and the calibrated averages matched
the observed rates in every probability band.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from src.models.dixon_coles import DixonColes, MatchProbabilities

log = logging.getLogger(__name__)

LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
MIN_SAMPLE = 1000  # out-of-sample predictions needed before calibrating
_EPS = 1e-4


def _logit(p: np.ndarray | float) -> np.ndarray | float:
    p = np.clip(p, _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


def _sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-x))


@dataclass(frozen=True)
class TotalsCalibration:
    coefs: dict[float, tuple[float, float]]  # line -> (a, b)
    samples: int = 0
    fitted_at: datetime | None = None
    log_loss: dict[float, tuple[float, float]] = field(default_factory=dict)  # line -> (raw, calibrated)

    def over(self, line: float, p_over: float) -> float:
        """Calibrated P(over); lines without a fit use the nearest fitted line."""
        if not self.coefs or line % 1 != 0.5:
            return p_over
        nearest = min(self.coefs, key=lambda x: abs(x - line))
        a, b = self.coefs[nearest]
        return float(_sigmoid(a + b * _logit(p_over)))

    def wrap(self, probs: MatchProbabilities | None) -> "CalibratedProbabilities | None":
        return None if probs is None else CalibratedProbabilities(probs, self)

    def to_dict(self) -> dict:
        return {"coefs": {str(k): list(v) for k, v in self.coefs.items()}, "samples": self.samples,
                "fitted_at": self.fitted_at.isoformat() if self.fitted_at else None,
                "log_loss": {str(k): list(v) for k, v in self.log_loss.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "TotalsCalibration":
        return cls(coefs={float(k): (float(v[0]), float(v[1])) for k, v in d["coefs"].items()},
                   samples=int(d.get("samples", 0)),
                   fitted_at=datetime.fromisoformat(d["fitted_at"]) if d.get("fitted_at") else None,
                   log_loss={float(k): (float(v[0]), float(v[1])) for k, v in d.get("log_loss", {}).items()})


class CalibratedProbabilities:
    """A :class:`MatchProbabilities` whose half-goal totals are recalibrated (1X2 untouched)."""

    def __init__(self, raw: MatchProbabilities, calibration: TotalsCalibration):
        self.raw = raw
        self.calibration = calibration

    def h2h(self) -> dict[str, float]:
        return self.raw.h2h()

    def totals(self, line: float) -> dict[str, float]:
        t = self.raw.totals(line)
        if line % 1 != 0.5:
            return t
        over = self.calibration.over(line, t["over"])
        return {"over": over, "under": 1.0 - over, "push": 0.0}

    def __getattr__(self, name: str):
        return getattr(self.raw, name)


def _log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, _EPS, 1 - _EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def fit_line(p_over: np.ndarray, went_over: np.ndarray) -> tuple[float, float]:
    x = _logit(np.asarray(p_over, dtype=float))
    y = np.asarray(went_over, dtype=float)
    res = minimize(lambda t: _log_loss(_sigmoid(t[0] + t[1] * x), y), np.array([0.0, 1.0]), method="Nelder-Mead")
    return float(res.x[0]), float(res.x[1])


def fit(predictions: pd.DataFrame, fitted_at: datetime | None = None) -> TotalsCalibration:
    """Fit from out-of-sample predictions: columns ``p_over_<line>`` and ``goals``."""
    if len(predictions) < MIN_SAMPLE:
        raise ValueError(f"Only {len(predictions)} out-of-sample predictions; {MIN_SAMPLE} needed to calibrate")
    coefs, losses = {}, {}
    for line in LINES:
        p = predictions[f"p_over_{line}"].to_numpy(dtype=float)
        y = (predictions["goals"].to_numpy() > line).astype(float)
        a, b = fit_line(p, y)
        coefs[line] = (a, b)
        losses[line] = (_log_loss(p, y), _log_loss(_sigmoid(a + b * _logit(p)), y))
    return TotalsCalibration(coefs, len(predictions), fitted_at, losses)


def walk_forward_predictions(matches: pd.DataFrame, xi: float, window_days: int, refit_days: int = 14,
                             min_team_matches: int = 10, warmup_days: int = 365,
                             max_goals: int = 10) -> pd.DataFrame:
    """Out-of-sample totals predictions for one competition's ``matches``.

    The first ``warmup_days`` of history only train; after that the model is
    refitted every ``refit_days`` and predicts the following block.
    """
    df = matches.copy()
    df["match_date"] = pd.to_datetime(df["match_date"])
    df = df.sort_values("match_date")
    if df.empty:
        return pd.DataFrame()
    start = df["match_date"].min() + pd.Timedelta(days=warmup_days)
    end = df["match_date"].max()
    model = DixonColes(xi=xi, max_goals=max_goals)
    params = None
    rows = []
    block_start = start
    while block_start <= end:
        block_end = block_start + pd.Timedelta(days=refit_days)
        block = df[(df["match_date"] >= block_start) & (df["match_date"] < block_end)]
        if not block.empty:
            train = df[(df["match_date"] < block_start)
                       & (df["match_date"] >= block_start - pd.Timedelta(days=window_days))]
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    model.fit(train, as_of=block_start, init=params)
                params = model.params
            except ValueError as exc:
                log.warning("Calibration: skipping block %s: %s", block_start.date(), exc)
                block_start = block_end
                continue
            for r in block.itertuples(index=False):
                if not model.can_price(r.home_team, r.away_team, min_team_matches):
                    continue
                neutral = bool(getattr(r, "neutral", False) or False)
                probs = model.predict(r.home_team, r.away_team, neutral=neutral)
                rows.append({"match_date": r.match_date, "goals": int(r.fthg + r.ftag),
                             **{f"p_over_{line}": probs.totals(line)["over"] for line in LINES}})
        block_start = block_end
    return pd.DataFrame(rows)
