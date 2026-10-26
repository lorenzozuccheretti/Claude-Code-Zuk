"""Totals calibration: fitting, application, storage and use by the agent."""

import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
import respx

from src import pipeline
from src.agent import runner
from src.agent.runner import RunReport, run_daily
from src.config import LEAGUES, Settings
from src.data.database import connect
from src.models import calibration
from src.models.calibration import LINES, TotalsCalibration
from src.models.dixon_coles import DixonColes
from tests.test_agent_runner import NOW, mock_odds, settings, tg_ok  # noqa: F401 - fixture


def overconfident_sample(n=6000, a=0.1, b=0.5, seed=0):
    """Raw probabilities whose true rate is sigmoid(a + b * logit(raw))."""
    rng = np.random.default_rng(seed)
    raw = rng.uniform(0.05, 0.95, n)
    true = 1 / (1 + np.exp(-(a + b * np.log(raw / (1 - raw)))))
    return raw, (rng.uniform(size=n) < true).astype(float)


def test_fit_line_recovers_the_true_mapping():
    raw, y = overconfident_sample()
    a, b = calibration.fit_line(raw, y)
    assert a == pytest.approx(0.1, abs=0.1) and b == pytest.approx(0.5, abs=0.07)


def test_fit_needs_enough_matches_and_reports_log_loss():
    raw, y = overconfident_sample(3000)
    goals = np.where(y == 1, 4, 1)  # over every line up to 3.5 exactly when y
    preds = pd.DataFrame({"goals": goals, **{f"p_over_{line}": raw for line in LINES}})
    cal = calibration.fit(preds)
    assert cal.samples == 3000 and set(cal.coefs) == set(LINES)
    raw_loss, cal_loss = cal.log_loss[2.5]
    assert cal_loss < raw_loss
    with pytest.raises(ValueError, match="needed to calibrate"):
        calibration.fit(preds.head(500))


def test_calibrated_probabilities_shrink_totals_only(synthetic_matches):
    model = DixonColes().fit(synthetic_matches, as_of=pd.Timestamp("2025-08-01"))
    raw = model.predict(*sorted(model.params.teams)[:2])
    cal = TotalsCalibration({2.5: (0.0, 0.5), 3.5: (-0.3, 0.5)})
    wrapped = cal.wrap(raw)
    assert wrapped.h2h() == raw.h2h()
    p = raw.totals(2.5)["over"]
    expected = 1 / (1 + np.exp(-0.5 * np.log(p / (1 - p))))
    t = wrapped.totals(2.5)
    assert t["over"] == pytest.approx(expected) and t["over"] + t["under"] == pytest.approx(1.0)
    assert abs(t["over"] - 0.5) < abs(p - 0.5)  # pulled toward the middle
    assert wrapped.totals(4.5)["over"] == pytest.approx(cal.over(3.5, raw.totals(4.5)["over"]))  # nearest line
    assert wrapped.totals(3.0) == raw.totals(3.0)  # integer lines untouched
    assert wrapped.lambda_home == raw.lambda_home
    assert cal.wrap(None) is None and TotalsCalibration({}).over(2.5, 0.7) == 0.7


def test_round_trip():
    cal = TotalsCalibration({2.5: (0.1, 0.5)}, 1234, datetime(2030, 1, 1), {2.5: (0.69, 0.68)})
    assert TotalsCalibration.from_dict(cal.to_dict()) == cal


def test_walk_forward_predicts_only_after_warmup(synthetic_matches):
    preds = calibration.walk_forward_predictions(synthetic_matches, xi=0.005, window_days=1095, refit_days=60)
    first = pd.to_datetime(synthetic_matches["match_date"]).min()
    assert len(preds) > 500 and preds["match_date"].min() >= first + pd.Timedelta(days=365)
    assert (preds["p_over_1.5"] >= preds["p_over_2.5"]).all() and preds["p_over_2.5"].between(0, 1).all()


def test_calibrate_stores_and_loads(loaded_con, monkeypatch):
    monkeypatch.setattr(calibration, "MIN_SAMPLE", 500)  # one synthetic league is ~750 predictions
    s = Settings(_env_file=None)
    cal = pipeline.calibrate(loaded_con, s, [LEAGUES["serie-a"]], now=datetime(2030, 1, 1), refit_days=60)
    loaded = pipeline.load_calibration(loaded_con)
    assert loaded == cal and loaded.fitted_at == datetime(2030, 1, 1)


def test_agent_refits_only_when_stale(settings, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(pipeline, "calibrate", lambda con, s, leagues, now: calls.append(now))
    con = connect(settings.db_file)
    report = RunReport(started_at=NOW, dry_run=True)
    runner._refresh_calibration(con, settings, NOW, report)  # none stored yet
    assert len(calls) == 1 and report.calibrated
    stored = TotalsCalibration({2.5: (0.0, 0.5)}, 2000, NOW - timedelta(days=10))
    con.execute("INSERT OR REPLACE INTO model_params VALUES ('totals_calibration', 'ALL', ?, ?)",
                [NOW, json.dumps(stored.to_dict())])
    runner._refresh_calibration(con, settings, NOW, report)
    assert len(calls) == 1  # 10 days old: still fresh
    runner._refresh_calibration(con, settings, NOW + timedelta(days=25), report)
    assert len(calls) == 2
    con.close()


@respx.mock
def test_agent_prices_totals_with_the_calibration(settings):  # noqa: F811
    con = connect(settings.db_file)
    # A calibration that rates every over at 90% makes the effect unmistakable.
    stored = TotalsCalibration({line: (2.1972, 0.0) for line in LINES}, 2000, NOW)
    con.execute("INSERT OR REPLACE INTO model_params VALUES ('totals_calibration', 'ALL', ?, ?)",
                [NOW, json.dumps(stored.to_dict())])
    con.close()
    mock_odds()
    tg_ok()
    s = settings.model_copy(update={"calibrate_totals": True, "model_weight": 1.0})
    r = run_daily(s, now=NOW, refresh=False, dry_run=True)
    overs = [p for p in r.picks if p.market == "totals" and p.outcome == "over"]
    assert overs and all(p.p_dc == pytest.approx(0.9, abs=1e-3) for p in overs)
