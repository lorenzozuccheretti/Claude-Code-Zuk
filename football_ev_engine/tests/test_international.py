from datetime import date

import httpx
import numpy as np
import pytest
import respx

from src.config import Settings, get_league
from src.data.ingest_international import parse_results
from src.models.dixon_coles import DixonColes
from src import pipeline

URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
CSV = """date,home_team,away_team,home_score,away_score,tournament,city,country,neutral
2021-06-01,Italy,Wales,1,0,Friendly,Rome,Italy,FALSE
2024-06-15,Spain,Croatia,3,0,UEFA Euro,Berlin,Germany,TRUE
2024-09-05,France,Italy,1,3,UEFA Nations League,Paris,France,FALSE
2024-10-01,Ellan Vannin,Sapmi,2,2,CONIFA World Football Cup,Douglas,Isle of Man,FALSE
2026-11-12,Italy,Norway,NA,NA,FIFA World Cup qualification,Milan,Italy,FALSE
"""


def test_parse_keeps_played_recent_fifa_matches():
    df = parse_results(CSV, since=date(2022, 1, 1))
    assert list(df["home_team"]) == ["Spain", "France"]  # old, CONIFA and unplayed rows dropped
    assert list(df["neutral"]) == [True, False]
    assert list(df["ftr"]) == ["H", "A"] and set(df["league"]) == {"INT"}
    assert list(df["season"]) == ["2024", "2024"]


def test_parse_rejects_wrong_file():
    with pytest.raises(ValueError):
        parse_results("a,b\n1,2\n", since=date(2020, 1, 1))


@respx.mock
def test_fetch_historical_routes_by_source(con):
    respx.get(URL).mock(return_value=httpx.Response(200, text=CSV))
    s = Settings(_env_file=None)
    results = pipeline.fetch_historical(con, s, [get_league("nations-league")], ["2022-2023"])
    assert results[0].rows == 2 and results[0].error is None
    assert con.execute("SELECT count(*) FROM matches WHERE league = 'INT' AND neutral").fetchone()[0] == 1


@respx.mock
def test_download_error_is_reported(con):
    respx.get(URL).mock(return_value=httpx.Response(500))
    r = pipeline.fetch_historical(con, Settings(_env_file=None), [get_league("nations-league")], ["2022-2023"])
    assert r[0].rows == 0 and r[0].error


def test_neutral_venue_has_no_home_advantage(synthetic_matches):
    m = DixonColes(xi=0.0).fit(synthetic_matches)
    lam_home, mu_home = m.expected_goals("Inter", "Milan")
    lam_neutral, mu_neutral = m.expected_goals("Inter", "Milan", neutral=True)
    assert lam_neutral == pytest.approx(lam_home / np.exp(m.params.home_advantage)) and mu_neutral == mu_home


def test_neutral_false_matches_fit_like_club_data(synthetic_matches):
    """An all-False neutral column must give exactly the fit of data without the column."""
    base = DixonColes(xi=0.0).fit(synthetic_matches).params
    same = DixonColes(xi=0.0).fit(synthetic_matches.assign(neutral=False)).params
    assert same.home_advantage == pytest.approx(base.home_advantage, abs=1e-6)


def test_league_overrides_decay_and_window():
    lg = get_league("nations-league")
    assert lg.source == "international" and lg.decay_xi < 0.005 and lg.window_days > 1095
    assert get_league("nations").slug == "nations-league"
