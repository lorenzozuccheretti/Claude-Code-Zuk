import json

import numpy as np
import pandas as pd
import pytest

from src.models.dixon_coles import DixonColes, DixonColesParams, decay_weights, tau, total_goals_probs
from tests.synthetic import TEAMS, true_params


def test_tau_cells():
    x = np.array([0, 0, 1, 1, 2])
    y = np.array([0, 1, 0, 1, 3])
    lam, mu, rho = np.full(5, 1.5), np.full(5, 1.1), -0.1
    np.testing.assert_allclose(tau(x, y, lam, mu, rho), [1 + 1.5 * 1.1 * 0.1, 1 - 0.15, 1 - 0.11, 1.1, 1.0])


def test_decay_weights():
    dates = pd.Series(pd.to_datetime(["2024-01-01", "2024-04-10", "2024-05-01"]))
    w = decay_weights(dates, pd.Timestamp("2024-04-10"), 0.005)
    assert w[0] == pytest.approx(np.exp(-0.005 * 100))
    assert w[1] == pytest.approx(1.0)
    assert w[2] == 0.0  # after the reference date


@pytest.fixture(scope="module")
def fitted(synthetic_matches):
    return DixonColes(xi=0.0).fit(synthetic_matches)


def test_recovers_true_parameters(fitted):
    tp = true_params(TEAMS, 7)
    p = fitted.params
    assert p.converged
    est_att = [p.attack[t] for t in TEAMS]
    est_def = [p.defence[t] for t in TEAMS]
    assert np.corrcoef(est_att, [tp["attack"][t] for t in TEAMS])[0, 1] > 0.85
    assert np.corrcoef(est_def, [tp["defence"][t] for t in TEAMS])[0, 1] > 0.75
    assert abs(p.home_advantage - tp["gamma"]) < 0.08
    assert abs(p.rho - tp["rho"]) < 0.1
    assert sum(p.attack.values()) == pytest.approx(0.0, abs=1e-8)


def test_probability_matrix(fitted):
    pr = fitted.predict("Inter", "Venezia")
    assert pr.matrix.sum() == pytest.approx(1.0)
    assert sum(pr.h2h().values()) == pytest.approx(1.0)
    t = pr.totals(2.5)
    assert t["over"] + t["under"] == pytest.approx(1.0) and t["push"] == 0.0
    # The 0-0 cell carries the tau correction.
    lam, mu, rho = pr.lambda_home, pr.mu_away, fitted.params.rho
    assert (lam, mu) == pytest.approx(fitted.expected_goals("Inter", "Venezia"))
    assert rho != 0


def test_integer_line_has_push():
    m = np.full((3, 3), 1 / 9)
    assert total_goals_probs(m, 2.0)["push"] == pytest.approx(3 / 9)


def test_home_advantage_is_positive(fitted):
    home = fitted.predict("Roma", "Lazio").home
    away_version = fitted.predict("Lazio", "Roma").away
    assert home > away_version


def test_unknown_team(fitted):
    assert not fitted.knows("Barcelona")
    with pytest.raises(KeyError):
        fitted.predict("Barcelona", "Inter")


def test_as_of_excludes_later_matches(synthetic_matches):
    cutoff = pd.Timestamp("2023-06-01")
    m = DixonColes().fit(synthetic_matches, as_of=cutoff)
    assert m.params.n_matches == int((pd.to_datetime(synthetic_matches["match_date"]) < cutoff).sum())
    # Changing results after the cutoff cannot change the fit.
    altered = synthetic_matches.copy()
    later = pd.to_datetime(altered["match_date"]) >= cutoff
    altered.loc[later, "fthg"] = 9
    m2 = DixonColes().fit(altered, as_of=cutoff)
    assert m2.params.attack == pytest.approx(m.params.attack)


def test_warm_start_matches_cold_start(synthetic_matches, fitted):
    warm = DixonColes(xi=0.0).fit(synthetic_matches, init=fitted.params)
    for t in TEAMS:
        assert warm.params.attack[t] == pytest.approx(fitted.params.attack[t], abs=1e-3)


def test_params_roundtrip_json(fitted):
    restored = DixonColesParams.from_dict(json.loads(json.dumps(fitted.params.to_dict())))
    m = DixonColes.from_params(restored)
    np.testing.assert_allclose(m.predict("Inter", "Milan").matrix, fitted.predict("Inter", "Milan").matrix)


def test_too_few_matches(synthetic_matches):
    with pytest.raises(ValueError):
        DixonColes().fit(synthetic_matches.head(10))


def test_glm_warm_start_is_used(synthetic_matches, monkeypatch):
    """A failing warm start falls back silently, so check it actually runs."""
    import src.models.dixon_coles as dc

    calls = []
    real = dc._poisson_glm_start
    monkeypatch.setattr(dc, "_poisson_glm_start", lambda *a: calls.append(1) or real(*a))
    monkeypatch.setattr(dc.log, "debug", lambda *a: (_ for _ in ()).throw(AssertionError(a)))
    DixonColes().fit(synthetic_matches)
    assert calls == [1]


def test_recovers_from_a_bad_warm_start(synthetic_matches, monkeypatch, fitted):
    """A wild GLM start (as rank-deficient national-team data can produce) must not strand the fit."""
    import src.models.dixon_coles as dc

    n = len(TEAMS)
    monkeypatch.setattr(dc, "_poisson_glm_start",
                        lambda *a: (np.full(n, 50.0), np.full(n, -50.0), 5.0))
    m = DixonColes(xi=0.0).fit(synthetic_matches)
    assert m.params.converged
    assert m.params.log_likelihood == pytest.approx(fitted.params.log_likelihood, abs=1e-6)
