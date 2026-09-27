r"""Dixon-Coles (1997) bivariate Poisson model with time decay.

For a match between home team *i* and away team *j*:

.. math::

    \lambda = \exp(\alpha_i + \beta_j + \gamma), \qquad \mu = \exp(\alpha_j + \beta_i)

    P(X=x, Y=y) = \tau_{\lambda,\mu}(x, y)\,\text{Pois}(x;\lambda)\,\text{Pois}(y;\mu)

where :math:`\alpha` is attack strength, :math:`\beta` is defensive weakness
(higher concedes more), :math:`\gamma` is home advantage, and the low-score
correction :math:`\tau` is

======  ==========================
(0, 0)  :math:`1 - \lambda\mu\rho`
(0, 1)  :math:`1 + \lambda\rho`
(1, 0)  :math:`1 + \mu\rho`
(1, 1)  :math:`1 - \rho`
other   1
======  ==========================

Parameters maximise the time-weighted log-likelihood
:math:`\sum_k \phi(t_k) \log P(x_k, y_k)` with
:math:`\phi(t) = e^{-\xi t}` and *t* the age of match *k* in days. The model
is identified by constraining :math:`\sum_i \alpha_i = 0`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import poisson

log = logging.getLogger(__name__)


@dataclass
class DixonColesParams:
    teams: list[str]
    attack: dict[str, float]
    defence: dict[str, float]
    home_advantage: float
    rho: float
    xi: float
    as_of: str  # ISO date the decay is measured from
    n_matches: int
    log_likelihood: float
    converged: bool
    matches_per_team: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DixonColesParams":
        return cls(**data)


@dataclass(frozen=True)
class MatchProbabilities:
    home_team: str
    away_team: str
    lambda_home: float
    mu_away: float
    matrix: np.ndarray  # P(home goals = row, away goals = col)

    @property
    def home(self) -> float:
        return float(np.tril(self.matrix, -1).sum())

    @property
    def draw(self) -> float:
        return float(np.trace(self.matrix))

    @property
    def away(self) -> float:
        return float(np.triu(self.matrix, 1).sum())

    def h2h(self) -> dict[str, float]:
        return {"home": self.home, "draw": self.draw, "away": self.away}

    def totals(self, line: float) -> dict[str, float]:
        """P(over), P(under) and P(push) for a total-goals line."""
        return total_goals_probs(self.matrix, line)


def total_goals_probs(matrix: np.ndarray, line: float) -> dict[str, float]:
    n = matrix.shape[0]
    goals = np.add.outer(np.arange(n), np.arange(n))
    return {
        "over": float(matrix[goals > line].sum()),
        "under": float(matrix[goals < line].sum()),
        "push": float(matrix[goals == line].sum()),
    }


def tau(x: np.ndarray, y: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float) -> np.ndarray:
    """Dixon-Coles low-score correction factor (vectorised)."""
    out = np.ones_like(lam, dtype=float)
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    out[m00] = 1 - lam[m00] * mu[m00] * rho
    out[m01] = 1 + lam[m01] * rho
    out[m10] = 1 + mu[m10] * rho
    out[m11] = 1 - rho
    return out


def decay_weights(dates: pd.Series, as_of: pd.Timestamp, xi: float) -> np.ndarray:
    """:math:`e^{-\\xi t}` with *t* in days before ``as_of`` (future matches weigh 0)."""
    age = (as_of - pd.to_datetime(dates)).dt.days.to_numpy(dtype=float)
    w = np.exp(-xi * np.clip(age, 0, None))
    w[age < 0] = 0.0
    return w


class DixonColes:
    """Fit and query a Dixon-Coles model for one league."""

    TAU_FLOOR = 1e-10

    def __init__(self, xi: float = 0.005, max_goals: int = 10, rho_bounds: tuple[float, float] = (-0.3, 0.3)):
        self.xi = xi
        self.max_goals = max_goals
        self.rho_bounds = rho_bounds
        self.params: DixonColesParams | None = None

    # -- fitting ------------------------------------------------------------

    def fit(
        self,
        matches: pd.DataFrame,
        as_of: date | pd.Timestamp | None = None,
        init: DixonColesParams | None = None,
    ) -> "DixonColes":
        """Estimate parameters from ``matches``.

        ``matches`` needs ``match_date, home_team, away_team, fthg, ftag``.
        Only matches strictly before ``as_of`` are used (default: the day
        after the last match), so this is safe for walk-forward backtests.
        ``init`` warm-starts the optimiser from a previous fit.
        """
        df = matches.copy()
        df["match_date"] = pd.to_datetime(df["match_date"])
        ref = pd.Timestamp(as_of) if as_of is not None else df["match_date"].max() + pd.Timedelta(days=1)
        df = df[df["match_date"] < ref]
        w = decay_weights(df["match_date"], ref, self.xi)
        keep = w > 1e-6
        df, w = df[keep], w[keep]
        if len(df) < 20:
            raise ValueError(f"Need at least 20 matches to fit Dixon-Coles, got {len(df)}")

        teams = sorted(set(df["home_team"]) | set(df["away_team"]))
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)
        hi = df["home_team"].map(idx).to_numpy()
        ai = df["away_team"].map(idx).to_numpy()
        x = df["fthg"].to_numpy(dtype=float)
        y = df["ftag"].to_numpy(dtype=float)
        w = w / w.sum()
        const = (w * (gammaln(x + 1) + gammaln(y + 1))).sum()

        def unpack(theta: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
            attack = np.append(theta[: n - 1], -theta[: n - 1].sum())
            defence = theta[n - 1 : 2 * n - 1]
            return attack, defence, theta[-2], theta[-1]

        def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
            attack, defence, gamma, rho = unpack(theta)
            log_lam = attack[hi] + defence[ai] + gamma
            log_mu = attack[ai] + defence[hi]
            lam, mu = np.exp(log_lam), np.exp(log_mu)
            t = tau(x, y, lam, mu, rho)
            t_safe = np.maximum(t, self.TAU_FLOOR)
            ll = (w * (np.log(t_safe) + x * log_lam - lam + y * log_mu - mu)).sum() - const

            # d log(tau) / d log(lambda), d log(mu), d rho
            m00 = (x == 0) & (y == 0)
            m01 = (x == 0) & (y == 1)
            m10 = (x == 1) & (y == 0)
            m11 = (x == 1) & (y == 1)
            dl = np.zeros_like(lam)
            dm = np.zeros_like(lam)
            dr = np.zeros_like(lam)
            dl[m00] = -lam[m00] * mu[m00] * rho / t_safe[m00]
            dm[m00] = dl[m00]
            dr[m00] = -lam[m00] * mu[m00] / t_safe[m00]
            dl[m01] = lam[m01] * rho / t_safe[m01]
            dr[m01] = lam[m01] / t_safe[m01]
            dm[m10] = mu[m10] * rho / t_safe[m10]
            dr[m10] = mu[m10] / t_safe[m10]
            dr[m11] = -1 / t_safe[m11]

            g_lam = w * (x - lam + dl)
            g_mu = w * (y - mu + dm)
            g_att = np.bincount(hi, g_lam, n) + np.bincount(ai, g_mu, n)
            g_def = np.bincount(ai, g_lam, n) + np.bincount(hi, g_mu, n)
            grad = np.concatenate(
                [g_att[: n - 1] - g_att[n - 1], g_def, [g_lam.sum(), (w * dr).sum()]]
            )
            return -ll, -grad

        theta0 = self._initial_theta(teams, df, w, init)
        bounds = [(-4, 4)] * (2 * n - 1) + [(-1, 1), self.rho_bounds]
        res = minimize(objective, theta0, jac=True, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 2000, "ftol": 1e-12, "gtol": 1e-8})
        if not res.success:
            log.warning("Dixon-Coles optimiser did not converge: %s", res.message)
        attack, defence, gamma, rho = unpack(res.x)
        counts = pd.concat([df["home_team"], df["away_team"]]).value_counts()
        self.params = DixonColesParams(
            teams=teams,
            attack={t: float(attack[i]) for t, i in idx.items()},
            defence={t: float(defence[i]) for t, i in idx.items()},
            home_advantage=float(gamma),
            rho=float(rho),
            xi=self.xi,
            as_of=ref.date().isoformat(),
            n_matches=int(len(df)),
            log_likelihood=float(-res.fun),
            converged=bool(res.success),
            matches_per_team={t: int(counts.get(t, 0)) for t in teams},
        )
        return self

    def _initial_theta(
        self, teams: list[str], df: pd.DataFrame, w: np.ndarray, init: DixonColesParams | None
    ) -> np.ndarray:
        """Warm start from a previous fit, else from an independent-Poisson GLM."""
        n = len(teams)
        if init is not None:
            mean_att = np.mean([init.attack.get(t, 0.0) for t in teams])
            att = np.array([init.attack.get(t, mean_att) for t in teams]) - mean_att
            dfn = np.array([init.defence.get(t, 0.0) + mean_att for t in teams])
            return np.concatenate([att[: n - 1], dfn, [init.home_advantage, init.rho]])
        try:
            att, dfn, gamma = _poisson_glm_start(teams, df, w)
        except Exception as exc:  # noqa: BLE001 - any GLM failure just means a cold start
            log.debug("GLM warm start failed (%s); starting from zeros", exc)
            att, dfn, gamma = np.zeros(n), np.zeros(n), 0.25
        return np.concatenate([att[: n - 1], dfn, [gamma, -0.05]])

    # -- persistence --------------------------------------------------------

    @classmethod
    def from_params(cls, params: DixonColesParams, max_goals: int = 10) -> "DixonColes":
        model = cls(xi=params.xi, max_goals=max_goals)
        model.params = params
        return model

    # -- prediction ---------------------------------------------------------

    def _require(self) -> DixonColesParams:
        if self.params is None:
            raise RuntimeError("Model is not fitted")
        return self.params

    def knows(self, team: str) -> bool:
        return team in self._require().attack

    def reliable(self, team: str, min_matches: int = 10) -> bool:
        """Known *and* fitted on enough matches to trust (promoted sides aren't, early on)."""
        p = self._require()
        return team in p.attack and p.matches_per_team.get(team, 0) >= min_matches

    def can_price(self, home: str, away: str, min_matches: int = 10) -> bool:
        return self.reliable(home, min_matches) and self.reliable(away, min_matches)

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        p = self._require()
        missing = [t for t in (home, away) if t not in p.attack]
        if missing:
            raise KeyError(f"Team(s) not in the fitted model: {', '.join(missing)}")
        lam = np.exp(p.attack[home] + p.defence[away] + p.home_advantage)
        mu = np.exp(p.attack[away] + p.defence[home])
        return float(lam), float(mu)

    def predict(self, home: str, away: str) -> MatchProbabilities:
        """Full score matrix (0..max_goals each side), renormalised to sum to 1."""
        p = self._require()
        lam, mu = self.expected_goals(home, away)
        goals = np.arange(self.max_goals + 1)
        matrix = np.outer(poisson.pmf(goals, lam), poisson.pmf(goals, mu))
        matrix[0, 0] *= 1 - lam * mu * p.rho
        matrix[0, 1] *= 1 + lam * p.rho
        matrix[1, 0] *= 1 + mu * p.rho
        matrix[1, 1] *= 1 - p.rho
        matrix = np.clip(matrix, 0, None)
        matrix /= matrix.sum()
        return MatchProbabilities(home, away, lam, mu, matrix)


def _poisson_glm_start(teams: list[str], df: pd.DataFrame, w: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Independent Poisson log-linear model (= Dixon-Coles with rho = 0) via statsmodels.

    Design: log E[goals] = attack[scorer] + defence[conceder] + gamma * is_home,
    with no intercept and the first defence level dropped for identifiability.
    The result is shifted to satisfy sum(attack) = 0.
    """
    import statsmodels.api as sm

    n = len(teams)
    idx = {t: i for i, t in enumerate(teams)}
    m = len(df)
    scorer = np.concatenate([df["home_team"].map(idx), df["away_team"].map(idx)])
    conceder = np.concatenate([df["away_team"].map(idx), df["home_team"].map(idx)])
    X = np.zeros((2 * m, 2 * n))
    X[np.arange(2 * m), scorer] = 1
    X[np.arange(2 * m), n + conceder] = 1
    X = np.delete(X, n, axis=1)  # drop first defence dummy
    home = np.concatenate([np.ones(m), np.zeros(m)])
    X = np.column_stack([X, home])
    yv = np.concatenate([df["fthg"].to_numpy(float), df["ftag"].to_numpy(float)])
    weights = np.concatenate([w, w]) * m  # rescale so weights are O(1)
    res = sm.GLM(yv, X, family=sm.families.Poisson(), freq_weights=weights).fit()
    coef = np.asarray(res.params)
    att = coef[:n]
    dfn = np.concatenate([[0.0], coef[n : 2 * n - 1]])
    shift = att.mean()
    return att - shift, dfn + shift, float(coef[-1])
