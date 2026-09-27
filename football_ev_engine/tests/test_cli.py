"""End-to-end: every CLI command against mocked football-data and Odds API."""

from datetime import datetime, timedelta

import httpx
import pytest
import respx
from typer.testing import CliRunner

from src.cli import app as cli
from src.cli.app import app
from src.config import get_settings
from src.data.database import connect
from tests.conftest import SEASONS
from tests.synthetic import odds_api_events, to_football_data_csv

FD = "https://www.football-data.co.uk/mmz4281"
API = "https://api.the-odds-api.com/v4"
QUOTA = {"x-requests-used": "2", "x-requests-remaining": "498", "x-requests-last": "2"}

runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.duckdb"))
    monkeypatch.setenv("MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("ALIASES_JSON", str(tmp_path / "aliases.json"))
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    monkeypatch.setattr(cli.console, "width", 250)  # keep table cells on one line
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def invoke(*args):
    result = runner.invoke(app, list(args), catch_exceptions=False)
    return result


@respx.mock
def test_full_workflow(env, synthetic_matches):
    assert invoke("init-db").exit_code == 0

    for season in SEASONS:
        rows = synthetic_matches[synthetic_matches["season"] == season]
        code = season[2:4] + season[7:9]
        respx.get(f"{FD}/{code}/I1.csv").mock(return_value=httpx.Response(200, text=to_football_data_csv(rows)))
    r = invoke("fetch-historical", "--league", "serie-a", "--seasons", ",".join(SEASONS))
    assert r.exit_code == 0, r.output
    assert "1140 matches" in r.output

    r = invoke("train", "--league", "serie-a")
    assert r.exit_code == 0, r.output
    assert "yes" in r.output
    assert (env / "models" / "ml_logreg_I1.joblib").exists()

    kickoff = datetime.utcnow().replace(microsecond=0) + timedelta(days=2)
    events = odds_api_events([("Inter Milan", "AC Milan"), ("AS Roma", "SS Lazio"), ("Atalanta BC", "Real Madrid")],
                             kickoff)
    respx.get(f"{API}/sports").mock(return_value=httpx.Response(
        200, json=[{"key": "soccer_italy_serie_a", "active": True}], headers=QUOTA))
    respx.get(f"{API}/sports/soccer_italy_serie_a/odds").mock(
        return_value=httpx.Response(200, json=events, headers=QUOTA))
    r = invoke("fetch-odds", "--league", "serie-a")
    assert r.exit_code == 0, r.output
    assert "Real Madrid" in r.output  # reported as unmatched
    assert "498 remaining" in r.output
    con = connect(env / "db.duckdb")
    mapped = dict(con.execute("SELECT home_team_raw, home_team FROM fixtures").fetchall())
    con.close()
    assert mapped == {"Inter Milan": "Inter", "AS Roma": "Roma", "Atalanta BC": "Atalanta"}

    # Loose filters so the synthetic prices produce at least one bet.
    r = invoke("predict", "--league", "serie-a", "--min-ev", "-1", "--model-weight", "1", "--show-skipped")
    assert r.exit_code == 0, r.output
    assert "+EV bets" in r.output and "Inter v Milan" in r.output
    assert "not matched" in r.output

    r = invoke("aliases", "--league", "serie-a", "--set", "Real Madrid=Napoli")
    assert r.exit_code == 0 and "Pinned" in r.output
    assert invoke("aliases", "--league", "serie-a", "--set", "Foo=Barcelona").exit_code == 1

    r = invoke("backtest", "--league", "serie-a", "--seasons", "2024-2025", "--refit-days", "28")
    assert r.exit_code == 0, r.output
    for label in ("Yield", "ROI", "Max drawdown", "Brier", "Pinnacle (de-vigged)", "ML benchmark"):
        assert label in r.output, label


def test_predict_before_train_fails_cleanly(env):
    invoke("init-db")
    r = invoke("predict", "--league", "serie-a")
    assert r.exit_code == 1 and "run train first" in r.output


def test_unknown_league(env):
    r = invoke("predict", "--league", "eredivisie")
    assert r.exit_code == 1 and "Unknown league" in r.output


def test_fetch_odds_without_key(env, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "")
    get_settings.cache_clear()
    r = invoke("fetch-odds")
    assert r.exit_code == 1 and "ODDS_API_KEY" in r.output
