import pytest

from src.engine.staking import kelly_fraction, stake_amount, stake_fraction


def test_kelly_formula():
    # p = 0.5, odds 2.2 -> b = 1.2, f = (0.6 - 0.5) / 1.2
    assert kelly_fraction(0.5, 2.2) == pytest.approx(0.1 / 1.2)


def test_quarter_kelly_below_cap():
    p, odds = 0.40, 2.6  # full Kelly = (0.64 - 0.6)/1.6 = 0.025
    assert stake_fraction(p, odds) == pytest.approx(0.25 * 0.025)


def test_cap_at_2_5_percent():
    assert stake_fraction(0.7, 2.5) == 0.025


def test_no_edge_no_stake():
    assert stake_fraction(0.3, 3.0) == 0.0
    assert stake_amount(0.3, 3.0, 1000) == 0.0


def test_stake_amount_rounds_down_to_cents():
    assert stake_amount(0.7, 2.5, 1000) == 25.0
    assert stake_amount(0.40, 2.6, 1234.567) == pytest.approx(7.71)


@pytest.mark.parametrize("p,odds", [(-0.1, 2.0), (1.1, 2.0), (0.5, 1.0)])
def test_invalid_inputs(p, odds):
    with pytest.raises(ValueError):
        kelly_fraction(p, odds)
