import numpy as np
import pytest

from src.engine.devig import devig, implied_probabilities, margin


def test_margin_and_multiplicative_formula():
    odds = [2.0, 3.4, 4.0]
    implied = 1 / np.array(odds)
    m = implied.sum() - 1
    assert margin(odds) == pytest.approx(m)
    fair = devig(odds)
    np.testing.assert_allclose(fair, implied / (1 + m))
    assert fair.sum() == pytest.approx(1.0)


def test_fair_odds_are_unchanged():
    fair = devig([2.0, 4.0, 4.0])
    np.testing.assert_allclose(fair, [0.5, 0.25, 0.25])


def test_power_method_sums_to_one_and_shades_longshots():
    odds = [1.30, 5.50, 11.0]
    mult, power = devig(odds, "multiplicative"), devig(odds, "power")
    assert power.sum() == pytest.approx(1.0)
    # Power takes more margin off the longshot than multiplicative does.
    assert power[2] < mult[2]
    assert power[0] > mult[0]


@pytest.mark.parametrize("bad", [[1.0, 2.0], [2.0], [0.5, 3.0], [float("nan"), 2.0]])
def test_invalid_odds(bad):
    with pytest.raises(ValueError):
        implied_probabilities(bad)


def test_unknown_method():
    with pytest.raises(ValueError):
        devig([2, 2], "shin")
