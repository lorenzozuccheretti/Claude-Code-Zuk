"""Benchmark 1X2 classifier on rolling team-form features.

This is the yardstick for Dixon-Coles, not a replacement: if a
feature-based classifier can't beat the goal model's Brier score, the goal
model is doing its job.

Features for each match are built only from that team's *earlier* matches
(``shift(1)`` before rolling), so there is no leakage of the match being
predicted. Two estimators are available:

* ``logreg`` - multinomial logistic regression on standardised features.
* ``gbm``    - scikit-learn's ``HistGradientBoostingClassifier``, a
  dependency-free stand-in for XGBoost.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

CLASSES = ("H", "D", "A")
STATS = ("gf", "ga", "sf", "sa", "stf", "sta", "pts")


def _team_long(matches: pd.DataFrame) -> pd.DataFrame:
    """One row per team per match, from that team's point of view."""
    m = matches.reset_index(drop=True).copy()
    m["match_id"] = m.index
    for col in ("hs", "as_", "hst", "ast"):
        if col not in m.columns:
            m[col] = np.nan
    pts_home = np.select([m["fthg"] > m["ftag"], m["fthg"] == m["ftag"]], [3, 1], 0)
    home = pd.DataFrame({
        "match_id": m["match_id"], "match_date": m["match_date"], "team": m["home_team"], "is_home": 1,
        "gf": m["fthg"], "ga": m["ftag"], "sf": m["hs"], "sa": m["as_"], "stf": m["hst"], "sta": m["ast"],
        "pts": pts_home,
    })
    away = pd.DataFrame({
        "match_id": m["match_id"], "match_date": m["match_date"], "team": m["away_team"], "is_home": 0,
        "gf": m["ftag"], "ga": m["fthg"], "sf": m["as_"], "sa": m["hs"], "stf": m["ast"], "sta": m["hst"],
        "pts": np.select([m["ftag"] > m["fthg"], m["ftag"] == m["fthg"]], [3, 1], 0),
    })
    long = pd.concat([home, away], ignore_index=True)
    for col in STATS:
        long[col] = pd.to_numeric(long[col], errors="coerce").astype(float)
    return long.sort_values(["team", "match_date", "match_id"])


def build_features(matches: pd.DataFrame, window: int = 6, min_periods: int = 3) -> pd.DataFrame:
    """Pre-match rolling means for both sides, aligned with ``matches`` rows.

    Returns a frame indexed like ``matches.reset_index(drop=True)`` with
    ``home_*``, ``away_*`` and ``diff_*`` columns; rows without enough
    history are NaN.
    """
    long = _team_long(matches)
    rolled = long.groupby("team")[list(STATS)].transform(
        lambda s: s.shift(1).rolling(window, min_periods=min_periods).mean()
    )
    long[[f"r_{c}" for c in STATS]] = rolled[list(STATS)].to_numpy()
    feats = [f"r_{c}" for c in STATS]
    home = long[long["is_home"] == 1].set_index("match_id")[feats].add_prefix("home_")
    away = long[long["is_home"] == 0].set_index("match_id")[feats].add_prefix("away_")
    out = home.join(away).sort_index()
    for c in STATS:
        out[f"diff_{c}"] = out[f"home_r_{c}"] - out[f"away_r_{c}"]
    # Shots are missing in some files; drop all-NaN columns so rows survive.
    return out.dropna(axis=1, how="all")


@dataclass
class MLBenchmark:
    kind: str = "logreg"
    window: int = 6
    pipeline: Pipeline | None = None
    feature_names: list[str] | None = None

    def _make(self) -> Pipeline:
        if self.kind == "logreg":
            return make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))
        if self.kind == "gbm":
            return make_pipeline(HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                                                l2_regularization=1.0))
        raise ValueError(f"Unknown classifier {self.kind!r}; use 'logreg' or 'gbm'")

    def fit(self, matches: pd.DataFrame) -> "MLBenchmark":
        m = matches.reset_index(drop=True)
        X = build_features(m, self.window)
        mask = X.notna().all(axis=1)
        if mask.sum() < 50:
            raise ValueError(f"Only {int(mask.sum())} matches have enough history to train on")
        self.feature_names = list(X.columns)
        self.pipeline = self._make().fit(X[mask].to_numpy(), m.loc[mask, "ftr"].to_numpy())
        return self

    def predict_proba_history(self, matches: pd.DataFrame) -> pd.DataFrame:
        """P(H), P(D), P(A) for rows of ``matches`` (features from earlier rows only)."""
        if self.pipeline is None or self.feature_names is None:
            raise RuntimeError("Classifier is not fitted")
        m = matches.reset_index(drop=True)
        X = build_features(m, self.window).reindex(columns=self.feature_names)
        mask = X.notna().all(axis=1)
        out = pd.DataFrame(np.nan, index=m.index, columns=list(CLASSES))
        if mask.any():
            proba = self.pipeline.predict_proba(X[mask].to_numpy())
            order = list(self.pipeline.classes_)
            out.loc[mask, list(CLASSES)] = proba[:, [order.index(c) for c in CLASSES]]
        return out

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: Path) -> "MLBenchmark":
        return joblib.load(path)
